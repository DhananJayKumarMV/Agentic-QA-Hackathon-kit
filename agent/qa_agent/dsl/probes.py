"""Probes: collect raw facts for each oracle (CONVENTIONS §4). The Verifier judges them.

Every probe returns a JSON-able dict. Exceptions become ``{"error": "..."}`` so the oracle is
reported as ``error`` (scenario unverified), never as a product failure.
"""
from __future__ import annotations

import asyncio
import fnmatch
import re
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlparse

from ..contracts import NetworkEvent, Oracle, SiteMap, StepResult, Target
from .params import obj_id, substitute, tx_account_ids

if TYPE_CHECKING:
    from .actions import ExecContext

# ---------------------------------------------------------------------------------------------
# Page-side scripts. Each carries a /*qa:<name>*/ tag (used by the test fake to dispatch).
# ---------------------------------------------------------------------------------------------

JS_MAIN_TEXT = "() => /*qa:main_text*/ ((document.querySelector('main') || document.body).innerText || '')"
JS_BODY_TEXT = "() => /*qa:body_text*/ ((document.body && document.body.innerText) || '')"
JS_VIEWPORT = "() => /*qa:viewport*/ ({width: window.innerWidth, height: window.innerHeight})"
JS_OVERFLOW = ("() => /*qa:overflow*/ ({scroll_width: document.documentElement.scrollWidth, "
               "client_width: document.documentElement.clientWidth})")

JS_ROWS = r"""(arg) => { /*qa:rows*/
  const root = document.querySelector('main') || document.body;
  const visible = (el) => !!(el.offsetParent !== null || el.getClientRects().length);
  let rows = [];
  if (arg && arg.selector) { try { rows = Array.from(document.querySelectorAll(arg.selector)); } catch (e) { rows = []; } }
  if (!rows.length) {
    rows = Array.from(root.querySelectorAll('tbody tr, [role=row]'))
      .filter((r) => !r.querySelector('th[scope=col], [role=columnheader]'));
  }
  rows = rows.filter(visible);
  const idOf = (el) => {
    for (const at of Array.from(el.attributes)) {
      const n = at.name.toLowerCase();
      if (/^data-.*id$/.test(n) && !/test-?id$/.test(n) && at.value) return at.value;
    }
    return null;
  };
  return rows.map((r) => {
    let id = idOf(r);
    if (!id) { for (const d of Array.from(r.querySelectorAll('*'))) { id = idOf(d); if (id) break; } }
    const table = r.closest('table, [role=table], [role=grid]');
    const headers = table ? Array.from(table.querySelectorAll('thead th, [role=columnheader]')).map((h) => h.innerText.trim()) : [];
    let cellEls = Array.from(r.children).filter((c) => /^(TD|TH)$/.test(c.tagName) || /cell/.test(c.getAttribute('role') || ''));
    if (!cellEls.length) cellEls = Array.from(r.querySelectorAll('[data-label]'));
    const cells = {};
    cellEls.forEach((c, i) => {
      const label = c.getAttribute('data-label') || headers[i] || ('col' + i);
      cells[label] = (c.innerText || '').trim();
    });
    const t = r.querySelector('time[datetime]');
    return { id, datetime: t ? t.getAttribute('datetime') : null, cells, text: (r.innerText || '').trim() };
  });
}"""

JS_ALERTS = r"""() => { /*qa:alerts*/
  const sel = '[role=alert],[role=status],[aria-live],.alert,.toast,.notification,.notice,.banner,[class*=success],[class*=error],[class*=empty]';
  const out = [];
  for (const el of Array.from(document.querySelectorAll(sel))) {
    if (!(el.offsetParent !== null || el.getClientRects().length)) continue;
    const text = (el.innerText || '').trim();
    if (!text) continue;
    const t = el.querySelector('h1,h2,h3,h4,strong,[class*=title]');
    out.push({ role: el.getAttribute('role') || '', live: el.getAttribute('aria-live') || '',
               classes: el.className && el.className.baseVal !== undefined ? el.className.baseVal : (el.className || ''),
               title: t ? t.innerText.trim() : (el.getAttribute('title') || ''), text });
  }
  return out;
}"""

