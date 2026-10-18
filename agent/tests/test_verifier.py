from qa_agent.agents.verifier import (
    evaluate, judge_pending, merge_attempts, needs_retry, rollup, triage_failures,
)
from qa_agent.contracts import (
    LlmCall, NetworkEvent, Oracle, Requirement, RequirementSet, Scenario, ScenarioRun, ScenarioVerdict,
    SiteMap, Snapshot, Step, StepResult, TestPlan,
)
from qa_agent.llm.client import FakeLLM
from qa_agent.llm.cost import CostTracker

SM = SiteMap(base_url="http://app.test", entities={"timezone": None, "accounts": []})


def scen(sid="SCN-A-1", oracles=None, polarity="positive", reqs=("REQ-A-001",), steps=None, params=None):
    return Scenario(
        id=sid, requirement_ids=list(reqs), polarity=polarity, title="t", rationale="r",
        steps=steps or [Step(action="goto", value="/transfer", note="Open the transfer page"),
                        Step(action="fill", value="{{amt}}", note="Enter amount {{amt}}"),
                        Step(action="fill", value="memo {{run_tag}}", note="Enter a memo")],
        oracles=oracles if oracles is not None else [Oracle(type="url_matches", args={"pattern": "^/ok"})],
    )


def run_for(s, probes=None, *, steps=None, aborted=None, foreign=False, network=None, params=None, attempt=1):
    return ScenarioRun(scenario_id=s.id, attempt=attempt, resolved_params=params or {"amt": "1.07", "run_tag": "R1"},
                       observations={"probes": probes or {}}, steps=steps or [], aborted_reason=aborted,
                       foreign_activity=foreign, network=network or [])


URL_OK = {"0": {"url": "http://x/ok"}}
URL_BAD = {"0": {"url": "http://x/bad"}}


# ---------------------------------------------------------------- evaluate

def test_evaluate_pass_fail_and_repro():
    s = scen()
    v = evaluate(s, run_for(s, URL_OK), SM)
    assert v.status == "passed" and v.confidence > 0.8
    assert v.repro_steps[-2] == "Enter amount 1.07"
    assert v.repro_steps[-1] == 'Enter a memo (value: "memo R1")'
    v = evaluate(s, run_for(s, URL_BAD), SM)
    assert v.status == "failed" and "expected URL path" in v.judge_notes


def test_evaluate_error_and_abort_are_unverified():
    s = scen()
    assert evaluate(s, run_for(s, {}), SM).status == "unverified"
    v = evaluate(s, run_for(s, URL_BAD, aborted="unresolvable param amt"), SM)
    assert v.status == "unverified" and "aborted" in v.judge_notes


def test_fail_with_step_error_is_unverified():
    s = scen()
    steps = [StepResult(step_idx=1, ok=False, duration_ms=5, error="Timeout 5000ms exceeded")]
    v = evaluate(s, run_for(s, URL_BAD, steps=steps), SM)
    assert v.status == "unverified" and "step 2 failed" in v.judge_notes


def test_fail_dominates_other_errors():
    s = scen(oracles=[Oracle(type="url_matches", args={"pattern": "^/ok"}),
                      Oracle(type="row_count_equals", args={"n": 1})])
    v = evaluate(s, run_for(s, URL_BAD), SM)
    assert v.status == "failed"


def test_llm_judge_pending_is_unverified():
    s = scen(oracles=[Oracle(type="url_matches", args={"pattern": "^/ok"}),
                      Oracle(type="llm_judge", args={"question": "q?", "inputs": ["visible_message"]})])
    v = evaluate(s, run_for(s, {**URL_OK, "1": {"visible_message": "m"}}), SM)
    assert v.status == "unverified"


def test_no_oracles_unverified():
    s = scen(oracles=[])
    assert evaluate(s, run_for(s), SM).status == "unverified"


# ---------------------------------------------------------------- retry / merge

