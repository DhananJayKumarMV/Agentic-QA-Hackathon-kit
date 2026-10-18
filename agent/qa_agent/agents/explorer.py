"""Explorer: crawl the live site (read-only) and build the SiteMap.

Black-box and generic: the entry action, pages, elements, API endpoints and live
entities are all discovered from the DOM, the accessibility tree and network traffic.
Forms are never submitted, except GET-only filter forms while a mutation guard aborts
every POST/PUT/PATCH/DELETE (when the browser supports the guard).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import deque
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from ..config import FAST_MODEL
from ..contracts import ApiEndpoint, ElementRef, NetworkEvent, PageModel, SiteMap

log = logging.getLogger(__name__)

PROMPT_VERSION = "explore-v1"
MAX_PAGES = 12

ENTRY_RX = re.compile(
    r"\b(enter|start|demo|sign ?in|log ?in|get started|continue|try( it)?|open|launch|explore|begin)\b", re.I
)
LOGOUT_RX = re.compile(r"\b(log ?out|sign ?out|exit|end session)\b", re.I)
FILTER_RX = re.compile(r"\b(filter|search|apply|find|refine)\b", re.I)
CLEAR_RX = re.compile(r"\b(clear|reset)\b", re.I)
CUSTOMER_RX = re.compile(r"customer|user|signed.in|profile.name|account.holder|whoami", re.I)
ACCESS_RX = re.compile(r"\b(sign in|log in|login|session (has )?(ended|expired)|enter the demo|access (is )?required)\b", re.I)
ID_SEG = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+|[0-9a-f]{12,}|(?=.*\d)[A-Za-z0-9_-]{10,})$",
    re.I,
)
ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")

TZ_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:[A-Z][a-z]+(?:_[A-Z][a-z]+)*)/(?:[A-Z][A-Za-z_]+)\b"), "__iana__"),
    (re.compile(r"\bpacific (standard |daylight )?time\b", re.I), "America/Los_Angeles"),
    (re.compile(r"\beastern (standard |daylight )?time\b", re.I), "America/New_York"),
    (re.compile(r"\bcentral (standard |daylight )?time\b", re.I), "America/Chicago"),
    (re.compile(r"\bmountain (standard |daylight )?time\b", re.I), "America/Denver"),
    (re.compile(r"\balaska (standard |daylight )?time\b", re.I), "America/Anchorage"),
    (re.compile(r"\bhawaii(-aleutian)? (standard )?time\b", re.I), "Pacific/Honolulu"),
    (re.compile(r"\bcentral european (summer )?time\b", re.I), "Europe/Berlin"),
    (re.compile(r"\b(greenwich mean time|british summer time|uk time)\b", re.I), "Europe/London"),
    (re.compile(r"\bindia(n)? standard time\b", re.I), "Asia/Kolkata"),
    (re.compile(r"\bjapan standard time\b", re.I), "Asia/Tokyo"),
    (re.compile(r"\b(UTC|coordinated universal time|GMT)\b"), "UTC"),
]
TZ_ABBR = {
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "EST": "America/New_York",
    "EDT": "America/New_York", "CST": "America/Chicago", "CDT": "America/Chicago",
    "MST": "America/Denver", "MDT": "America/Denver", "UTC": "UTC", "GMT": "UTC",
}


# ---------------------------------------------------------------- LLM output models
class Rename(BaseModel):
    from_key: str
    to_key: str


class PageLabels(BaseModel):
    page: str
    title: str
    purpose: str
    renames: list[Rename] = []
    forms: dict[str, list[str]] = {}


class EntryPick(BaseModel):
    index: int
    reason: str = ""


# ---------------------------------------------------------------- in-page extraction
EXTRACT_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => !!el && (el.checkVisibility ? el.checkVisibility() : !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const region = (el) => {
    if (el.closest('nav, [role=navigation]')) return el.closest('header, [role=banner]') ? 'nav' : (el.closest('main') ? 'main_nav' : 'nav');
    if (el.closest('header, [role=banner]')) return 'header';
    if (el.closest('footer, [role=contentinfo]')) return 'footer';
    return 'main';
  };
  const forms = Array.from(document.querySelectorAll('form, [role=form], [role=search]'));
  const formIdx = (el) => { const f = el.closest('form, [role=form], [role=search]'); return f ? forms.indexOf(f) : -1; };
  const byIds = (ids) => norm((ids || '').split(/\s+/).map(id => { const e = id && document.getElementById(id); return e ? e.innerText : ''; }).join(' '));
  const labelOf = (el) => {
    if (el.getAttribute('aria-label')) return norm(el.getAttribute('aria-label'));
    if (el.getAttribute('aria-labelledby')) return byIds(el.getAttribute('aria-labelledby'));
    if (el.labels && el.labels.length) return norm(Array.from(el.labels).map(l => l.innerText).join(' '));
    return norm(el.getAttribute('placeholder') || el.getAttribute('title') || '');
  };
  const nameOf = (el) => norm(el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') ? byIds(el.getAttribute('aria-labelledby')) : '') || el.innerText || el.value || el.getAttribute('title') || '');
  const testid = (el) => { const t = el.closest('[data-testid]'); return t === el ? el.getAttribute('data-testid') : null; };

  const links = Array.from(document.querySelectorAll('a[href]')).filter(visible).map(a => ({
    href: a.getAttribute('href'), abs: a.href, name: nameOf(a), region: region(a), testid: testid(a),
  }));
  const buttons = Array.from(document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')).filter(visible).map(b => ({
    name: nameOf(b), type: (b.getAttribute('type') || (b.tagName === 'BUTTON' ? 'submit' : 'button')).toLowerCase(),
    region: region(b), form: formIdx(b), testid: testid(b), disabled: !!b.disabled, id: b.id || null,
  }));
  const fields = Array.from(document.querySelectorAll('input, select, textarea')).filter(el => {
    const t = (el.getAttribute('type') || '').toLowerCase();
    return !['hidden', 'submit', 'button', 'reset', 'image'].includes(t) && visible(el);
  }).map(el => {
    const t = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : (el.getAttribute('type') || 'text').toLowerCase();
    return {
      tag: el.tagName.toLowerCase(), type: t, id: el.id || null, name: el.getAttribute('name'), label: labelOf(el),
      region: region(el), form: formIdx(el), testid: el.getAttribute('data-testid'), required: !!el.required,
      hint: byIds(el.getAttribute('aria-describedby')), placeholder: el.getAttribute('placeholder'),
      inputmode: el.getAttribute('inputmode'),
      options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => ({value: o.value, label: norm(o.label || o.textContent)})) : null,
    };
  });
  const tidCounts = {};
  for (const el of document.querySelectorAll('[data-testid]')) {
    if (!visible(el)) continue;
    const k = el.getAttribute('data-testid');
    const r = tidCounts[k] || (tidCounts[k] = {testid: k, count: 0, tag: el.tagName.toLowerCase(), region: region(el), sample: norm(el.innerText).slice(0, 80), role: el.getAttribute('role')});
    r.count += 1;
  }
  const tables = Array.from(document.querySelectorAll('table')).filter(visible).map(t => {
    const body = t.tBodies.length ? Array.from(t.tBodies).flatMap(b => Array.from(b.rows)) : Array.from(t.rows).slice(1);
    return {
      name: norm(t.getAttribute('aria-label') || (t.caption ? t.caption.innerText : '')), testid: t.getAttribute('data-testid'),
      headers: Array.from(t.querySelectorAll('thead th, tr:first-child th')).map(th => norm(th.innerText)),
      rows: body.length, row_testid: body.length && body[0].getAttribute('data-testid'),
    };
  });
  const formInfo = forms.map((f, i) => ({
    idx: i, name: norm(f.getAttribute('aria-label') || (f.getAttribute('aria-labelledby') ? byIds(f.getAttribute('aria-labelledby')) : '')),
    method: (f.getAttribute('method') || 'get').toLowerCase(), id: f.id || null, testid: f.getAttribute('data-testid'),
    notes: Array.from(f.querySelectorAll('p, small, .hint, .help')).filter(visible).map(p => norm(p.innerText)).filter(t => t && t.length < 200),
  }));
  const statuses = Array.from(document.querySelectorAll('[role=status], [role=alert], [aria-live]')).filter(visible).map(s => ({role: s.getAttribute('role') || 'live', text: norm(s.innerText).slice(0, 160)}));
  const h1 = document.querySelector('h1');
  return {
    url: location.href, path: location.pathname, doc_title: document.title, h1: h1 ? norm(h1.innerText) : '',
    links, buttons, fields, testids: Object.values(tidCounts), tables, forms: formInfo, statuses,
    has_main: !!document.querySelector('main'),
    text: norm(document.body ? document.body.innerText : '').slice(0, 4000),
  };
}
"""