JS_UI_BALANCES = r"""(arg) => { /*qa:ui_balances*/
  // Balance capture uses ONLY balance-keyed elements (class / test id / aria-label / data-label /
  // id containing "balance", or the sitemap's balance selectors). Table rows are never read, so a
  // transaction amount can never be mistaken for a balance.
  const root = document.querySelector('main') || document.body;
  const money = /[−-]?\s?[$€£]?\s?[−-]?\d[\d,]*\.\d{2}(?!\d)/;
  const visible = (el) => !!(el.offsetParent !== null || el.getClientRects().length);
  const hintOf = (d) => (((d.className && d.className.baseVal === undefined) ? d.className : '') + ' ' +
      (d.id || '') + ' ' + (d.getAttribute('aria-label') || '') + ' ' + (d.getAttribute('data-label') || '') + ' ' +
      (d.getAttribute('data-testid') || '') + ' ' + (d.getAttribute('data-test') || '')).toLowerCase();
  const inRow = (el) => !!el.closest('tr, [role=row], tbody, table, [role=table], [role=grid]');
  let els = [];
  for (const sel of (arg.selectors || [])) {
    try { els = els.concat(Array.from(document.querySelectorAll(sel))); } catch (e) { /* bad selector */ }
  }
  for (const d of Array.from(root.querySelectorAll('*'))) {
    if (hintOf(d).includes('balance')) els.push(d);
  }
  els = Array.from(new Set(els)).filter((e) => visible(e) && !inRow(e) && money.test(e.innerText || ''));
  // Keep the innermost: drop an element that contains another candidate.
  els = els.filter((e) => !els.some((o) => o !== e && e.contains(o)));
  const labelsIn = (text) => arg.accounts.filter((a) => a.labels.some((l) => l && text.includes(l))).map((a) => a.id);
  const urlAccounts = arg.accounts.filter((a) => location.href.includes(a.id)).map((a) => a.id);
  const out = {};
  const ambiguous = [];
  for (const el of els) {
    let owner = null;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ids = labelsIn(n.innerText || '');
      if (ids.length === 1) { owner = ids[0]; break; }
      if (ids.length > 1) break;  // reached a container shared by several accounts
    }
    if (!owner && urlAccounts.length === 1) owner = urlAccounts[0];  // detail page of one account
    const text = (el.innerText || '').trim();
    if (!owner) { ambiguous.push(text); continue; }
    if (out[owner] && out[owner].text !== text) { out[owner].conflict = (out[owner].conflict || []).concat([text]); continue; }
    let card = el;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      if (labelsIn(n.innerText || '').length === 1) card = n; else break;
    }
    out[owner] = { text, card_text: (card.innerText || '').trim().slice(0, 400) };
  }
  return { balances: out, ambiguous };
}"""

DEFAULT_BUTTON_DELAY_MS = 2000

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def find_ref(sitemap: SiteMap, key: str):
    for page in sitemap.pages.values():
        if key in page.elements:
            return page.elements[key]
    return None


def ref_selector(ref) -> str | None:
    if ref is None:
        return None
    if ref.css:
        return ref.css
    if ref.test_id:
        return f'[data-testid="{ref.test_id}"]'
    return None


def _route_matches(template: str, path: str) -> bool:
    pat = re.sub(r"(:[A-Za-z_]\w*|\{[^}]+\})", "[^/]+", template.rstrip("/") or "/")
    return re.fullmatch(pat, path.rstrip("/") or "/") is not None


def row_key(sitemap: SiteMap, current_path: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    best: str | None = None
    for route, page in sitemap.pages.items():
        for k, ref in page.elements.items():
            if k.endswith(".row") or (ref.multiple and "row" in k.split(".")[-1]):
                if _route_matches(route, current_path):
                    return k
                best = best or k
    return best


def url_matches_glob(url: str, glob: str) -> bool:
    path = urlparse(url).path or url
    if not any(c in glob for c in "*?["):
        return glob in url
    return fnmatch.fnmatch(url, glob) or fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.lstrip("*"))