def test_needs_retry():
    s = scen()
    clean_fail = run_for(s, URL_BAD)
    v = evaluate(s, clean_fail, SM)
    assert not needs_retry(v, clean_fail)
    assert needs_retry(v, run_for(s, URL_BAD, foreign=True))
    net = [NetworkEvent(method="POST", url="http://x/api/t", status=503, t_ms=1)]
    assert needs_retry(v, run_for(s, URL_BAD, network=net))
    net = [NetworkEvent(method="GET", url="http://x/api/me", status=-1, t_ms=1)]
    assert not needs_retry(v, run_for(s, URL_BAD, network=net))  # cancelled GETs are navigation noise
    steps = [StepResult(step_idx=0, ok=False, duration_ms=5, error="timeout")]
    r = run_for(s, URL_BAD, steps=steps)
    assert needs_retry(evaluate(s, r, SM), r)
    ok = run_for(s, URL_OK)
    assert not needs_retry(evaluate(s, ok, SM), ok)
    ab = run_for(s, aborted="unresolvable param x")
    assert not needs_retry(evaluate(s, ab, SM), ab)


def test_merge_attempts():
    s = scen()
    f = evaluate(s, run_for(s, URL_BAD), SM)
    p = evaluate(s, run_for(s, URL_OK, attempt=2), SM)
    m = merge_attempts([f, p])
    assert m.status == "unverified" and m.flaky and m.attempts == 2 and "flaky" in m.judge_notes
    m = merge_attempts([f, f.model_copy()])
    assert m.status == "failed" and not m.flaky and m.attempts == 2
    u = evaluate(s, run_for(s, {}), SM)
    m = merge_attempts([u, f])
    assert m.status == "failed" and not m.flaky


# ---------------------------------------------------------------- judge

def judge_scenario():
    s = scen(oracles=[Oracle(type="llm_judge", args={"question": "Does it explain the rejection?"})])
    r = run_for(s, {"0": {"visible_message": "Amount must be greater than zero"}})
    return s, r, evaluate(s, r, SM)


async def test_judge_pass():
    s, r, v = judge_scenario()
    llm = FakeLLM({("verifier", "judge"): {"verdict": "pass", "confidence": 0.9, "reason": "explains"}})
    await judge_pending([v], [r], llm)
    assert v.status == "passed" and v.oracle_results[0].status == "pass"
    assert llm.calls[0]["model"] == "claude-haiku-4-5"


async def test_judge_low_confidence_unverified():
    s, r, v = judge_scenario()
    llm = FakeLLM({("verifier", "judge"): {"verdict": "fail", "confidence": 0.6, "reason": "hmm"}})
    await judge_pending([v], {s.id: r}, llm)
    assert v.status == "unverified"


async def test_judge_fail_confident():
    s, r, v = judge_scenario()
    llm = FakeLLM({("verifier", "judge"): {"verdict": "fail", "confidence": 0.85, "reason": "no reason given"}})
    await judge_pending([v], [r], llm)
    assert v.status == "failed" and v.confidence == 0.85


async def test_judge_budget_exceeded():
    s, r, v = judge_scenario()
    t = CostTracker(budget_usd=1.0)
    t.record(LlmCall(agent="x", model="claude-haiku-4-5", purpose="p", usd=0.9))
    llm = FakeLLM({("verifier", "judge"): {"verdict": "pass", "confidence": 0.9, "reason": "x"}}, tracker=t)
    await judge_pending([v], [r], llm)
    assert v.status == "unverified" and "budget" in v.oracle_results[0].observed
    assert not llm.calls


# ---------------------------------------------------------------- triage

REQS = RequirementSet(prd_sha256="0", prompt_version="v", requirements=[
    Requirement(id="REQ-A-001", area="transfers", statement="s1", source_quote="Amount must be positive",
                kind="validation", priority="P0", testability="automatable"),
    Requirement(id="REQ-B-001", area="history", statement="s2", source_quote="Dates are inclusive",
                kind="functional", priority="P1", testability="automatable"),
    Requirement(id="REQ-C-001", area="ui", statement="s3", source_quote="Looks nice",
                kind="ux", priority="P2", testability="manual_only"),
])