# ---------------------------------------------------------------- small helpers
def slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:max_len].rstrip("_") or "item"


def singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def template_path(path: str) -> str:
    segs = [s for s in path.split("/") if s]
    return "/" + "/".join("{id}" if ID_SEG.match(s) else s for s in segs)


def page_prefix(route: str, landing: bool = False) -> str:
    if landing:
        return "landing"
    segs = [s for s in route.split("/") if s]
    if not segs:
        return "home"
    if segs[-1] == "{id}":
        base = [s for s in segs if s != "{id}"]
        return slug(singular(base[-1]) if base else "detail") + "_detail"
    return slug(segs[-1])


def is_dynamic_name(name: str) -> bool:
    """Names that embed data (money, long digit runs, masked numbers) make poor locators."""
    return bool(re.search(r"\d[\d,]*\.\d{2}|\d{3,}|•|\*{2,}|@", name)) or len(name) > 48


def dom_hash(raw: dict[str, Any]) -> str:
    skeleton = {
        "links": sorted({(l["region"], l["name"] if not is_dynamic_name(l["name"]) else "#") for l in raw["links"]}),
        "buttons": sorted({(b["region"], b["name"]) for b in raw["buttons"]}),
        "fields": sorted({(f["type"], f["label"]) for f in raw["fields"]}),
        "testids": sorted({t["testid"] for t in raw["testids"]}),
    }
    return hashlib.sha256(json.dumps(skeleton, sort_keys=True, default=str).encode()).hexdigest()[:16]