def matching_events(network: list[NetworkEvent], method: str | None, glob: str) -> list[NetworkEvent]:
    return [e for e in network
            if (not method or e.method.upper() == method.upper()) and url_matches_glob(e.url, glob)]


def _path_query(url: str) -> str:
    p = urlparse(url)
    if not p.scheme:
        return url
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def _account_id_for(ref: Any, ctx: "ExecContext") -> str:
    """Account param name / resolved value -> account id from the live list."""
    s = str(ref)
    val = ctx.params.get(s, s)
    for a in ctx.state.accounts:
        if obj_id(a) == val:
            return val
    for a in ctx.state.accounts:
        if val in (str(a.get("name")), str(a.get("masked_number"))):
            return obj_id(a) or val
    return val


def _target(args: dict, ctx: "ExecContext") -> Target:
    t = args.get("target")
    if t is None:
        raise ValueError("oracle needs a target")
    if isinstance(t, str):
        return Target(key=t)
    return Target(**t)


async def _target_texts(t: Target, ctx: "ExecContext") -> list[str]:
    el, _, _ = await ctx.browser.resolve(t)
    n = await el.count()
    if n > 1 and t.nth is None:
        return [x.strip() for x in await el.all_texts()]
    return [(await el.text()).strip()]


async def capture_rows(ctx: "ExecContext", args: dict) -> list[dict]:
    path = _path_query(await ctx.browser.current_url()).split("?")[0]
    t = args.get("target")
    key = row_key(ctx.sitemap, path, (t.get("key") if isinstance(t, dict) else t) if t else None)
    sel = ref_selector(find_ref(ctx.sitemap, key)) if key else None
    rows = await ctx.browser.evaluate(JS_ROWS, {"selector": sel})
    return list(rows or [])


async def _alerts(ctx: "ExecContext") -> list[dict]:
    return list(await ctx.browser.evaluate(JS_ALERTS) or [])


def _resolve_filters(filters: dict, ctx: "ExecContext") -> dict:
    out = dict(filters or {})
    if out.get("account"):
        out["account"] = _account_id_for(out["account"], ctx)
    return out


# ---------------------------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------------------------


async def _p_url(args, ctx):
    url = await ctx.browser.current_url()
    return {"url": _path_query(url), "full_url": url}


async def _p_visible(args, ctx):
    el, desc, _ = await ctx.browser.resolve(_target(args, ctx))
    return {"count": await el.count(), "visible": await el.is_visible(), "locator": desc}


async def _p_absent(args, ctx):
    try:
        el, desc, _ = await ctx.browser.resolve(_target(args, ctx))
        return {"count": await el.count(), "visible": await el.is_visible(), "locator": desc}
    except Exception as e:  # noqa: BLE001 - not resolvable == absent
        return {"count": 0, "visible": False, "resolve_error": str(e)[:300]}


async def _p_text(args, ctx):
    if args.get("target"):
        return {"text": "\n".join(await _target_texts(_target(args, ctx), ctx))}
    return {"text": await ctx.browser.evaluate(JS_MAIN_TEXT) or ""}


async def _p_money(args, ctx):
    return {"texts": await _target_texts(_target(args, ctx), ctx)}


async def _p_masked(args, ctx):
    full: list[str] = []
    for a in ctx.state.accounts:
        if obj_id(a):
            full.append(obj_id(a))  # type: ignore[arg-type]
        for k, v in a.items():
            kl = k.lower()
            if isinstance(v, str) and ("number" in kl or "iban" in kl or kl in ("account_no", "acct_no")) \
                    and not re.search(r"[•*xX•]", v) and len(re.sub(r"\D", "", v)) >= 6:
                full.append(v)
    return {"visible_text": await ctx.browser.evaluate(JS_BODY_TEXT) or "",
            "url": await ctx.browser.current_url(), "full_ids": sorted(set(full))}


async def _p_balance_delta(args, ctx):
    deltas = {_account_id_for(k, ctx): str(v) for k, v in (args.get("deltas") or {}).items()}
    return {"deltas": deltas}