async def test_triage_downgrade_only():
    s = scen()
    f = evaluate(s, run_for(s, URL_BAD), SM)
    p = evaluate(s, run_for(s, URL_OK), SM)
    llm = FakeLLM({("verifier", "triage"): {"confirmed": False, "rationale": "PRD does not say that",
                                            "expected": "", "observed": "", "severity": "minor"}})
    plan = TestPlan(requirement_set_sha="a", sitemap_sha="b", scenarios=[s])
    await triage_failures([f, p], REQS, llm, plan=plan)
    assert f.status == "unverified" and "not confirmed" in f.judge_notes
    assert p.status == "passed"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and "Amount must be positive" in call["user"]


async def test_triage_confirmed_keeps_failed_and_sets_severity():
    s = scen()
    f = evaluate(s, run_for(s, URL_BAD), SM)
    llm = FakeLLM({("verifier", "triage"): {"confirmed": True, "rationale": "clear", "expected": "E1",
                                            "observed": "O1", "severity": "critical"}})
    await triage_failures([f], REQS, llm)  # no plan: requirement inferred from scenario id
    assert f.status == "failed"
    assert "Amount must be positive" in llm.calls[0]["user"]
    plan = TestPlan(requirement_set_sha="a", sitemap_sha="b", scenarios=[s])
    rv = {x.requirement_id: x for x in rollup(REQS, plan, [f])}["REQ-A-001"]
    assert rv.status == "failed" and rv.severity == "critical" and rv.expected == "E1" and rv.observed == "O1"


async def test_triage_never_upgrades_unverified_and_budget_keeps_failed():
    s = scen()
    f = evaluate(s, run_for(s, URL_BAD), SM)
    t = CostTracker(budget_usd=1.0)
    t.record(LlmCall(agent="x", model="claude-haiku-4-5", purpose="p", usd=0.95))
    llm = FakeLLM({("verifier", "triage"): {"confirmed": True, "rationale": "", "expected": "", "observed": "",
                                            "severity": "major"}}, tracker=t)
    await triage_failures([f], REQS, llm)
    assert f.status == "failed" and "budget" in f.judge_notes


# ---------------------------------------------------------------- rollup

def mkv(sid, status, oracle_sev="major", ostatus=None):
    o = Oracle(type="url_matches", args={"pattern": "x"}, severity=oracle_sev)
    from qa_agent.contracts import OracleResult
    res = OracleResult(oracle=o, status=ostatus or {"passed": "pass", "failed": "fail", "unverified": "error"}[status],
                       expected="exp-" + sid, observed="obs-" + sid,
                       evidence=[{"kind": "screenshot", "path": f"{sid}.png", "caption": "c"}])
    return ScenarioVerdict(scenario_id=sid, status=status, confidence=0.9, oracle_results=[res],
                           judge_notes=f"note-{sid}")


def plan_of(*scs):
    return TestPlan(requirement_set_sha="a", sitemap_sha="b", scenarios=list(scs))


def test_rollup_every_requirement_once():
    plan = plan_of(scen("S1"))
    out = rollup(REQS, plan, [mkv("S1", "passed")])
    assert [r.requirement_id for r in out] == ["REQ-A-001", "REQ-B-001", "REQ-C-001"]
    by = {r.requirement_id: r for r in out}
    assert by["REQ-A-001"].status == "passed"
    assert by["REQ-B-001"].status == "unverified" and "no scenarios" in by["REQ-B-001"].reason
    assert by["REQ-C-001"].status == "unverified" and "manual_only" in by["REQ-C-001"].reason


def test_rollup_failed_aggregates_and_severity():
    plan = plan_of(scen("S1"), scen("S2", polarity="negative"))
    out = rollup(REQS, plan, [mkv("S1", "passed"), mkv("S2", "failed", "critical")])[0]
    assert out.status == "failed" and out.severity == "critical"
    assert out.expected == "exp-S2" and out.observed == "obs-S2" and out.evidence[0].path == "S2.png"
    out = rollup(REQS, plan, [mkv("S1", "failed", "minor"), mkv("S2", "failed", "minor")])[0]
    assert out.status == "failed" and out.severity == "minor" and "[S1]" in out.expected


def test_rollup_negative_unverified_blocks_pass():
    plan = plan_of(scen("S1"), scen("S2", polarity="edge"))
    out = rollup(REQS, plan, [mkv("S1", "passed"), mkv("S2", "unverified")])[0]
    assert out.status == "unverified" and "S2" in out.reason and "note-S2" in out.reason


