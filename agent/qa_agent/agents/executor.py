"""Executor: run one Scenario against the live site and record what happened (CONVENTIONS §1, §4, §5).

It never decides pass/fail: it resolves params, runs steps, runs one probe per oracle and
attaches network, snapshots and evidence. The Verifier judges the result.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..browser.port import BrowserPort
from ..contracts import Scenario, ScenarioRun, SiteMap
from ..dsl.actions import ExecContext, enter_session, run_step
from ..dsl.params import UnresolvableParam, placeholders, resolve_params
from ..dsl.probes import button_watch_plan, run_probes, watched_click
from ..evidence.store import EvidenceStore
from ..state.snapshot import StateManager, event_key

log = logging.getLogger(__name__)

SNAPSHOT_ORACLES = {"balance_delta", "no_state_change", "new_tx_count", "tx_present_in_histories"}
LIVE_SOURCES = {"account", "balance", "transaction"}


async def _session_alive(state: StateManager) -> bool:
    ep = state.accounts_endpoint
    if not ep:
        return False
    try:
        ev = await state.api("GET", ep)
    except Exception:  # noqa: BLE001
        return False
    return ev.status < 400


async def ensure_logged_in(browser: BrowserPort, sitemap: SiteMap, state: StateManager) -> None:
    if await _session_alive(state):
        return
    await enter_session(browser, sitemap)
    if not await _session_alive(state):
        raise RuntimeError("could not establish a session via sitemap.auth.entry")


def _needs_snapshots(s: Scenario) -> bool:
    return s.mutates_state or any(o.type in SNAPSHOT_ORACLES for o in s.oracles)


async def run_scenario(
    scenario: Scenario,
    browser: BrowserPort,
    sitemap: SiteMap,
    state: StateManager,
    *,
    attempt: int,
    evidence_dir: Path,
) -> ScenarioRun:
    run = ScenarioRun(scenario_id=scenario.id, attempt=attempt)
    store = EvidenceStore(evidence_dir, scenario.id, attempt)
    sctx = state.begin(scenario)
    run.observations["run_tag"] = sctx.run_tag
    ctx = ExecContext(scenario=scenario, browser=browser, sitemap=sitemap, state=state, run=run, params={},
                      store=store)
    ctx.extras["console_start"] = len(getattr(browser, "console_log", None) or [])
    pre = set(scenario.preconditions)
    needs_snap = _needs_snapshots(scenario)
    t_start = time.monotonic()

    # 1. Session + live data for params (always with a session: the API needs it).
    try:
        await ensure_logged_in(browser, sitemap, state)
        live = needs_snap or any(p.source in LIVE_SOURCES for p in scenario.params)
        await (state.snapshot() if live else state.ensure_fresh())
    except Exception as e:  # noqa: BLE001
        run.aborted_reason = f"setup failed: {type(e).__name__}: {e}"[:500]
        await _finish(run, ctx, store, needs_snap=False)
        return run

    # 2. Params.
    try:
        params = resolve_params(scenario.params, state.param_context(scenario.id))
        used = set()
        for st in scenario.steps:
            used |= placeholders(st.value) | placeholders(st.target.model_dump() if st.target else None)
        for o in scenario.oracles:
            used |= placeholders(o.args)
        used |= placeholders(scenario.expected_money_delta)
        missing = sorted(used - set(params))
        if missing:
            raise UnresolvableParam(missing[0], "placeholder has no binding")
    except UnresolvableParam as e:
        run.aborted_reason = f"unresolvable param {e.name}" + (f" ({e.why})" if e.why else "")
        await _finish(run, ctx, store, needs_snap=False)
        return run
    ctx.params = params
    sctx.params = dict(params)
    run.resolved_params = {k: str(v) for k, v in params.items()}

    # 3. Preconditions that drop the session, then the baseline snapshot.
    try:
        if "logged_out" in pre or "fresh_context" in pre:
            if needs_snap:
                run.state_before = await state.snapshot()
            await browser.new_context(fresh=True)
        elif needs_snap and not any(st.action == "snapshot_state" for st in scenario.steps):
            run.state_before = await state.snapshot()
    except Exception as e:  # noqa: BLE001
        run.aborted_reason = f"precondition failed: {type(e).__name__}: {e}"[:500]
        await _finish(run, ctx, store, needs_snap=False)
        return run

    # 4. Steps (stop on first hard failure).
    browser.clear_network_log()
    watch = button_watch_plan(scenario, params)
    for i, step in enumerate(scenario.steps):
        res = await watched_click(step, i, ctx, watch[i]) if i in watch else None
        if res is None:
            res = await run_step(step, i, ctx)
        run.steps.append(res)
        if not res.ok:
            run.aborted_reason = f"step {i} ({step.action}: {step.note}) failed: {res.error}"[:500]
            break

    # 5. Network + snapshot after steps, then probes. One more settle first: a late request fired by
    # the last action (e.g. a fetch after a client-side URL change) must land before we read the page.
    try:
        await browser.settle()
    except Exception:  # noqa: BLE001
        pass
    _collect_network(run, ctx)
    if needs_snap:
        await _final_snapshot(run, state)
    if run.aborted_reason is None:
        async def after_mutation() -> None:
            _collect_network(run, ctx)
            if needs_snap:
                await _final_snapshot(run, state)

        run.observations["probes"] = await run_probes(scenario.oracles, ctx, after_mutation)
    run.observations["duration_ms"] = int((time.monotonic() - t_start) * 1000)
    await _finish(run, ctx, store, needs_snap=needs_snap)
    return run


def _collect_network(run: ScenarioRun, ctx: ExecContext) -> None:
    own = ctx.state.own_event_keys
    deliberate = {event_key(e) for e in ctx.extras.get("api_events", [])}
    events = [e for e in ctx.browser.network_log() if event_key(e) not in own or event_key(e) in deliberate]
    seen = {event_key(e) for e in events}
    for e in ctx.extras.get("api_events", []):  # scenario api_get/api_post, if the port didn't log them
        if event_key(e) not in seen:
            events.append(e)
    run.network = sorted(events, key=lambda e: e.t_ms)


async def _final_snapshot(run: ScenarioRun, state: StateManager) -> None:
    try:
        run.state_after = await state.snapshot()
    except Exception as e:  # noqa: BLE001
        run.observations["state_after_error"] = str(e)[:300]


async def _finish(run: ScenarioRun, ctx: ExecContext, store: EvidenceStore, *, needs_snap: bool) -> None:
    b = ctx.browser
    try:
        run.observations["url"] = await b.current_url()
    except Exception:  # noqa: BLE001
        pass
    evidence = []
    console = getattr(b, "console_log", None)
    if isinstance(console, list):
        entries = console[ctx.extras.get("console_start", 0):]
        run.observations["console"] = entries
        if entries:
            try:
                evidence.append(store.save_json("console", entries, kind="console",
                                                caption=f"{len(entries)} browser console error(s)").model_dump())
            except Exception:  # noqa: BLE001
                pass
    try:
        evidence.append((await b.screenshot(store.name("final"))).model_dump())
    except Exception as e:  # noqa: BLE001
        run.observations["screenshot_error"] = str(e)[:300]
    try:
        evidence.append(store.save_network(run.network).model_dump())
    except Exception:  # noqa: BLE001
        pass
    run.observations["evidence"] = evidence
    if needs_snap and run.state_before is not None and run.state_after is not None:
        try:
            foreign = ctx.state.foreign_tx_ids(run)
            run.foreign_activity = bool(foreign)
            if foreign:
                run.observations["foreign_tx_ids"] = foreign
            if ctx.scenario.mutates_state:
                run.observations["ledger"] = [e.model_dump() for e in ctx.state.record_ledger(run)]
        except Exception as e:  # noqa: BLE001
            log.warning("ledger/foreign detection failed for %s: %s", run.scenario_id, e)