async def _p_snapshots(args, ctx):
    from ..state.ledger import new_tx_ids

    run = ctx.run
    return {"new_tx_ids": new_tx_ids(run.state_before, run.state_after),
            "foreign_tx_ids": ctx.state.foreign_tx_ids(run) if run.state_after else []}


async def _p_tx_present(args, ctx):
    from ..state.ledger import new_tx_ids, posted_tx_ids

    run = ctx.run
    tx_id = next(iter(posted_tx_ids(run)), None)
    if tx_id is None:
        new = new_tx_ids(run.state_before, run.state_after)
        tx_id = new[0] if len(new) == 1 else None
    if tx_id is None:
        return {"error": "no transaction id from a successful POST or a single new snapshot transaction"}
    accounts = [_account_id_for(a, ctx) for a in (args.get("accounts") or [])]
    api_present: dict[str, bool] = {}
    qp = ctx.state.account_query_param()
    for aid in accounts:
        if qp:
            txs = await ctx.state.fetch_transactions({qp: aid})
            api_present[aid] = any(obj_id(t) == tx_id for t in txs)
        else:
            txs = await ctx.state.fetch_transactions()
            api_present[aid] = any(obj_id(t) == tx_id and aid in tx_account_ids(t) for t in txs)
    ui_present: dict[str, bool] = {}
    detail = next(((r, p) for r, p in ctx.sitemap.pages.items()
                   if re.search(r":[A-Za-z_]\w*|\{[^}]+\}", r) and "account" in r.lower()
                   and any(k.endswith(".row") or "row" in k.split(".")[-1] for k in p.elements)), None)
    if detail:
        back_to = _path_query(await ctx.browser.current_url())
        try:
            for aid in accounts:
                await ctx.browser.goto(re.sub(r":[A-Za-z_]\w*|\{[^}]+\}", aid, detail[0], count=1))
                await ctx.browser.settle()
                rows = await capture_rows(ctx, {})
                ui_present[aid] = any(r.get("id") == tx_id for r in rows)
        except Exception:  # noqa: BLE001 - fall back to API presence
            ui_present = {}
        finally:
            try:
                await ctx.browser.goto(back_to)
                await ctx.browser.settle()
            except Exception:  # noqa: BLE001
                pass
    present = ui_present if ui_present and len(ui_present) == len(accounts) else api_present
    return {"tx_id": tx_id, "present": present, "api_present": api_present, "ui_present": ui_present}


async def _p_rows_filters(args, ctx):
    rows = await capture_rows(ctx, args)
    all_tx = await ctx.state.fetch_transactions()
    by_id = {obj_id(t): t for t in all_tx}
    for r in rows:
        r["account_ids"] = tx_account_ids(by_id[r["id"]]) if r.get("id") in by_id else []
    return {"rows": rows, "all_tx": all_tx, "filters": _resolve_filters(args.get("filters") or {}, ctx),
            "timezone": ctx.state.timezone}


async def _p_rows_api_set(args, ctx):
    rows = await capture_rows(ctx, args)
    all_tx = await ctx.state.fetch_transactions()
    return {"displayed_ids": [r.get("id") for r in rows], "all_tx": all_tx,
            "rows": [{"id": r.get("id"), "cells": r.get("cells") or {}} for r in rows],
            "filters": _resolve_filters(args.get("filters") or {}, ctx), "timezone": ctx.state.timezone}


_LISTY = re.compile(r"(row|item|card|entry|tile|result)s?$")
_NOT_LISTY = re.compile(r"(alert|status|link|nav|button|balance|amount|heading|menu)")


def list_key_for_page(sitemap: SiteMap, current_path: str) -> str | None:
    """The repeated item element of the current page (rows, cards, items), if unambiguous."""
    for route, page in sitemap.pages.items():
        if not _route_matches(route, current_path):
            continue
        cands = [k for k, ref in page.elements.items()
                 if ref.multiple and _LISTY.search(k.split(".")[-1]) and not _NOT_LISTY.search(k.split(".")[-1])]
        rows = [k for k in cands if "row" in k.split(".")[-1]]
        if rows:
            return rows[0]
        if cands:
            return cands[0]
    return None


