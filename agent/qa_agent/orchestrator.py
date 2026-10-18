"""Runs the whole pipeline: spec + explore -> plan -> execute by phase -> verify -> report."""
import asyncio
import fnmatch
import logging
import platform
import secrets
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from .agents.executor import ensure_logged_in, run_scenario
from .agents.explorer import explore
from .agents.reporter import add_executive_summary, build_report, render, render_pdf
from .agents.spec_analyst import extract_requirements, prd_sha256
from .agents.test_planner import plan_tests
from .agents.verifier import evaluate, judge_pending, merge_attempts, needs_retry, rollup, triage_failures
from .browser.chaos import ChaosConfig
from .browser.recovery import error_state
from .browser.session import launch
from .cache.artifacts import ArtifactCache
from .context.repo import discover_routes, resolve_repo
from .config import FAST_MODEL, REASONING_MODEL, RunConfig
from .contracts import RequirementSet, Scenario, ScenarioRun, ScenarioVerdict, SiteMap, TestPlan
from .llm.client import ClaudeLLM
from .llm.cost import CostTracker
from .state.snapshot import StateManager

log = logging.getLogger(__name__)
console = Console()

PHASES = ["read_only", "negative_mutation", "positive_mutation", "session_destructive"]


def new_run_id() -> str:
    return f"qa-{datetime.now():%Y%m%d-%H%M}-{secrets.token_hex(2)}"


def _write(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2))


def _select(plan: TestPlan, cfg: RunConfig) -> tuple[list[Scenario], list[str]]:
    """Apply --only and --read-only. Returns (scenarios to run, limitation notes)."""
    notes: list[str] = []
    scenarios = plan.scenarios
    if cfg.only:
        scenarios = [s for s in scenarios
                     if any(fnmatch.fnmatch(r, pat) for r in s.requirement_ids for pat in cfg.only)]
        notes.append(f"Run restricted to requirements matching {cfg.only}.")
    if cfg.read_only:
        skipped = [s for s in scenarios if s.mutates_state]
        scenarios = [s for s in scenarios if not s.mutates_state]
        if skipped:
            notes.append(f"--read-only: {len(skipped)} data-changing scenarios were not run; "
                         "their requirements are reported as unverified.")
    order = {p: i for i, p in enumerate(PHASES)}
    return sorted(scenarios, key=lambda s: order[s.phase]), notes


DEFAULT_SCENARIO_TIMEOUT_S = 90.0
# Step actions / oracle probes that touch the shared session or data: never run in parallel.
_SESSION_ACTIONS = {"logout", "enter_demo", "api_post"}
_SESSION_PROBES = {"button_disabled_during"}


def _aborted(scn: Scenario, attempt: int, reason: str) -> ScenarioRun:
    return ScenarioRun(scenario_id=scn.id, attempt=attempt, aborted_reason=reason[:500])


def _unverified(scn: Scenario, note: str) -> ScenarioVerdict:
    return ScenarioVerdict(scenario_id=scn.id, status="unverified", confidence=0.0, judge_notes=note[:500])


def _chaos_of(browser) -> object | None:
    return getattr(browser, "chaos", None)


async def _attempt(scn: Scenario, browser, sitemap: SiteMap, state: StateManager, evidence_dir: Path,
                   attempt: int, timeout_s: float) -> ScenarioRun:
    """One attempt that can never raise: timeouts and crashes become an aborted run (-> unverified)."""
    try:
        return await asyncio.wait_for(
            run_scenario(scn, browser, sitemap, state, attempt=attempt, evidence_dir=evidence_dir), timeout_s)
    except asyncio.TimeoutError:
        reason = f"orchestrator: scenario exceeded the {timeout_s:.0f}s wall-clock limit (timed out)"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        reason = f"orchestrator: executor raised {type(exc).__name__}: {exc}"
    log.warning("%s attempt %d: %s", scn.id, attempt, reason)
    return _aborted(scn, attempt, reason)