def field_thing(label: str, ftype: str, in_filter: bool) -> str:
    l = label.lower()
    if in_filter:
        if re.search(r"\b(start|from|begin)\b.*\bdate\b|\bdate from\b", l):
            return "filter_start_date"
        if re.search(r"\b(end|to|until)\b.*\bdate\b|\bdate to\b", l):
            return "filter_end_date"
        return "filter_" + slug(re.sub(r"\b(filter|by)\b", "", l))
    if re.match(r"^\s*from\b|\bsource\b|\bdebit\b", l):
        return "source"
    if re.match(r"^\s*to\b|\bdestination\b|\brecipient\b|\bcredit\b", l):
        return "destination"
    if re.search(r"\bamount\b", l):
        return "amount"
    if re.search(r"\b(memo|note|description|reference)\b", l):
        return "memo"
    return slug(re.sub(r"\(.*?\)", "", l)) if label else slug(ftype)


def css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- heuristic labeler
class Candidate(BaseModel):
    key: str
    ref: ElementRef
    kind: str
    region: str
    desc: str


def heuristic_candidates(raw: dict[str, Any], prefix: str, *, entry_name: str | None = None) -> tuple[list[Candidate], dict[str, list[str]], dict[str, list[dict[str, str]]]]:
    cands: list[Candidate] = []
    used: set[str] = set()
    forms: dict[str, list[str]] = {}
    options: dict[str, list[dict[str, str]]] = {}

    def add(key: str, ref_kw: dict[str, Any], kind: str, region: str, desc: str) -> str | None:
        base, n = key, 2
        while key in used:
            key = f"{base}_{n}"
            n += 1
        used.add(key)
        cands.append(Candidate(key=key, ref=ElementRef(key=key, **ref_kw), kind=kind, region=region, desc=desc))
        return key

    filter_forms = set()
    for f in raw["forms"]:
        btn_names = " ".join(b["name"] for b in raw["buttons"] if b["form"] == f["idx"])
        if FILTER_RX.search(f["name"] or "") or (FILTER_RX.search(btn_names) and f["method"] == "get"
                                                   and not any(b["type"] == "submit" and not FILTER_RX.search(b["name"])
                                                               for b in raw["buttons"] if b["form"] == f["idx"])):
            filter_forms.add(f["idx"])

    def form_key(idx: int) -> str:
        f = raw["forms"][idx]
        if idx in filter_forms:
            return "filters"
        return slug(f["name"]) if f["name"] else prefix

    # Links
    for l in raw["links"]:
        name, href = l["name"], l["href"] or ""
        if not name or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        p = urlparse(l["abs"])
        if l["region"] in ("nav",):
            add(f"nav.{slug(name)}", {"role": "link", "name": name}, "link", "nav", f'link "{name}" -> {href}')
        elif l["region"] == "header":
            add(f"header.{slug(name)}", {"role": "link", "name": name, "test_id": l["testid"]}, "link", "header", f'link "{name}" -> {href}')
        elif l["region"] in ("main", "main_nav"):
            tmpl = template_path(p.path)
            if l["testid"]:
                continue  # covered by the testid pass
            if "{id}" in tmpl:
                head = tmpl.split("{id}")[0]
                ent = singular(slug([s for s in head.split("/") if s][-1])) if [s for s in head.split("/") if s] else "item"
                key = f"{prefix}.{ent}_link"
                if key not in used:
                    add(key, {"css": f'main a[href^="{css_attr(head)}"]', "multiple": True}, "link", "main", f"links to {tmpl}")
            elif not is_dynamic_name(name):
                add(f"{prefix}.{slug(name)}", {"role": "link", "name": name}, "link", "main", f'link "{name}" -> {href}')

    # Buttons
    for b in raw["buttons"]:
        name = b["name"]
        if not name:
            continue
        kw: dict[str, Any] = {"role": "button", "name": name}
        if b["testid"]:
            kw["test_id"] = b["testid"]
        if b["region"] in ("header", "nav"):
            thing = "logout" if LOGOUT_RX.search(name) else slug(name)
            add(f"header.{thing}", kw, "button", "header", f'button "{name}"')
            continue
        if b["region"] == "footer":
            continue
        if entry_name and name == entry_name:
            thing = "entry"
        elif b["form"] >= 0 and b["form"] in filter_forms:
            thing = "filter_clear" if CLEAR_RX.search(name) else ("filter_apply" if b["type"] == "submit" or FILTER_RX.search(name) else slug(name))
        elif b["form"] >= 0 and b["type"] == "submit":
            thing = "submit"
        else:
            thing = slug(name)
        key = add(f"{prefix}.{thing}", kw, "button", "main", f'button "{name}" type={b["type"]}')
        if b["form"] >= 0 and key:
            forms.setdefault(form_key(b["form"]), []).append(key)

    # Fields
    for f in raw["fields"]:
        label = f["label"]
        in_filter = f["form"] in filter_forms
        thing = field_thing(label, f["type"], in_filter)
        role = {"select": "combobox", "checkbox": "checkbox", "radio": "radio", "textarea": "textbox"}.get(f["type"], "textbox")
        if f["type"] in ("number",):
            role = "spinbutton"
        kw = {"role": role if label else None, "name": label or None, "label": label or None,
              "test_id": f["testid"], "css": f'#{f["id"]}' if f["id"] and re.match(r"^[A-Za-z][\w-]*$", f["id"]) else None}
        if f["type"] == "date":
            kw["role"] = kw["name"] = None  # date inputs expose inconsistent roles; label/css are reliable
        key = add(f"{prefix}.{thing}", kw, "field", f["region"], f'{f["type"]} "{label}"' + (f' hint="{f["hint"]}"' if f["hint"] else ""))
        if f["options"] is not None and key:
            options[key] = f["options"]
        if f["form"] >= 0 and key:
            forms.setdefault(form_key(f["form"]), []).append(key)

    # data-testid elements (lists, cards, values)
    for t in raw["testids"]:
        tid = t["testid"]
        if any(c.ref.test_id == tid for c in cands):
            continue
        region = t["region"]
        scope = "header" if region in ("header", "nav") else prefix
        thing = slug(tid)
        if scope == "header" and CUSTOMER_RX.search(tid + " " + t["sample"]):
            thing = "customer"
        add(f"{scope}.{thing}", {"test_id": tid, "multiple": t["count"] > 1}, "testid", region,
            f'[data-testid={tid}] x{t["count"]} <{t["tag"]}> "{t["sample"][:40]}"')

    # Tables and rows
    for i, tb in enumerate(raw["tables"]):
        sfx = "" if i == 0 else f"_{i + 1}"
        if not tb["testid"]:
            kw = {"role": "table", "name": tb["name"]} if tb["name"] and not is_dynamic_name(tb["name"]) else {"css": "main table"}
            add(f"{prefix}.table{sfx}", kw, "table", "main", f'table "{tb["name"]}" headers={tb["headers"]}')
        row_kw = {"test_id": tb["row_testid"], "multiple": True} if tb["row_testid"] else {"css": "main table tbody tr", "multiple": True}
        add(f"{prefix}.row{sfx}", row_kw, "rows", "main", f"table rows ({tb['rows']} now) headers={tb['headers']}")

    # Page-level containers
    add(f"{prefix}.heading", {"css": "main h1" if raw.get("has_main") else "h1"}, "heading", "main", f'h1 "{raw["h1"]}"')
    if raw.get("has_main"):
        add(f"{prefix}.main", {"css": "main"}, "region", "main", "main content")
    if raw["forms"] or raw["statuses"]:
        add(f"{prefix}.alert", {"role": "alert", "multiple": True}, "alert", "main", "error/validation alerts (may appear later)")
        add(f"{prefix}.status", {"role": "status", "multiple": True}, "status", "main", "status messages")
    kinds = {c.key: c.kind for c in cands}
    forms = {fk: sorted(v, key=lambda k: kinds.get(k) == "button") for fk, v in forms.items()}
    return cands, forms, options