async def _p_row_count(args, ctx):
    t = args.get("target")
    if t:
        target = _target(args, ctx)
        try:
            el, desc, _ = await ctx.browser.resolve(target)
        except Exception as e:  # noqa: BLE001 - nothing matches == zero items, but say so
            return {"count": 0, "locator": None, "resolve_error": str(e)[:300]}
        return {"count": await el.count(), "locator": desc}
    path = _path_query(await ctx.browser.current_url()).split("?")[0]
    rows = await capture_rows(ctx, args)
    if rows:
        return {"count": len(rows), "locator": "table rows"}
    key = list_key_for_page(ctx.sitemap, path)
    if key and not key.endswith(".row"):
        try:
            el, desc, _ = await ctx.browser.resolve(Target(key=key))
            return {"count": await el.count(), "locator": desc, "key": key}
        except Exception as e:  # noqa: BLE001
            return {"count": 0, "locator": None, "key": key, "resolve_error": str(e)[:300]}
    return {"count": 0, "locator": "table rows"}


async def _p_empty(args, ctx):
    rows = await capture_rows(ctx, args)
    alerts = await _alerts(ctx)
    status = [a["text"] for a in alerts
              if a.get("role") == "status" or a.get("live") == "polite" or "empty" in str(a.get("classes", "")).lower()]
    errs = [a["text"] for a in alerts if a.get("role") == "alert" or a.get("live") == "assertive"]
    return {"row_count": len(rows), "status_texts": status, "alert_texts": errs}


def balances_route(sitemap: SiteMap) -> str | None:
    """A route without path parameters whose page lists several balances."""
    best = None
    for route, page in sitemap.pages.items():
        if re.search(r":[A-Za-z_]\w*|\{[^}]+\}", route):
            continue
        keys = [k for k, ref in page.elements.items() if "balance" in k.split(".")[-1]]
        if any(page.elements[k].multiple for k in keys):
            return route
        if keys and best is None:
            best = route
    return best


async def _p_ui_api(args, ctx):
    accounts = await ctx.state.fetch_accounts()
    spec = [{"id": obj_id(a), "labels": [str(a[k]) for k in ("name", "masked_number", "nickname") if a.get(k)]}
            for a in accounts if obj_id(a)]
    selectors = []
    t = args.get("target")
    keys = [t.get("key") if isinstance(t, dict) else t] if t else []
    keys += [k for p in ctx.sitemap.pages.values() for k in p.elements if "balance" in k.split(".")[-1]]
    for k in keys:
        if k and "balance" in k:
            sel = ref_selector(find_ref(ctx.sitemap, k))
            if sel and sel not in selectors:
                selectors.append(sel)
    arg = {"accounts": spec, "selectors": selectors}
    found = await ctx.browser.evaluate(JS_UI_BALANCES, arg) or {}
    bal = found.get("balances", found) if isinstance(found, dict) else {}
    page_used = None
    if not bal:
        # The current page shows no balances: read them where the site lists them (a static route
        # with balance elements), then come back.
        route = balances_route(ctx.sitemap)
        if route:
            back_to = _path_query(await ctx.browser.current_url())
            try:
                await ctx.browser.goto(route)
                await ctx.browser.settle()
                found = await ctx.browser.evaluate(JS_UI_BALANCES, arg) or {}
                bal = found.get("balances", found) if isinstance(found, dict) else {}
                page_used = route
            finally:
                try:
                    await ctx.browser.goto(back_to)
                    await ctx.browser.settle()
                except Exception:  # noqa: BLE001
                    pass
    return {"page": page_used or "current","ui_balances": {k: v.get("text") for k, v in bal.items() if isinstance(v, dict)},
            "card_texts": {k: v.get("card_text") for k, v in bal.items() if isinstance(v, dict)},
            "conflicts": {k: v["conflict"] for k, v in bal.items() if isinstance(v, dict) and v.get("conflict")},
            "ambiguous": found.get("ambiguous", []) if isinstance(found, dict) else [],
            "api_accounts": accounts}