def _chaos_downgrade(scn: Scenario, verdict: ScenarioVerdict, injected: list) -> ScenarioVerdict:
    """A failure observed while chaos mode injected faults into the same attempt is not attributable
    to the product: report it as unverified, never as a defect."""
    if verdict.status != "failed" or not injected:
        return verdict
    sample = ", ".join(f"{m} {u}" for m, u in injected[:3])
    note = (f"inconclusive under chaos mode: {len(injected)} injected HTTP 503(s) during this attempt ({sample}); "
            f"underlying result was 'failed'. {verdict.judge_notes or ''}")
    return verdict.model_copy(update={"status": "unverified", "confidence": 0.2, "judge_notes": note[:1000]})


async def _restore(browser) -> list[str]:
    """Bring the browser back to a sane state between scenarios / before a retry. Never raises."""
    obs: list[str] = []
    ensure = getattr(browser, "ensure_alive", None)
    if ensure is not None:
        try:
            obs += await asyncio.wait_for(ensure(), 60)
        except Exception as exc:  # noqa: BLE001
            obs.append(f"browser repair failed: {type(exc).__name__}: {exc}")
            return obs
    if getattr(browser, "page", None) is None or not hasattr(browser, "recover"):
        return obs
    try:
        err = await asyncio.wait_for(error_state(browser), 10)
        if err:
            obs.append(f"page left in an error state ({err[:80]!r})")
            obs += await asyncio.wait_for(browser.recover(expect_logged_in=False), 30)
            if await asyncio.wait_for(error_state(browser), 10):
                await browser.new_context()
                obs.append("error state persisted; opened a new browser context")
        remember = getattr(browser, "remember_session", None)
        if remember is not None:
            await remember()
    except Exception as exc:  # noqa: BLE001
        obs.append(f"recovery check failed: {type(exc).__name__}: {exc}")
    return obs


async def _run_one(scn: Scenario, browser, sitemap: SiteMap, state: StateManager, evidence_dir: Path,
                   timeout_s: float = DEFAULT_SCENARIO_TIMEOUT_S) -> tuple[ScenarioVerdict, ScenarioRun]:
    """Run a scenario, compensating after every attempt and retrying suspected flakes in a fresh context."""
    attempts: list[ScenarioVerdict] = []
    run: ScenarioRun | None = None
    try:
        start_url = await asyncio.wait_for(browser.current_url(), 5)
    except Exception:  # noqa: BLE001
        start_url = None
    for attempt in range(1, max(1, scn.max_attempts) + 1):
        notes: list[str] = []
        if attempt > 1:
            # Retry from a clean page: new context (cookies kept) reopened where the scenario started.
            try:
                await asyncio.wait_for(browser.new_context(fresh=False), 30)
                notes.append("retry: fresh browser context")
                if start_url and start_url.startswith("http"):
                    await asyncio.wait_for(browser.goto(start_url), 30)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"retry: new context failed ({type(exc).__name__})")
            notes += await _restore(browser)
        chaos = _chaos_of(browser)
        n0 = len(chaos.injected) if chaos is not None else 0
        run = await _attempt(scn, browser, sitemap, state, evidence_dir, attempt, timeout_s)
        injected = list(chaos.injected[n0:]) if chaos is not None else []
        if injected:
            run.observations["chaos_injected"] = [f"{m} {u}" for m, u in injected]
        if notes:
            run.observations["orchestrator"] = notes
        if scn.mutates_state:
            try:
                await asyncio.wait_for(state.compensate(run), 60)
            except Exception as exc:  # noqa: BLE001
                log.warning("compensation for %s failed: %s", scn.id, exc)
        try:
            verdict = evaluate(scn, run, sitemap)
        except Exception as exc:  # noqa: BLE001
            log.warning("verifier error on %s: %s", scn.id, exc)
            verdict = _unverified(scn, f"verifier error: {type(exc).__name__}: {exc}")
        verdict = _chaos_downgrade(scn, verdict, injected)
        attempts.append(verdict)
        try:
            again = needs_retry(verdict, run) or bool(injected and verdict.status != "passed")
        except Exception:  # noqa: BLE001
            again = False
        if not again:
            break
    try:
        verdict = merge_attempts(attempts) if len(attempts) > 1 else attempts[0]
    except Exception:  # noqa: BLE001
        verdict = attempts[-1]
    assert run is not None
    return verdict, run


