"""Execute one DSL Step through the BrowserPort (CONVENTIONS §1, §3)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..browser.port import BrowserPort
from ..contracts import Evidence, Scenario, ScenarioRun, SiteMap, Step, StepResult, Target
from .params import substitute

if TYPE_CHECKING:
    from ..evidence.store import EvidenceStore
    from ..state.snapshot import StateManager

SETTLE_MS = 10_000
WAIT_MS = 10_000

_JS_SUBMIT = """() => { /*qa:submit*/
  const a = document.activeElement;
  const form = (a && a.form) || document.querySelector('main form') || document.querySelector('form');
  if (!form) return false;
  if (form.requestSubmit) form.requestSubmit(); else form.submit();
  return true;
}"""

_JS_BODY_TEXT = "() => /*qa:body_text*/ (document.body && document.body.innerText) || ''"


class StepFailed(Exception):
    pass


@dataclass
class ExecContext:
    scenario: Scenario
    browser: BrowserPort
    sitemap: SiteMap
    state: "StateManager"
    run: ScenarioRun
    params: dict[str, str]
    store: "EvidenceStore | None" = None
    extras: dict[str, Any] = field(default_factory=dict)


def auth_target(sitemap: SiteMap, which: str) -> Target:
    """Target for sitemap.auth['entry'|'logout'] (ElementRef-like dict, a key string, or nothing)."""
    ref = (sitemap.auth or {}).get(which)
    if isinstance(ref, str) and ref:
        return Target(key=ref)
    if isinstance(ref, dict) and ref.get("key"):
        return Target(key=str(ref["key"]))
    if isinstance(ref, dict):
        # Find a sitemap element with the same role/name/test id; else a key whose words are its name,
        # which the browser layer can still guess by accessible name.
        for page in sitemap.pages.values():
            for k, el in page.elements.items():
                if (ref.get("test_id") and el.test_id == ref.get("test_id")) or \
                        (ref.get("name") and el.name == ref.get("name") and el.role == ref.get("role", el.role)):
                    return Target(key=k)
        if ref.get("name"):
            slug = re.sub(r"[^a-z0-9]+", "_", str(ref["name"]).lower()).strip("_")
            return Target(key=f"auth.{slug or which}")
    return Target(key=f"auth.{which}")


def _sub_target(t: Target | None, params: dict[str, str]) -> Target | None:
    if t is None:
        return None
    return Target(**substitute(t.model_dump(), params))


def parse_viewport(value: str | None) -> tuple[int, int]:
    nums = [int(n) for n in re.findall(r"\d+", value or "")]
    if not nums:
        return 1280, 800
    return nums[0], (nums[1] if len(nums) > 1 else 800)


def parse_api_value(value: str | None) -> tuple[str, Any]:
    """'path' | 'path {json}' | '{"path": ..., "body": ...}'."""
    v = (value or "").strip()
    if v.startswith("{"):
        d = json.loads(v)
        return str(d.get("path", "")), d.get("body")
    path, _, rest = v.partition(" ")
    rest = rest.strip()
    return path, (json.loads(rest) if rest else None)


async def _wait_visible(browser: BrowserPort, target: Target, timeout_ms: int) -> str:
    deadline = time.monotonic() + timeout_ms / 1000
    last: Exception | None = None
    while True:
        try:
            el, desc, _ = await browser.resolve(target)
            if await el.is_visible():
                return desc
        except Exception as e:  # noqa: BLE001
            last = e
        if time.monotonic() > deadline:
            raise StepFailed(f"timed out waiting for {target.key}" + (f": {last}" if last else ""))
        await asyncio.sleep(0.25)


async def enter_session(ctx_browser: BrowserPort, sitemap: SiteMap) -> str:
    """Use the site's own entry action (e.g. a 'start demo' button) from its landing route."""
    landing = (sitemap.auth or {}).get("landing_route") or "/"
    await ctx_browser.goto(landing)
    await ctx_browser.settle(SETTLE_MS)
    el, desc, _ = await ctx_browser.resolve(auth_target(sitemap, "entry"))
    await el.click()
    await ctx_browser.settle(SETTLE_MS)
    return desc