async def _p_network(args, ctx):
    method = args.get("method")
    glob = args.get("path_glob") or "*"
    ev = matching_events(ctx.run.network, method, glob)
    return {"events": [{"method": e.method, "url": e.url, "status": e.status} for e in ev]}


async def _p_no_success(args, ctx):
    return {"alerts": await _alerts(ctx)}


JS_MARK = r"""(arg) => { /*qa:mark_target*/
  const cands = Array.from(document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]'));
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const el = cands.find((e) => norm(e.innerText || e.value) === norm(arg.text)) ||
             cands.find((e) => norm(e.innerText || e.value).includes(norm(arg.text)));
  if (!el) return false;
  document.querySelectorAll('[data-qa-probe]').forEach((e) => e.removeAttribute('data-qa-probe'));
  el.setAttribute('data-qa-probe', '1');
  return true;
}"""

JS_SAMPLE = r"""() => { /*qa:probe_sample*/
  const el = document.querySelector('[data-qa-probe]');
  if (!el) return null;
  const busy = el.closest('[aria-busy="true"]');
  return { disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
           aria_busy: busy ? 'true' : el.getAttribute('aria-busy'),
           aria_disabled: el.getAttribute('aria-disabled'),
           text: (el.innerText || el.value || '').trim() };
}"""

JS_CLICK_MARKED = "() => { /*qa:probe_click*/ const el = document.querySelector('[data-qa-probe]'); if (el) el.click(); return !!el; }"


async def _sample(el) -> dict:
    out: dict[str, Any] = {}
    for name, fn in (("disabled", el.is_disabled), ("aria_busy", lambda: el.attribute("aria-busy")),
                     ("aria_disabled", lambda: el.attribute("aria-disabled")), ("text", el.text)):
        try:
            out[name] = await asyncio.wait_for(fn(), timeout=0.5)
        except Exception:  # noqa: BLE001
            out[name] = None
    return out


async def _p_button(args, ctx):
    """Hold matching requests, click, click again at once, and watch the control while pending.

    The control is tagged in the DOM before clicking because its accessible name may change
    while busy ("Send" -> "Sending..."), which would break a name-based locator.
    """
    b = ctx.browser
    glob = args.get("request_glob") or "*"
    method = args.get("method", "POST")
    delay = int(args.get("delay_ms") or DEFAULT_BUTTON_DELAY_MS)
    target = _target(args, ctx)
    # Hold every request of this method, not only the glob: if the planner's glob names the wrong
    # endpoint we can still observe the control while the click's real request is pending.
    await b.delay_requests(method, "**/*", delay)
    before_log = len(b.network_log())
    el, desc, _ = await b.resolve(target)
    initial = await _sample(el)
    marked = False
    if initial.get("text"):
        try:
            marked = bool(await b.evaluate(JS_MARK, {"text": initial["text"]}))
        except Exception:  # noqa: BLE001
            marked = False

    async def sample() -> dict:
        if marked:
            try:
                s = await b.evaluate(JS_SAMPLE)
                if s is not None:
                    return s
            except Exception:  # noqa: BLE001
                pass
        return await _sample(el)

    t0 = time.monotonic()
    await el.click()
    samples = [await sample()]
    if marked:
        try:
            await b.evaluate(JS_CLICK_MARKED)  # native click: ignored by a disabled button, like a user's
            second_click = "dispatched"
        except Exception as e:  # noqa: BLE001
            second_click = f"failed: {type(e).__name__}"
    else:
        try:  # a disabled button makes this wait; that's the point, so cap it
            await asyncio.wait_for(el.click(), timeout=0.5)
            second_click = "accepted"
        except Exception as e:  # noqa: BLE001
            second_click = f"blocked: {type(e).__name__}"
    samples.append(await sample())
    while time.monotonic() - t0 < delay / 1000 * 0.8:
        samples.append(await sample())
        await asyncio.sleep(0.1)
    try:
        await b.settle()
    except Exception:  # noqa: BLE001
        pass
    clear = getattr(b, "clear_delays", None)  # optional extra on the port
    if clear is not None:
        try:
            await clear()
        except Exception:  # noqa: BLE001
            pass
    new_events = b.network_log()[before_log:]
    matching = len(matching_events(new_events, method, glob))
    request_source = "request_glob"
    if matching == 0:
        same_method = matching_events(new_events, method, "*")
        if same_method:  # the glob matched nothing; count what the click actually sent
            matching = len(same_method)
            request_source = f"any {method.upper()} (request_glob matched none: " + \
                ", ".join(sorted({urlparse(e.url).path for e in same_method})) + ")"
    disabled = any(s.get("disabled") or str(s.get("aria_busy")).lower() == "true"
                   or str(s.get("aria_disabled")).lower() == "true" for s in samples)
    busy_texts = [s["text"] for s in samples if s.get("text") and s.get("text") != initial.get("text")]
    return {"disabled_while_pending": disabled, "busy_text": busy_texts[0] if busy_texts else None,
            "matching_requests": matching, "request_source": request_source,
            "second_click": second_click, "locator": desc,
            "dom_tagged": marked, "initial": initial, "samples": samples[:20]}