def _parallel_safe(scn: Scenario) -> bool:
    """Read-only scenarios that cannot disturb the shared session or data."""
    if scn.phase != "read_only" or scn.mutates_state:
        return False
    if set(scn.preconditions) - {"logged_in"}:
        return False
    if any(st.action in _SESSION_ACTIONS for st in scn.steps):
        return False
    return not any(o.type in _SESSION_PROBES for o in scn.oracles)


class _Progress:
    def __init__(self, total: int) -> None:
        self.total, self.done, self.t0 = total, 0, time.monotonic()

    def line(self, scn: Scenario, verdict: ScenarioVerdict, secs: float, worker: str = "") -> None:
        self.done += 1
        elapsed = time.monotonic() - self.t0
        eta = elapsed / self.done * (self.total - self.done)
        colour = {"passed": "green", "failed": "red"}.get(verdict.status, "yellow")
        tag = f" ({worker})" if worker else ""
        retry = f" x{verdict.attempts}" if verdict.attempts > 1 else ""
        console.print(f"     [{self.done}/{self.total}] {scn.phase[:12]:<12} {scn.id:<22} [{colour}]{verdict.status}[/]"
                      f"{retry} {secs:.1f}s{tag} | {_fmt(elapsed)} ETA {_fmt(eta)}", soft_wrap=True)