# ---------------------------------------------------------------- entities and API
def _shape(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        return {k: type(v).__name__ if v is not None else "null" for k, v in body.items()}
    if isinstance(body, list):
        return {"[]": "list"}
    return {}


def _lists_of_dicts(body: Any, depth: int = 0) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    if depth > 3:
        return out
    if isinstance(body, list) and body and all(isinstance(x, dict) for x in body):
        out.append(body)
    if isinstance(body, dict):
        for v in body.values():
            out.extend(_lists_of_dicts(v, depth + 1))
    return out


def _is_num(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return isinstance(v, str) and bool(NUMERIC.match(v.strip()))


def _pick(d: dict[str, Any], names: list[str], rx: str | None = None) -> str | None:
    for n in names:
        if n in d:
            return n
    if rx:
        for k in d:
            if re.search(rx, k, re.I):
                return k
    return None


def endpoints_from_network(events: list[NetworkEvent], api_base: str | None) -> list[ApiEndpoint]:
    eps: dict[tuple[str, str], ApiEndpoint] = {}
    for e in events:
        if not e.url.startswith("http") or e.status == 0:
            continue
        p = urlparse(e.url)
        origin = f"{p.scheme}://{p.netloc}"
        if api_base and origin != api_base:
            continue
        key = (e.method, template_path(p.path))
        q = sorted(parse_qs(p.query, keep_blank_values=True))
        ep = eps.get(key)
        if ep is None:
            eps[key] = ApiEndpoint(method=e.method, path_template=key[1], query_params=q,
                                   sample_status=e.status, response_shape=_shape(e.resp_body))
        else:
            ep.query_params = sorted(set(ep.query_params) | set(q))
            if not ep.response_shape and e.resp_body is not None:
                ep.response_shape = _shape(e.resp_body)
    return sorted(eps.values(), key=lambda x: (x.path_template, x.method))


def detect_timezone(hints: list[str], texts: list[str]) -> str | None:
    for source in (hints, texts):
        for h in source:
            for rx, tz in TZ_PATTERNS:
                m = rx.search(h)
                if not m:
                    continue
                if tz == "__iana__":
                    cand = m.group(0)
                    if cand.split("/")[0] in {"America", "Europe", "Asia", "Africa", "Australia", "Pacific", "Atlantic", "Indian", "Etc"}:
                        return cand
                    continue
                return tz
    # Last resort: abbreviations used consistently in displayed date-times.
    abbrs = re.findall(r"\b\d{1,2}:\d{2}\s?(?:AM|PM|am|pm)?\s+([A-Z]{3})\b", " ".join(texts))
    zones = {TZ_ABBR[a] for a in abbrs if a in TZ_ABBR}
    return zones.pop() if len(zones) == 1 else None


def extract_entities(events: list[NetworkEvent], api_base: str | None) -> dict[str, Any]:
    ent: dict[str, Any] = {}
    best_acc: tuple[int, int, str, list[dict[str, Any]]] | None = None
    best_tx: tuple[int, int, str, list[dict[str, Any]]] | None = None
    tx_query_params: dict[str, set[str]] = {}
    for e in events:
        if e.method != "GET" or e.status >= 400 or not e.url.startswith("http"):
            continue
        p = urlparse(e.url)
        if api_base and f"{p.scheme}://{p.netloc}" != api_base:
            continue
        path = template_path(p.path)
        has_query = bool(p.query)
        for lst in _lists_of_dicts(e.resp_body):
            first = lst[0]
            bal = _pick(first, ["balance", "available_balance", "current_balance"], r"balance")
            if bal and _is_num(first.get(bal)):
                score = (0 if not has_query and "{id}" not in path else 1, -len(lst))
                if best_acc is None or score < best_acc[:2]:
                    best_acc = (*score, path, lst)
                continue
            amt = _pick(first, ["amount", "value", "total"], r"amount")
            dt = next((k for k, v in first.items() if isinstance(v, str) and ISO_DT.match(v)), None)
            if amt and dt:
                tx_query_params.setdefault(path, set()).update(parse_qs(p.query, keep_blank_values=True))
                score = (0 if not has_query and "{id}" not in path else 1, -len(lst))
                if best_tx is None or score < best_tx[:2]:
                    best_tx = (*score, path, lst)

    if best_acc:
        _, _, path, lst = best_acc
        f = lst[0]
        k_id = _pick(f, ["id", "uuid"], r"_id$")
        k_name = _pick(f, ["name", "display_name", "nickname", "title", "label"])
        k_type = _pick(f, ["type", "kind", "account_type", "category"])
        k_mask = _pick(f, ["masked_number", "mask", "last4", "last_four"], r"mask|last.?4|number")
        k_bal = _pick(f, ["balance", "available_balance", "current_balance"], r"balance")
        k_cur = _pick(f, ["currency", "currency_code"])
        ent["accounts"] = [
            {"id": a.get(k_id) if k_id else None, "name": a.get(k_name) if k_name else None,
             "type": a.get(k_type) if k_type else None, "masked_number": a.get(k_mask) if k_mask else None,
             "balance": str(a.get(k_bal)) if k_bal and a.get(k_bal) is not None else None}
            for a in lst
        ]
        ent["accounts_endpoint"] = path
        if k_cur and f.get(k_cur):
            ent["currency"] = f.get(k_cur)
    if best_tx:
        _, _, path, lst = best_tx
        f = lst[0]
        k_type = _pick(f, ["type", "kind", "transaction_type", "category"])
        k_status = _pick(f, ["status", "state"])
        k_dt = _pick(f, ["occurred_at", "created_at", "timestamp", "date", "posted_at"]) or next(
            (k for k, v in f.items() if isinstance(v, str) and ISO_DT.match(v)), None)
        k_cur = _pick(f, ["currency", "currency_code"])
        ent["transactions_endpoint"] = path
        ent["transactions_query_params"] = sorted(tx_query_params.get(path, set()))
        ent["transactions_sample_count"] = len(lst)
        if k_type:
            ent["tx_types"] = sorted({str(t[k_type]) for t in lst if t.get(k_type) is not None})
        if k_status:
            ent["tx_statuses"] = sorted({str(t[k_status]) for t in lst if t.get(k_status) is not None})
        if k_dt:
            dts = sorted(str(t[k_dt]) for t in lst if isinstance(t.get(k_dt), str))
            if dts:
                ent["date_span"] = [dts[0], dts[-1]]
        if k_cur and "currency" not in ent and f.get(k_cur):
            ent["currency"] = f.get(k_cur)
    return ent


def money_currency_from_text(text: str) -> str | None:
    m = re.search(r"([$€£¥])\s?\d", text)
    return {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}.get(m.group(1)) if m else None


# ---------------------------------------------------------------- crawl
async def _extract(browser: Any) -> dict[str, Any]:
    return await browser.evaluate(EXTRACT_JS)


async def _verify_refs(browser: Any, cands: list[Candidate]) -> None:
    """Drop role+name pairs Playwright cannot find, so every stored locator works today."""
    page = getattr(browser, "page", None)
    if page is None:
        return
    for c in cands:
        r = c.ref
        if r.role and r.name:
            try:
                n = await page.get_by_role(r.role, name=r.name, exact=True).count()
            except Exception:
                n = 0
            if n == 0:
                r.role = r.role if not (r.test_id or r.label or r.css) else None
                if r.role is None:
                    r.name = None
        if r.label and not r.role:
            try:
                if await page.get_by_label(r.label, exact=True).count() == 0:
                    r.label = None if (r.css or r.test_id) else r.label
            except Exception:
                pass


async def _llm_label(llm: Any, route: str, raw: dict[str, Any], snap: str, cands: list[Candidate]) -> PageLabels | None:
    if llm is None:
        return None
    from ..llm.client import load_prompt

    listing = "\n".join(f"- {c.key}: {c.desc}" for c in cands)
    user = f"Route: {route}\nHeading: {raw['h1']}\n\nCandidates:\n{listing}\n\nARIA snapshot:\n{snap}"
    try:
        out = await llm.complete(
            agent="explorer", purpose=f"label:{route}", model=FAST_MODEL,
            system=load_prompt("explore_label.md"), user=user, output_model=PageLabels,
            max_tokens=2_000, cache_parts=(PROMPT_VERSION, dom_hash(raw)),
        )
        return out if isinstance(out, PageLabels) else None
    except Exception as exc:  # FakeLLM miss, budget exhausted, refusal, network
        log.info("explorer labeler fell back to heuristics for %s: %s", route, exc)
        return None


def _apply_labels(labels: PageLabels | None, cands: list[Candidate], forms: dict[str, list[str]], raw: dict[str, Any], route: str) -> tuple[str, str, dict[str, ElementRef], dict[str, list[str]], dict[str, str]]:
    rename: dict[str, str] = {}
    if labels:
        valid = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
        taken = {c.key for c in cands}
        for r in labels.renames:
            if r.from_key in taken and valid.match(r.to_key) and r.to_key not in rename.values():
                rename[r.from_key] = r.to_key
    elements: dict[str, ElementRef] = {}
    for c in cands:
        k = rename.get(c.key, c.key)
        if k in elements:
            k = c.key if c.key not in elements else f"{k}_{len(elements)}"
        c.ref.key = k
        elements[k] = c.ref
    if labels and labels.forms:
        out_forms = {slug(fk): [rename.get(x, x) for x in v if rename.get(x, x) in elements] for fk, v in labels.forms.items()}
        out_forms = {k: v for k, v in out_forms.items() if v}
    else:
        out_forms = {fk: [rename.get(x, x) for x in v] for fk, v in forms.items()}
    title = (labels.title if labels else None) or raw["h1"] or raw["doc_title"] or route
    purpose = (labels.purpose if labels else None) or _heuristic_purpose(raw)
    return title, purpose, elements, out_forms, rename


def _heuristic_purpose(raw: dict[str, Any]) -> str:
    parts = []
    if raw["tables"]:
        parts.append(f"lists {raw['tables'][0]['name'] or 'records'} in a table")
    if raw["forms"]:
        parts.append("has form(s): " + ", ".join(f["name"] or "unnamed" for f in raw["forms"]))
    if not parts:
        parts.append("shows " + (raw["h1"] or "content"))
    return f"{raw['h1'] or 'Page'}: " + "; ".join(parts)


def _hints(raw: dict[str, Any]) -> list[str]:
    hs: list[str] = []
    for f in raw["fields"]:
        if f["hint"] and f["hint"] not in hs:
            hs.append(f["hint"])
    for f in raw["forms"]:
        for n in f["notes"]:
            if n not in hs:
                hs.append(n)
    return hs


def _observed_states(raw: dict[str, Any], settle_stats: dict[str, Any]) -> list[str]:
    states: list[str] = []
    if settle_stats.get("saw_loading") or settle_stats.get("saw_busy"):
        states.append("loading")
    for s in raw["statuses"]:
        t = s["text"].lower()
        if s["role"] == "alert" and t:
            states.append("error")
        elif re.search(r"\bno \w+|nothing|empty|0 (results|items|records|transactions)\b", t):
            states.append("empty")
        elif re.search(r"success|completed|saved|sent", t):
            states.append("success")
    return sorted(set(states))


async def _choose_entry(raw: dict[str, Any], llm: Any) -> dict[str, Any] | None:
    cands = [dict(kind="button", **b) for b in raw["buttons"] if b["name"] and not b["disabled"]]
    cands += [dict(kind="link", **l) for l in raw["links"]
              if l["name"] and not (l["href"] or "").startswith(("#", "mailto:", "tel:"))
              and urlparse(l["abs"]).netloc == urlparse(raw["url"]).netloc]
    if not cands:
        return None
    scored = []
    for c in cands:
        s = 0
        if ENTRY_RX.search(c["name"]):
            s += 3
        if c["kind"] == "button":
            s += 1
        if c["region"] == "main":
            s += 1
        if LOGOUT_RX.search(c["name"]) or re.search(r"skip|privacy|terms|help|docs?", c["name"], re.I):
            s -= 5
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    top = scored[0]
    ambiguous = len(scored) > 1 and scored[1][0] == top[0]
    if (top[0] < 3 or ambiguous) and llm is not None:
        from ..llm.client import load_prompt

        listing = "\n".join(f"{i}. {c['kind']} \"{c['name']}\" region={c['region']}" for i, (_, c) in enumerate(scored))
        try:
            pick = await llm.complete(
                agent="explorer", purpose="entry", model=FAST_MODEL, system=load_prompt("explore_entry.md"),
                user=f"Landing page heading: {raw['h1']}\n\nCandidates:\n{listing}", output_model=EntryPick,
                max_tokens=300, cache_parts=(PROMPT_VERSION, listing),
            )
            if isinstance(pick, EntryPick) and 0 <= pick.index < len(scored):
                return scored[pick.index][1]
        except Exception as exc:
            log.info("entry pick fell back to heuristics: %s", exc)
    return top[1] if top[0] >= 3 else None


async def _click_by_name(browser: Any, kind: str, name: str) -> None:
    page = browser.page
    role = "button" if kind == "button" else "link"
    loc = page.get_by_role(role, name=name, exact=True)
    await (loc.first if await loc.count() > 1 else loc).click()


async def _wait_url_change(browser: Any, before: str, timeout_s: float = 6.0) -> None:
    import asyncio

    for _ in range(int(timeout_s / 0.1)):
        if await browser.current_url() != before:
            break
        await asyncio.sleep(0.1)
    await browser.settle()


async def _exercise_filter_form(browser: Any, raw: dict[str, Any], route_path: str, entities_hint: dict[str, Any]) -> None:
    """With the mutation guard active, apply each filter form once to learn its query params."""
    page = browser.page
    filter_forms = [f for f in raw["forms"] if FILTER_RX.search(f["name"] or "") or any(
        b["form"] == f["idx"] and FILTER_RX.search(b["name"]) for b in raw["buttons"])]
    for f in filter_forms:
        form_loc = page.locator("form, [role=form], [role=search]").nth(f["idx"])
        for fld in [x for x in raw["fields"] if x["form"] == f["idx"]]:
            try:
                if fld["type"] == "select" and fld["options"] and len(fld["options"]) > 1:
                    target = next((o for o in fld["options"][1:] if o["value"]), None)
                    if target:
                        sel = form_loc.locator("select").filter(has=page.locator(f'option[value="{css_attr(target["value"])}"]')).first
                        await sel.select_option(value=target["value"], timeout=2_000)
                elif fld["type"] == "date" and fld["id"]:
                    span = entities_hint.get("date_span") or []
                    if span:
                        is_start = "start" in field_thing(fld["label"], "date", True)
                        await page.locator(f'#{fld["id"]}').fill((span[0] if is_start else span[1])[:10], timeout=2_000)
            except Exception as exc:
                log.debug("filter field %s not exercised: %s", fld["label"], exc)
        submit = next((b for b in raw["buttons"] if b["form"] == f["idx"] and b["type"] == "submit" and not CLEAR_RX.search(b["name"])), None)
        if submit is None:
            continue
        try:
            await form_loc.get_by_role("button", name=submit["name"], exact=True).click(timeout=3_000)
            await browser.settle()
        except Exception as exc:
            log.debug("filter submit failed: %s", exc)
        await browser.goto(route_path)


async def explore(browser: Any, llm: Any, extra_routes: list[str] | None = None) -> SiteMap:
    """Crawl the live site. `extra_routes` (e.g. routes found in the target's source) are visited
    after the routes reachable by links, so pages without a visible link are still modelled."""
    base = browser.base_url.rstrip("/")
    base_netloc = urlparse(base).netloc
    if hasattr(browser, "set_mutation_guard"):
        await browser.set_mutation_guard(False)
    browser.clear_network_log()

    pages: dict[str, PageModel] = {}
    options_all: dict[str, list[dict[str, str]]] = {}
    hints_all: list[str] = []
    texts_all: list[str] = []
    auth: dict[str, Any] = {"landing_route": "/"}

    # 1. Landing page and entry action
    await browser.goto("/")
    landing_raw = await _extract(browser)
    landing_path = urlparse(landing_raw["url"]).path or "/"
    auth["landing_route"] = landing_path
    entry = await _choose_entry(landing_raw, llm)

    async def model_page(raw: dict[str, Any], route: str, *, landing: bool, settle_stats: dict[str, Any]) -> None:
        prefix = page_prefix(route, landing=landing)
        cands, forms, options = heuristic_candidates(raw, prefix, entry_name=entry["name"] if (landing and entry) else None)
        await _verify_refs(browser, cands)
        snap = await browser.aria_snapshot(max_chars=6_000)
        labels = await _llm_label(llm, route, raw, snap, cands)
        title, purpose, elements, out_forms, rename = _apply_labels(labels, cands, forms, raw, route)
        for k, v in options.items():
            options_all[rename.get(k, k)] = v
        hints = _hints(raw)
        hints_all.extend(hints)
        texts_all.append(raw["text"])
        pages[route] = PageModel(route=route, title=title, purpose=purpose, elements=elements, forms=out_forms,
                                 observed_states=_observed_states(raw, settle_stats), hints=hints)

    landing_settle = dict(getattr(browser, "last_settle", {}) or {})
    entry_post: list[str] = []
    if entry is None:
        log.warning("explorer: no entry action found; assuming the site needs no session")
        await model_page(landing_raw, landing_path, landing=False, settle_stats=landing_settle)
        start_path = landing_path
    else:
        await model_page(landing_raw, landing_path, landing=True, settle_stats=landing_settle)
        entry_key = next((k for k, r in pages[landing_path].elements.items()
                          if r.name == entry["name"] and r.role == ("button" if entry["kind"] == "button" else "link")),
                         f"landing.{slug(entry['name'])}")
        auth["entry"] = {"key": entry_key, "role": "button" if entry["kind"] == "button" else "link", "name": entry["name"]}
        before = await browser.current_url()
        n_before = len(browser.network_log())
        await _click_by_name(browser, entry["kind"], entry["name"])
        await _wait_url_change(browser, before)
        for e in browser.network_log()[n_before:]:
            if e.method in ("POST", "PUT", "PATCH", "DELETE"):
                entry_post.append(e.url)
                auth["entry_request"] = {"method": e.method, "path": urlparse(e.url).path, "status": e.status}
        start_path = urlparse(await browser.current_url()).path or "/"
        auth["post_login_route"] = start_path

    # From here on nothing may change server state.
    if hasattr(browser, "set_mutation_guard"):
        await browser.set_mutation_guard(True, allow=entry_post)

    # 2. BFS over same-origin in-app links
    queue: deque[str] = deque([start_path, *(r for r in (extra_routes or []) if r != start_path)])
    seen_templates: set[str] = {template_path(landing_path)} if entry else set()
    visited: dict[str, str] = {}  # template -> concrete path
    logout_ref: dict[str, Any] | None = None
    while queue and len(visited) < MAX_PAGES:
        path = queue.popleft()
        tmpl = template_path(path)
        if tmpl in visited:
            continue
        try:
            await browser.goto(path)
        except Exception as exc:
            log.warning("explorer: cannot open %s: %s", path, exc)
            continue
        actual = urlparse(await browser.current_url()).path or "/"
        if template_path(actual) != tmpl and template_path(actual) in visited | {template_path(landing_path)}:
            continue  # redirected somewhere already known
        settle_stats = dict(getattr(browser, "last_settle", {}) or {})
        raw = await _extract(browser)
        visited[tmpl] = path
        seen_templates.add(tmpl)
        await model_page(raw, tmpl, landing=False, settle_stats=settle_stats)
        for k, r in pages[tmpl].elements.items():
            if logout_ref is None and r.role == "button" and r.name and LOGOUT_RX.search(r.name):
                logout_ref = {"key": k, "role": "button", "name": r.name}
        if FILTER_RX.search(" ".join(f["name"] for f in raw["forms"])) and hasattr(browser, "set_mutation_guard"):
            hint_ent = extract_entities(_all_events(browser), getattr(browser, "api_base", None))
            await _exercise_filter_form(browser, raw, path, hint_ent)
        for l in raw["links"]:
            p = urlparse(l["abs"])
            if p.netloc != base_netloc or (l["href"] or "").startswith(("#", "mailto:", "tel:")):
                continue
            if LOGOUT_RX.search(l["name"] or ""):
                continue
            t = template_path(p.path or "/")
            if t not in seen_templates:
                seen_templates.add(t)
                queue.append(p.path or "/")
    if logout_ref:
        auth["logout"] = logout_ref

    # 3. Network-derived API map and live entities
    events = _all_events(browser)
    api_base = getattr(browser, "api_base", None)
    endpoints = endpoints_from_network(events, api_base)
    entities = extract_entities(events, api_base)
    entities["timezone"] = detect_timezone(hints_all, texts_all)
    if "currency" not in entities:
        entities["currency"] = money_currency_from_text(" ".join(texts_all))
    if options_all:
        entities["options"] = options_all

    # 4. Which routes need the session: re-open them in a fresh context
    protected: list[str] = []
    if entry is not None and hasattr(browser, "new_context"):
        await browser.new_context(fresh=True)
        for tmpl, path in visited.items():
            try:
                await browser.goto(path)
            except Exception:
                continue
            raw = await _extract(browser)
            now = urlparse(raw["url"]).path or "/"
            shows_entry = any(b["name"] == entry["name"] for b in raw["buttons"]) or any(
                l["name"] == entry["name"] for l in raw["links"])
            if now != path or shows_entry or ACCESS_RX.search(raw["h1"] + " " + " ".join(s["text"] for s in raw["statuses"])):
                protected.append(tmpl)
        # Leave the browser logged in, as it was before the check.
        try:
            await browser.goto(landing_path)
            before = await browser.current_url()
            await _click_by_name(browser, entry["kind"], entry["name"])
            await _wait_url_change(browser, before)
        except Exception as exc:
            log.warning("explorer: re-entry after protected-route check failed: %s", exc)
    auth["protected_routes"] = protected

    if hasattr(browser, "set_mutation_guard"):
        await browser.set_mutation_guard(False)

    sitemap = SiteMap(base_url=base, api_base=api_base, pages=pages, api=endpoints, auth=auth, entities=entities)
    if hasattr(browser, "sitemap"):
        try:
            browser.sitemap = sitemap
        except Exception:
            pass
    return sitemap


def _all_events(browser: Any) -> list[NetworkEvent]:
    fn = getattr(browser, "all_network", None)
    return fn() if callable(fn) else browser.network_log()


__all__ = ["explore", "PageLabels", "PROMPT_VERSION", "extract_entities", "endpoints_from_network",
           "detect_timezone", "heuristic_candidates", "template_path"]