async def _p_overflow(args, ctx):
    b = ctx.browser
    orig = await b.evaluate(JS_VIEWPORT) or {}
    h = int(orig.get("height") or 800)
    out: dict[str, Any] = {}
    try:
        for w in args.get("widths") or [375]:
            await b.set_viewport(int(w), h)
            try:
                await b.settle()
            except Exception:  # noqa: BLE001
                pass
            out[str(w)] = await b.evaluate(JS_OVERFLOW)
    finally:
        if orig.get("width"):
            await b.set_viewport(int(orig["width"]), h)
    return {"by_width": out}


JUDGE_TEXT_CHARS = 4000


async def _p_llm(args, ctx):
    """Alerts/status messages first, then the relevant region's text (target, else main content)."""
    alerts = await _alerts(ctx)
    region = ""
    if args.get("target"):
        try:
            region = "\n".join(await _target_texts(_target(args, ctx), ctx))
        except Exception:  # noqa: BLE001 - fall back to the main content
            region = ""
    if not region:
        region = await ctx.browser.evaluate(JS_MAIN_TEXT) or ""
    parts, seen = [], set()
    for a in alerts:
        t = str(a.get("text") or "").strip()
        if t and t not in seen and t not in region:
            seen.add(t)
            parts.append(t)
    head = "\n".join(parts)
    msg = (head + "\n\n" if head else "") + str(region).strip()
    msg = msg[:JUDGE_TEXT_CHARS]
    shot = None
    if "screenshot" in (args.get("inputs") or ["screenshot"]):
        name = ctx.store.name("judge") if ctx.store else f"{ctx.scenario.id}__judge"
        shot = (await ctx.browser.screenshot(name)).path
    return {"visible_message": msg, "screenshot": shot}


PROBES = {
    "url_matches": _p_url,
    "element_visible": _p_visible,
    "element_absent": _p_absent,
    "text_matches": _p_text,
    "text_not_matches": _p_text,
    "money_format": _p_money,
    "masked_identifier": _p_masked,
    "balance_delta": _p_balance_delta,
    "no_state_change": _p_snapshots,
    "new_tx_count": _p_snapshots,
    "tx_present_in_histories": _p_tx_present,
    "rows_satisfy_filters": _p_rows_filters,
    "rows_equal_api_set": _p_rows_api_set,
    "row_count_equals": _p_row_count,
    "empty_state_shown": _p_empty,
    "ui_matches_api": _p_ui_api,
    "api_status": _p_network,
    "api_error_code_present": _p_network,
    "no_success_ui": _p_no_success,
    "button_disabled_during": _p_button,
    "no_horizontal_overflow": _p_overflow,
    "llm_judge": _p_llm,
}

# Probes that change page or server state; the executor re-snapshots / restores around them.
MUTATING_PROBES = {"button_disabled_during"}
# Probes that judge the end state (snapshots / network), so they run after any mutating probe.
END_STATE_PROBES = {"balance_delta", "no_state_change", "new_tx_count", "api_status", "api_error_code_present"}
# Probes that navigate or resize: run after the ones that read the page as the steps left it.
DISRUPTIVE_PROBES = {"tx_present_in_histories", "no_horizontal_overflow"}
# Page-reading probes that may navigate away and back: last among the page readers, but before
# any mutating probe changes server state.
MAY_NAVIGATE_PROBES = {"ui_matches_api"}