async def _do(step: Step, ctx: ExecContext) -> tuple[str | None, bool]:
    """Perform the action. Returns (locator description, healed)."""
    b, p = ctx.browser, ctx.params
    value = substitute(step.value, p) if step.value is not None else None
    target = _sub_target(step.target, p)
    a = step.action

    async def el_for(t: Target | None):
        if t is None:
            raise StepFailed(f"{a} needs a target")
        return await b.resolve(t)

    if a == "goto":
        path = value or "/"
        await b.goto(path)
        return f"goto {path}", False
    if a in ("click", "submit") and target is not None:
        el, desc, healed = await el_for(target)
        await el.click()
        return desc, healed
    if a == "submit":
        ok = await b.evaluate(_JS_SUBMIT)
        if not ok:
            raise StepFailed("no form to submit")
        return "form.requestSubmit()", False
    if a == "click":
        raise StepFailed("click needs a target")
    if a == "fill":
        el, desc, healed = await el_for(target)
        await el.fill(value or "")
        return desc, healed
    if a == "select":
        el, desc, healed = await el_for(target)
        await el.select(value or "")
        return desc, healed
    if a == "reload":
        await b.reload()
        return "reload", False
    if a == "back":
        await b.back()
        return "back", False
    if a == "logout":
        el, desc, healed = await el_for(target or auth_target(ctx.sitemap, "logout"))
        await el.click()
        return desc, healed
    if a == "enter_demo":
        if target is not None:
            el, desc, healed = await el_for(target)
            await el.click()
            return desc, healed
        return await enter_session(b, ctx.sitemap), False
    if a == "set_viewport":
        w, h = parse_viewport(value)
        await b.set_viewport(w, h)
        return f"viewport {w}x{h}", False
    if a == "wait_for":
        if target is not None:
            ms = int(value) if value and value.strip().isdigit() else WAIT_MS
            return await _wait_visible(b, target, ms), False
        if value and value.strip().isdigit():
            await asyncio.sleep(min(int(value), 30_000) / 1000)
            return f"wait {value}ms", False
        if value:
            deadline = time.monotonic() + WAIT_MS / 1000
            while value not in (await b.evaluate(_JS_BODY_TEXT) or ""):
                if time.monotonic() > deadline:
                    raise StepFailed(f"text {value!r} did not appear")
                await asyncio.sleep(0.25)
            return f"text {value!r}", False
        return "settle", False
    if a == "snapshot_state":
        snap = await ctx.state.snapshot()
        if ctx.run.state_before is None:
            ctx.run.state_before = snap
        else:
            ctx.run.state_after = snap
        return "api snapshot", False
    if a in ("api_get", "api_post"):
        path, body = parse_api_value(value)
        method = "GET" if a == "api_get" else "POST"
        # Deliberate scenario API calls belong in run.network: use the port directly (not state.api).
        ev = await b.api_request(method, path, body if method == "POST" else None)
        ctx.extras.setdefault("api_events", []).append(ev)
        ctx.run.observations.setdefault("api_calls", []).append(
            {"method": method, "path": path, "status": ev.status, "body": ev.resp_body})
        return f"{method} {path}", False
    raise StepFailed(f"unknown action {a}")


async def run_step(step: Step, idx: int, ctx: ExecContext) -> StepResult:
    t0 = time.monotonic()
    evidence: list[Evidence] = []
    try:
        desc, healed = await _do(step, ctx)
        try:
            await ctx.browser.settle(SETTLE_MS)
        except Exception:  # noqa: BLE001 - a page that never settles is not a step failure by itself
            pass
        return StepResult(step_idx=idx, ok=True, locator_used=desc, healed=healed,
                          duration_ms=int((time.monotonic() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:500]
        try:
            name = ctx.store.name(f"step{idx}_failure") if ctx.store else f"{ctx.scenario.id}__step{idx}_failure"
            evidence.append(await ctx.browser.screenshot(name, highlight=_sub_target(step.target, ctx.params)))
        except Exception:  # noqa: BLE001
            pass
        return StepResult(step_idx=idx, ok=False, locator_used=step.target.key if step.target else None,
                          duration_ms=int((time.monotonic() - t0) * 1000), error=err, evidence=evidence)
