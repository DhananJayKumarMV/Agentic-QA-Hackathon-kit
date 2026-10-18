"""Orchestrator robustness: crashes, hangs, retries and parallel read-only execution never kill the run."""
import asyncio

import pytest

from qa_agent import orchestrator as orch
from qa_agent.browser.chaos import Chaos, ChaosConfig
from qa_agent.contracts import Oracle, Scenario, ScenarioRun, ScenarioVerdict, SiteMap, Step
from qa_agent.state.snapshot import StateManager

from .fakes import FakeBrowser


def _scn(sid: str, **kw) -> Scenario:
    base = dict(id=sid, requirement_ids=["REQ-X-1"], polarity="positive", title=sid, rationale="r",
                steps=[Step(action="goto", value="/", note="open")],
                oracles=[Oracle(type="url_matches", args={"pattern": "/"})])
    base.update(kw)
    return Scenario(**base)


def _verdict(scn, run, sitemap):
    if run.aborted_reason:
        return ScenarioVerdict(scenario_id=scn.id, status="unverified", confidence=0.0, judge_notes=run.aborted_reason)
    return ScenarioVerdict(scenario_id=scn.id, status=run.observations.get("status", "passed"), confidence=0.9)


class ForkableFake(FakeBrowser):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.name = ""
        self.contexts = 0
        self.forks: list["ForkableFake"] = []
        self.closed = False

    async def new_context(self, fresh: bool = False) -> None:
        self.contexts += 1
        await super().new_context(fresh)

    async def fork(self, name: str) -> "ForkableFake":
        child = ForkableFake()
        child.name = name
        self.forks.append(child)
        return child

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def patched(monkeypatch):
    calls: list[tuple[str, int, str]] = []
    behaviour: dict[str, object] = {}

    async def fake_run(scn, browser, sitemap, state, *, attempt, evidence_dir):
        calls.append((scn.id, attempt, getattr(browser, "name", "")))
        b = behaviour.get(scn.id)
        if b == "raise":
            raise RuntimeError("boom")
        if b == "hang":
            await asyncio.sleep(10)
        if b == "flaky" and attempt == 1:
            return ScenarioRun(scenario_id=scn.id, attempt=attempt, aborted_reason="step 0 failed: Timeout 10000ms")
        if b == "fail":
            run = ScenarioRun(scenario_id=scn.id, attempt=attempt)
            run.observations["status"] = "failed"
            return run
        await asyncio.sleep(0.01)
        return ScenarioRun(scenario_id=scn.id, attempt=attempt)

    monkeypatch.setattr(orch, "run_scenario", fake_run)
    monkeypatch.setattr(orch, "evaluate", _verdict)
    return calls, behaviour


async def _execute(scenarios, browser, **kw):
    sm = SiteMap(base_url="http://app.test")
    state = StateManager(browser, sm, "qa-test")
    return await orch.execute_scenarios(scenarios, browser, sm, state, __import__("pathlib").Path("/tmp/qa-orch"),
                                        run_id="qa-test", **kw)


async def test_exception_and_timeout_do_not_kill_run(patched):
    calls, behaviour = patched
    behaviour.update({"S2": "raise", "S3": "hang"})
    scns = [_scn("S1"), _scn("S2", max_attempts=1), _scn("S3"), _scn("S4")]
    verdicts, runs = await _execute(scns, FakeBrowser(), parallel=1, timeout_s=0.2)
    status = {v.scenario_id: v.status for v in verdicts}
    assert status == {"S1": "passed", "S2": "unverified", "S3": "unverified", "S4": "passed"}
    assert "RuntimeError: boom" in runs["S2"].aborted_reason
    assert "timed out" in runs["S3"].aborted_reason
    assert [c[0] for c in calls].count("S3") == 2  # a timeout is transient: retried once
    assert set(runs) == {"S1", "S2", "S3", "S4"}


async def test_retry_gets_fresh_context(patched):
    calls, behaviour = patched
    behaviour["S1"] = "flaky"
    b = ForkableFake()
    verdicts, runs = await _execute([_scn("S1")], b, parallel=1)
    assert verdicts[0].status == "passed" and verdicts[0].attempts == 2
    assert b.contexts == 1
    assert "retry: fresh browser context" in runs["S1"].observations["orchestrator"]


async def test_parallel_read_only_uses_forked_contexts(patched):
    calls, behaviour = patched
    b = ForkableFake()
    ro = [_scn(f"R{i}") for i in range(5)]
    session = _scn("R-logout", steps=[Step(action="logout", note="sign out")])
    logged_out = _scn("R-anon", preconditions=["logged_out"])
    mut = _scn("M1", phase="positive_mutation", mutates_state=True)
    res: dict = {}
    verdicts, runs = await _execute(ro + [session, logged_out, mut], b, parallel=3, resilience=res)
    assert [v.scenario_id for v in verdicts] == [s.id for s in ro + [session, logged_out, mut]]
    assert all(v.status == "passed" for v in verdicts)
    by = {sid: who for sid, _, who in calls}
    assert {by[f"R{i}"] for i in range(5)} <= {"w1", "w2", "w3"}
    assert by["R-logout"] == by["R-anon"] == by["M1"] == ""  # session/data-affecting: main browser
    assert len(b.forks) == 3 and all(f.closed for f in b.forks)
    assert res["parallel_scenarios"] == 5


def test_parallel_safety_rules():
    assert orch._parallel_safe(_scn("a"))
    assert not orch._parallel_safe(_scn("b", mutates_state=True))
    assert not orch._parallel_safe(_scn("c", phase="negative_mutation"))
    assert not orch._parallel_safe(_scn("d", preconditions=["fresh_context"]))
    assert not orch._parallel_safe(_scn("e", steps=[Step(action="api_post", value="/x", note="n")]))
    assert not orch._parallel_safe(_scn("f", oracles=[Oracle(type="button_disabled_during", args={})]))


async def test_chaos_injected_failure_is_inconclusive(patched):
    calls, behaviour = patched
    behaviour["S1"] = "fail"
    b = FakeBrowser()
    b.chaos = Chaos(ChaosConfig(error_rate=1.0))
    orig = orch.run_scenario

    async def run_with_fault(scn, browser, *a, **kw):
        browser.chaos.injected.append(("GET", "http://api.test/api/accounts"))
        return await orig(scn, browser, *a, **kw)

    orch.run_scenario = run_with_fault
    try:
        verdicts, runs = await _execute([_scn("S1"), _scn("S2")], b, parallel=1)
    finally:
        orch.run_scenario = orig
    assert verdicts[0].status == "unverified" and "chaos" in verdicts[0].judge_notes
    assert verdicts[1].status == "passed"
    assert runs["S1"].observations["chaos_injected"]


async def test_failure_without_chaos_stays_failed(patched):
    calls, behaviour = patched
    behaviour["S1"] = "fail"
    verdicts, _ = await _execute([_scn("S1")], FakeBrowser(), parallel=1)
    assert verdicts[0].status == "failed"