PRECOMPUTED = "precomputed_probes"


def _target_key(t: Any) -> str | None:
    if isinstance(t, dict):
        return t.get("key")
    if isinstance(t, Target):
        return t.key
    return t if isinstance(t, str) else None


def button_watch_plan(scenario: Any, params: dict[str, str]) -> dict[int, list[int]]:
    """Map step index -> button_disabled_during oracle indices observed during that step.

    When the scenario's own steps click/submit the watched control, the pending-state check must
    happen during that click (with the form as the steps filled it); clicking again afterwards
    hits a reset or already-submitted form and sends nothing.
    """
    out: dict[int, list[int]] = {}
    for oi, o in enumerate(scenario.oracles):
        if o.type != "button_disabled_during":
            continue
        key = _target_key(substitute(o.args or {}, params).get("target"))
        if not key:
            continue
        hit = None
        for si, st in enumerate(scenario.steps):
            if st.action in ("click", "submit") and st.target is not None and \
                    substitute(st.target.key, params) == key:
                hit = si
        if hit is not None:
            out.setdefault(hit, []).append(oi)
    return out


async def watched_click(step: Any, idx: int, ctx: "ExecContext", oracle_idxs: list[int]) -> StepResult | None:
    """Perform a click step while observing the control (button_disabled_during probe).

    Returns None when the observation could not run, so the caller runs the step normally.
    """
    t0 = time.monotonic()
    o = ctx.scenario.oracles[oracle_idxs[0]]
    args = substitute(o.args or {}, ctx.params)
    try:
        out = await _p_button(args, ctx)
    except Exception as e:  # noqa: BLE001
        ctx.extras.setdefault("button_watch_errors", []).append(f"{type(e).__name__}: {e}"[:300])
        clear = getattr(ctx.browser, "clear_delays", None)
        if clear is not None:
            try:
                await clear()
            except Exception:  # noqa: BLE001
                pass
        return None
    out["observed_during_step"] = idx
    store = ctx.extras.setdefault(PRECOMPUTED, {})
    for oi in oracle_idxs:
        store[str(oi)] = dict(out)
    return StepResult(step_idx=idx, ok=True, locator_used=out.get("locator"), healed=False,
                      duration_ms=int((time.monotonic() - t0) * 1000))


async def run_probe(oracle: Oracle, ctx: "ExecContext", index: int | None = None) -> dict:
    if index is not None and str(index) in (ctx.extras.get(PRECOMPUTED) or {}):
        return ctx.extras[PRECOMPUTED][str(index)]
    fn = PROBES.get(oracle.type)
    if fn is None:
        return {"error": f"no probe for oracle type {oracle.type}"}
    try:
        args = substitute(oracle.args or {}, ctx.params)
        return await fn(args, ctx)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:500]}


def _phase(t: str) -> float:
    if t in MAY_NAVIGATE_PROBES:
        return 0.5
    if t in MUTATING_PROBES:
        return 1
    if t in END_STATE_PROBES or t in DISRUPTIVE_PROBES:
        return 2
    return 0


async def run_probes(oracles: list[Oracle], ctx: "ExecContext",
                     after_mutation: Callable[[], Awaitable[None]] | None = None) -> dict[str, dict]:
    """Run one probe per oracle; results keyed by stringified oracle index.

    Order: page-reading probes, then mutating probes (then ``after_mutation`` refreshes network
    and snapshots), then end-state and navigating probes.
    """
    out: dict[str, dict] = {}
    for phase in (0, 0.5, 1, 2):
        idx = [i for i, o in enumerate(oracles) if _phase(o.type) == phase]
        for i in idx:
            out[str(i)] = await run_probe(oracles[i], ctx, i)
        if phase == 1 and idx and after_mutation is not None:
            await after_mutation()
    return {str(i): out[str(i)] for i in range(len(oracles))}