def test_rollup_requires_positive_for_functional():
    reqs = RequirementSet(prd_sha256="0", prompt_version="v", requirements=[REQS.requirements[1]])
    plan = plan_of(scen("S1", polarity="negative", reqs=("REQ-B-001",)))
    out = rollup(reqs, plan, [mkv("S1", "passed")])[0]
    assert out.status == "unverified" and "no positive" in out.reason


def test_rollup_validation_passes_on_negative_only():
    plan = plan_of(scen("S1", polarity="negative"))
    assert rollup(REQS, plan, [mkv("S1", "passed")])[0].status == "passed"


def test_rollup_missing_verdict_and_flaky():
    plan = plan_of(scen("S1"), scen("S2"))
    flaky = mkv("S2", "unverified").model_copy(update={"flaky": True})
    out = rollup(REQS, plan, [flaky])[0]
    assert out.status == "unverified" and "not executed" in out.reason and "flaky" in out.reason


async def test_triage_attribution_limits_failure_to_violated_requirement():
    # One scenario covers A and B; triage says only B's rule is violated. A has another passing scenario.
    s_multi = scen("S-MULTI", reqs=("REQ-A-001", "REQ-B-001"))
    s_a = scen("S-A")
    f = evaluate(s_multi, run_for(s_multi, URL_BAD), SM)
    p = evaluate(s_a, run_for(s_a, URL_OK), SM)
    llm = FakeLLM({("verifier", "triage"): {"confirmed": True, "rationale": "r", "expected": "E", "observed": "O",
                                            "severity": "major", "violated_requirement_ids": ["REQ-B-001", "REQ-Z"]}})
    plan = plan_of(s_multi, s_a)
    await triage_failures([f, p], REQS, llm, plan=plan)
    assert f.status == "failed" and "violates: REQ-B-001" in f.judge_notes
    by = {x.requirement_id: x for x in rollup(REQS, plan, [f, p])}
    assert by["REQ-B-001"].status == "failed"
    assert by["REQ-A-001"].status == "passed" and "S-A" in by["REQ-A-001"].reason
    # Only evidence for a requirement and attributed elsewhere: unverified, never failed or passed.
    plan2 = plan_of(s_multi)
    by2 = {x.requirement_id: x for x in rollup(REQS, plan2, [f])}
    assert by2["REQ-A-001"].status == "unverified" and by2["REQ-B-001"].status == "failed"


def test_rollup_edge_only_requirement_passes_but_negative_only_functional_does_not():
    reqs = RequirementSet(prd_sha256="0", prompt_version="v", requirements=[REQS.requirements[1]])
    plan = plan_of(scen("S1", polarity="edge", reqs=("REQ-B-001",)))
    assert rollup(reqs, plan, [mkv("S1", "passed")])[0].status == "passed"
    plan = plan_of(scen("S1", polarity="edge", reqs=("REQ-B-001",)), scen("S2", reqs=("REQ-B-001",)))
    assert rollup(reqs, plan, [mkv("S1", "passed"), mkv("S2", "unverified")])[0].status == "unverified"


async def test_triage_dedupes_identical_failures_and_uses_cheap_cached_call():
    s1, s2, s3 = scen("S1"), scen("S2"), scen("S3", reqs=("REQ-B-001",))
    f1 = evaluate(s1, run_for(s1, {"0": {"url": "http://x/bad-1111"}}), SM)
    f2 = evaluate(s2, run_for(s2, {"0": {"url": "http://x/bad-2222"}}), SM)  # same symptom up to numbers
    f3 = evaluate(s3, run_for(s3, URL_BAD), SM)  # other requirement
    llm = FakeLLM({("verifier", "triage"): {"confirmed": False, "rationale": "not in PRD",
                                            "expected": "", "observed": "", "severity": "minor"}})
    await triage_failures([f1, f2, f3], REQS, llm, plan=plan_of(s1, s2, s3))
    assert len(llm.calls) == 2
    assert all(v.status == "unverified" for v in (f1, f2, f3))
    assert "shared triage of S1" in f2.judge_notes