def _fmt(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    return f"{m}m{s:02d}s"


async def execute_scenarios(scenarios: list[Scenario], browser, sitemap: SiteMap, state: StateManager,
                            evidence_dir: Path, *, run_id: str, parallel: int = 1,
                            timeout_s: float = DEFAULT_SCENARIO_TIMEOUT_S, on_result=None,
                            resilience: dict | None = None) -> tuple[list[ScenarioVerdict], dict[str, ScenarioRun]]:
    """Execute scenarios in phase order. Parallel-safe read-only ones may run on forked contexts.

    A scenario that raises, hangs or crashes the browser never stops the run: it is recorded as an
    aborted run (-> unverified) and the browser is repaired before the next one.
    """
    resilience = resilience if resilience is not None else {}
    resilience.setdefault("recoveries", [])
    progress = _Progress(len(scenarios))
    results: dict[str, tuple[ScenarioVerdict, ScenarioRun]] = {}

    async def one(scn: Scenario, b, st: StateManager, worker: str = "") -> None:
        t = time.monotonic()
        try:
            verdict, run = await _run_one(scn, b, sitemap, st, evidence_dir, timeout_s)
        except Exception as exc:  # noqa: BLE001  (last line of defence)
            run = _aborted(scn, 1, f"orchestrator: {type(exc).__name__}: {exc}")
            verdict = _unverified(scn, run.aborted_reason or "")
        results[scn.id] = (verdict, run)
        if on_result is not None:
            try:
                on_result(scn, verdict, run)
            except Exception as exc:  # noqa: BLE001
                log.warning("result hook failed for %s: %s", scn.id, exc)
        progress.line(scn, verdict, time.monotonic() - t, worker)
        obs = await _restore(b)
        if obs:
            resilience["recoveries"].append({"after": scn.id, "observations": obs})
            console.print(f"       [dim]recovered: {'; '.join(obs)[:160]}[/]")

    par: list[Scenario] = []
    if parallel > 1 and hasattr(browser, "fork"):
        par = [s for s in scenarios if _parallel_safe(s)]
    if len(par) < 2:
        par = []
    workers: list[tuple[object, StateManager]] = []
    for k in range(min(parallel, len(par))):
        try:
            w = await browser.fork(f"w{k + 1}")
            workers.append((w, StateManager(w, sitemap, run_id)))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not open parallel context: %s", exc)
            break
    if not workers:
        par = []
    if par:
        resilience["parallel_contexts"] = len(workers)
        resilience["parallel_scenarios"] = len(par)
        queue = list(par)

        async def worker_loop(w, st: StateManager) -> None:
            while queue:
                await one(queue.pop(0), w, st, getattr(w, "name", ""))

        try:
            await asyncio.gather(*(worker_loop(w, st) for w, st in workers))
        finally:
            for w, st in workers:
                state.ledger.extend(st.ledger)
                state.compensations.extend(st.compensations)
                chaos = _chaos_of(w)
                if chaos is not None:
                    resilience.setdefault("chaos_stats_workers", []).append(dict(chaos.stats))
                try:
                    await w.close()
                except Exception:  # noqa: BLE001
                    pass
    done = {s.id for s in par}
    for scn in scenarios:
        if scn.id not in done:
            await one(scn, browser, state)

    verdicts = [results[s.id][0] for s in scenarios]
    runs = {s.id: results[s.id][1] for s in scenarios}
    return verdicts, runs


async def run_pipeline(cfg: RunConfig) -> Path:
    run_id = new_run_id()
    run_dir = cfg.out_dir / run_id
    artifacts = run_dir / "artifacts"
    evidence_dir = run_dir / "evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    limitations: list[str] = []
    console.rule(f"[bold]QA run {run_id}")

    if cfg.reset_cmd:
        console.print(f"Resetting target state: {cfg.reset_cmd}")
        subprocess.run(shlex.split(cfg.reset_cmd), check=True)

    tracker = CostTracker(cfg.budget_usd)
    llm = ClaudeLLM(tracker, ArtifactCache(cfg.cache_dir, enabled=cfg.use_cache),
                    refusal_fallback=cfg.refusal_fallback)
    prd_text = cfg.prd_path.read_text(encoding="utf-8")

    chaos = ChaosConfig(cfg.chaos_latency_ms, cfg.chaos_error_rate, cfg.chaos_seed)
    source_routes: list[str] = []
    if cfg.repo:
        try:
            source_routes = discover_routes(resolve_repo(cfg.repo, cfg.cache_dir))
            console.print(f"     Source context: {len(source_routes)} client-side route(s) from {cfg.repo}")
        except Exception as exc:  # noqa: BLE001
            limitations.append(f"The --repo source could not be read ({exc}); exploration used links only.")
    browser = await launch(cfg.target_url, headed=cfg.headed, evidence_dir=evidence_dir, llm=llm)
    try:
        # 1-2. Spec Analyst and Explorer run concurrently.
        console.print("[cyan]1/6[/] Extracting requirements and exploring the site…")
        reqs, sitemap = await asyncio.gather(extract_requirements(prd_text, llm), explore(browser, llm, source_routes))
        browser.sitemap = sitemap
        _write(artifacts / "requirements.json", reqs)
        _write(artifacts / "sitemap.json", sitemap)
        console.print(f"     {len(reqs.requirements)} requirements, {len(sitemap.pages)} pages")

        # 3. Planner.
        console.print("[cyan]2/6[/] Planning scenarios…")
        plan = await plan_tests(reqs, sitemap, llm)
        _write(artifacts / "plan.json", plan)
        scenarios, notes = _select(plan, cfg)
        limitations += notes
        console.print(f"     {len(plan.scenarios)} scenarios planned, {len(scenarios)} selected")

        # 4. Baseline.
        state = StateManager(browser, sitemap, run_id)
        await ensure_logged_in(browser, sitemap, state)
        baseline = await state.snapshot()

        # 5. Execute phase by phase (mutations serial: the target's data is shared).
        console.print("[cyan]3/6[/] Executing scenarios…")
        if chaos.enabled and hasattr(browser, "set_chaos"):
            await browser.set_chaos(chaos)
            console.print(f"     [magenta]{chaos.describe()}[/]")

        def save_run(scn: Scenario, verdict: ScenarioVerdict, run: ScenarioRun) -> None:
            _write(artifacts / "runs" / f"{scn.id}.json", run)

        resilience: dict = {}
        verdicts, runs = await execute_scenarios(
            scenarios, browser, sitemap, state, evidence_dir, run_id=run_id, parallel=cfg.parallel,
            timeout_s=cfg.scenario_timeout_s, on_result=save_run, resilience=resilience)
        if chaos.enabled and hasattr(browser, "set_chaos"):
            main_chaos = getattr(browser, "chaos", None)
            if main_chaos is not None:
                resilience["chaos_stats"] = dict(main_chaos.stats)
            await browser.set_chaos(None)
            limitations.append(
                f"Self-test {chaos.describe()}. Faults were injected only into the page's API traffic during "
                "scenario execution (not exploration, snapshots or compensation); failures observed in an "
                "attempt that received an injected error are reported as unverified, not as defects.")
        if resilience.get("recoveries"):
            limitations.append(f"The agent repaired the browser state {len(resilience['recoveries'])} time(s) "
                               "between scenarios (see environment.resilience).")
        if getattr(browser, "relaunches", 0):
            resilience["browser_relaunches"] = browser.relaunches
        aborted = [v.scenario_id for v, r in ((v, runs[v.scenario_id]) for v in verdicts)
                   if (r.aborted_reason or "").startswith("orchestrator:")]
        if aborted:
            limitations.append(f"{len(aborted)} scenario(s) crashed or exceeded the {cfg.scenario_timeout_s:.0f}s "
                               f"limit and are reported as unverified: {aborted}.")

        # 6. Reconcile shared state against the baseline.
        await ensure_logged_in(browser, sitemap, state)
        final = await state.snapshot()
        drift = {k: (baseline.balances.get(k), v) for k, v in final.balances.items()
                 if baseline.balances.get(k) != v}
        if drift:
            limitations.append(f"Balances differ from the start of the run after compensation: {drift}.")

        # 7. LLM review of judgment calls and failures.
        console.print("[cyan]4/6[/] Judging and triaging…")
        await judge_pending(verdicts, runs, llm)
        await triage_failures(verdicts, reqs, llm, plan=plan, runs=runs)
        req_verdicts = rollup(reqs, plan, verdicts)

        # 8. Report.
        console.print("[cyan]5/6[/] Writing the report…")
        finished = datetime.now(timezone.utc)
        report = build_report(
            run_id, cfg.target_url, prd_sha256(prd_text), started, finished, reqs, plan, verdicts,
            req_verdicts, state.ledger, state.compensations, tracker.report(),
            environment={
                "target_url": cfg.target_url,
                "api_base": sitemap.api_base,
                "timezone": sitemap.entities.get("timezone"),
                "models": {"reasoning": REASONING_MODEL, "fast": FAST_MODEL},
                "python": platform.python_version(),
                "platform": platform.platform(),
                "read_only": cfg.read_only,
                "repo": cfg.repo,
                "source_routes": source_routes,
                "chaos": chaos.as_dict() if chaos.enabled else None,
                "parallel": cfg.parallel,
                "scenario_timeout_s": cfg.scenario_timeout_s,
                "resilience": resilience,
            },
            limitations=limitations,
        )
        report = await add_executive_summary(report, llm)
        report.cost = tracker.report()  # include the summary call
    finally:
        await browser.close()

    written = render(report, run_dir, ("json", "md", "html"), evidence_base_dirs=[run_dir])
    try:
        written["pdf"] = await render_pdf(written["html"])
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF export failed: %s", exc)
    (run_dir / "cost.json").write_text(report.cost.model_dump_json(indent=2))

    s = report.summary.get("by_status", {})
    console.print(f"[cyan]6/6[/] Done. Requirements: {s}, defects: {report.summary.get('defects_total')}  Cost: ${report.cost.total_usd:.4f}")
    console.print(f"     Report: {written.get('html')}")
    return run_dir


def load_requirements(path: Path) -> RequirementSet:
    return RequirementSet.model_validate_json(path.read_text())
