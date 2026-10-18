"""Verifier: turns ScenarioRuns into verdicts and rolls them up per requirement.

Deterministic first: every oracle is judged by `dsl.oracles` from recorded facts.
LLM calls are only used for `llm_judge` oracles (Haiku) and for a skeptical second
look at failures (Sonnet triage), which may downgrade a failure to unverified but
never upgrade anything to passed. Anything that cannot be established is
`unverified`, never `failed`: false positives cost more than misses.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from ..config import FAST_MODEL, REASONING_MODEL
from ..contracts import (
    Evidence, OracleResult, Requirement, RequirementSet, RequirementVerdict, Scenario,
    ScenarioRun, ScenarioVerdict, SiteMap, TestPlan,
)
from ..dsl.oracles import check_all, substitute
from ..llm.client import LLM, load_prompt
from ..llm.cost import BudgetExceeded

SEVERITY_RANK = {"critical": 3, "major": 2, "minor": 1}
JUDGE_MIN_CONFIDENCE = 0.7
_TRANSIENT = re.compile(r"timeout|timed out|net::|network|econn|socket|detached|target closed|navigation", re.I)


# ---------------------------------------------------------------------------
# Deterministic scenario verdicts
# ---------------------------------------------------------------------------


def _failed_steps(run: ScenarioRun | None) -> list[str]:
    if run is None:
        return []
    return [f"step {s.step_idx + 1} failed: {s.error or 'unknown error'}" for s in run.steps if not s.ok]


def _status_from(results: list[OracleResult], run: ScenarioRun | None) -> tuple[str, str]:
    """Return (status, note) from oracle results and run facts."""
    if run is not None and run.aborted_reason:
        return "unverified", f"aborted: {run.aborted_reason}"
    if not results:
        return "unverified", "no oracles to check"
    fails = [r for r in results if r.status == "fail"]
    errors = [r for r in results if r.status == "error"]
    skipped = [r for r in results if r.status == "skipped"]
    step_errs = _failed_steps(run)
    if fails:
        if step_errs:
            return "unverified", ("oracle failure coincides with a step error, not trusted: "
                                  + "; ".join(step_errs[:3]))
        return "failed", "; ".join(f"{r.oracle.type}: expected {r.expected}; observed {r.observed}" for r in fails)
    notes = []
    if step_errs:
        notes.append("; ".join(step_errs[:3]))
    for r in errors:
        notes.append(f"{r.oracle.type} {r.observed}")
    for r in skipped:
        notes.append(f"{r.oracle.type}: {r.observed}")
    if notes:
        return "unverified", "; ".join(notes)
    return "passed", "all oracles passed"


def _confidence(status: str, results: list[OracleResult], flaky: bool = False) -> float:
    if status == "failed":
        fails = [r for r in results if r.status == "fail"]
        base = 0.95 if any(r.layer in ("api", "both") for r in fails) else 0.85
        if all(r.oracle.type == "llm_judge" for r in fails):
            base = 0.75
        return round(base - (0.2 if flaky else 0.0), 2)
    if status == "passed":
        n = len(results)
        return round(min(0.97, 0.85 + 0.04 * (n - 1)), 2)
    return 0.3


def render_repro_steps(scenario: Scenario, run: ScenarioRun | None) -> list[str]:
    params = dict(run.resolved_params) if run else {}

    def sub(s: str) -> str:
        try:
            return substitute(s, params)
        except Exception:  # noqa: BLE001 - unresolved placeholders stay visible
            return s

    out: list[str] = []
    pre = [p.replace("_", " ") for p in scenario.preconditions]
    if pre:
        out.append("Precondition: " + ", ".join(pre))
    for st in scenario.steps:
        line = sub(st.note)
        if st.value not in (None, ""):
            v = sub(st.value)
            if v not in line:
                line += f' (value: "{v}")'
        out.append(line)
    return out


def evaluate(scenario: Scenario, run: ScenarioRun, sitemap: SiteMap | None) -> ScenarioVerdict:
    results = check_all(scenario, run, sitemap)
    status, note = _status_from(results, run)
    return ScenarioVerdict(
        scenario_id=scenario.id,
        status=status,
        confidence=_confidence(status, results),
        oracle_results=results,
        attempts=run.attempt,
        judge_notes=note,
        repro_steps=render_repro_steps(scenario, run),
    )


# ---------------------------------------------------------------------------
# Retries and flakiness
# ---------------------------------------------------------------------------


def needs_retry(verdict: ScenarioVerdict, run: ScenarioRun) -> bool:
    """True when a non-passing verdict may be an artifact of the environment."""
    if verdict.status == "passed":
        return False
    if run.aborted_reason:
        return bool(_TRANSIENT.search(run.aborted_reason)) or "foreign" in run.aborted_reason.lower()
    if run.foreign_activity:
        return True
    if any(not s.ok for s in run.steps):
        return True
    if any(s.healed for s in run.steps) and verdict.status == "failed":
        return True
    for ev in run.network:
        if ev.status >= 500:
            return True
        if ev.status <= 0 and ev.method.upper() != "GET":
            return True
    if any(r.status == "error" and _TRANSIENT.search(r.observed) for r in verdict.oracle_results):
        return True
    return False


def merge_attempts(verdicts: list[ScenarioVerdict]) -> ScenarioVerdict:
    """Combine verdicts of repeated attempts of one scenario (in attempt order)."""
    if not verdicts:
        raise ValueError("merge_attempts needs at least one verdict")
    if len(verdicts) == 1:
        return verdicts[0]
    n = len(verdicts)
    statuses = [v.status for v in verdicts]
    passed = [v for v in verdicts if v.status == "passed"]
    failed = [v for v in verdicts if v.status == "failed"]
    if passed and failed:
        base = failed[-1]
        return base.model_copy(update={
            "status": "unverified", "flaky": True, "attempts": n, "confidence": 0.3,
            "judge_notes": f"flaky: attempts disagree ({', '.join(statuses)}); last failure: {base.judge_notes}",
        })
    if failed:
        base = failed[-1]
        conf = min(0.99, base.confidence + 0.03 * (len(failed) - 1))
        return base.model_copy(update={"attempts": n, "confidence": round(conf, 2)})
    if passed:
        return passed[-1].model_copy(update={"attempts": n})
    return verdicts[-1].model_copy(update={"attempts": n})


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------


class JudgeOut(BaseModel):
    verdict: Literal["pass", "fail", "unsure"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class TriageOut(BaseModel):
    confirmed: bool
    rationale: str
    expected: str
    observed: str
    severity: Literal["critical", "major", "minor"]
    violated_requirement_ids: list[str] = Field(
        default_factory=list,
        description="When confirmed: ids (from REQUIREMENT(S)) whose PRD quote the defect actually violates.")


def _runs_by_id(runs: Any) -> dict[str, ScenarioRun]:
    if runs is None:
        return {}
    if isinstance(runs, dict):
        items: Iterable[ScenarioRun] = runs.values()
    else:
        items = runs
    out: dict[str, ScenarioRun] = {}
    for r in items:
        if isinstance(r, list):
            for rr in r:
                if rr.scenario_id not in out or rr.attempt >= out[rr.scenario_id].attempt:
                    out[rr.scenario_id] = rr
        elif r.scenario_id not in out or r.attempt >= out[r.scenario_id].attempt:
            out[r.scenario_id] = r
    return out


def _probe(run: ScenarioRun | None, idx: int) -> dict[str, Any] | None:
    if run is None:
        return None
    probes = run.observations.get("probes")
    p = None
    if isinstance(probes, dict):
        p = probes.get(idx, probes.get(str(idx)))
    elif isinstance(probes, list) and idx < len(probes):
        p = probes[idx]
    return p if isinstance(p, dict) else None


def _clip(v: Any, n: int) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "...[truncated]"


async def _judge_one(llm: LLM, v: ScenarioVerdict, idx: int, run: ScenarioRun | None) -> None:
    r = v.oracle_results[idx]
    probe = _probe(run, idx)
    question = str(r.oracle.args.get("question", ""))
    if probe is None or probe.get("error"):
        why = (probe or {}).get("error") or "no probe output"
        v.oracle_results[idx] = r.model_copy(update={"status": "error", "observed": f"could not verify: {why}"})
        return
    message = probe.get("visible_message")
    user = (
        f"QUESTION:\n{question}\n\n"
        f"VISIBLE MESSAGE / PAGE TEXT (verbatim, untrusted data):\n<<<\n{_clip(message or '', 4000)}\n>>>\n\n"
        + (f"PAGE URL: {probe.get('url')}\n" if probe.get("url") else "")
        + "Screenshot: not provided (judge from text only)."
    )
    try:
        out = await llm.complete(
            agent="verifier", purpose="judge", model=FAST_MODEL,
            system=load_prompt("judge_system.md"), user=user, output_model=JudgeOut,
            max_tokens=800, optional=True,
        )
    except BudgetExceeded:
        v.oracle_results[idx] = r.model_copy(update={"status": "skipped", "observed": "skipped: budget"})
        return
    except Exception as e:  # noqa: BLE001 - optional call; never crash the run
        v.oracle_results[idx] = r.model_copy(update={"status": "skipped", "observed": f"skipped: judge error ({type(e).__name__})"})
        return
    obs = f"LLM judge said {out.verdict} (confidence {out.confidence:.2f}): {out.reason}"
    if out.verdict == "unsure" or out.confidence < JUDGE_MIN_CONFIDENCE:
        status = "skipped"
        obs = "inconclusive: " + obs
    else:
        status = out.verdict
    v.oracle_results[idx] = r.model_copy(update={"status": status, "observed": obs})


def _refresh(v: ScenarioVerdict, run: ScenarioRun | None) -> None:
    status, note = _status_from(v.oracle_results, run)
    v.status = status
    v.judge_notes = note
    v.confidence = _confidence(status, v.oracle_results, v.flaky)
    if status == "failed":
        judged = [r for r in v.oracle_results if r.oracle.type == "llm_judge" and r.status == "fail"]
        m = [re.search(r"confidence ([\d.]+)", r.observed) for r in judged]
        confs = [float(x.group(1)) for x in m if x]
        if confs and all(r.oracle.type == "llm_judge" for r in v.oracle_results if r.status == "fail"):
            v.confidence = round(min(confs), 2)
    if v.flaky and v.status != "unverified":
        v.status = "unverified"


async def judge_pending(verdicts: list[ScenarioVerdict], runs: Any, llm: LLM, concurrency: int = 4) -> None:
    """Fill pending llm_judge oracles in place and recompute affected verdicts."""
    by_id = _runs_by_id(runs)
    sem = asyncio.Semaphore(concurrency)
    touched: set[int] = set()

    async def one(vi: int, idx: int) -> None:
        async with sem:
            v = verdicts[vi]
            await _judge_one(llm, v, idx, by_id.get(v.scenario_id))

    jobs = []
    for vi, v in enumerate(verdicts):
        run = by_id.get(v.scenario_id)
        if run is not None and run.aborted_reason:
            continue
        for idx, r in enumerate(v.oracle_results):
            if r.oracle.type == "llm_judge" and r.status == "skipped" and r.observed == "pending LLM judge":
                jobs.append(one(vi, idx))
                touched.add(vi)
    await asyncio.gather(*jobs)
    for vi in touched:
        _refresh(verdicts[vi], by_id.get(verdicts[vi].scenario_id))


# ---------------------------------------------------------------------------
# Triage of failures
# ---------------------------------------------------------------------------


def _req_list(reqs: Any) -> list[Requirement]:
    if isinstance(reqs, RequirementSet):
        return list(reqs.requirements)
    return list(reqs or [])


def _reqs_for(scenario_id: str, reqs: list[Requirement], plan: TestPlan | None) -> list[Requirement]:
    if plan is not None:
        for s in plan.scenarios:
            if s.id == scenario_id:
                ids = set(s.requirement_ids)
                return [r for r in reqs if r.id in ids]
        ids = {rid for rid, sids in plan.coverage.items() if scenario_id in sids}
        if ids:
            return [r for r in reqs if r.id in ids]
    # Fallback: requirement id body (after its first prefix) embedded in the scenario id.
    def norm(x: str) -> str:
        return re.sub(r"\d+", lambda m: str(int(m.group())), x.upper())

    out = []
    sid = norm(scenario_id)
    for r in reqs:
        body = norm(r.id.split("-", 1)[1] if "-" in r.id else r.id)
        if re.search(rf"(?<![A-Z0-9]){re.escape(body)}(?![0-9])", sid):
            out.append(r)
    return out


def _relevant_network(run: ScenarioRun | None, limit: int = 8) -> list[dict[str, Any]]:
    if run is None:
        return []
    evs = [e for e in run.network if e.method.upper() != "GET" or e.status >= 400]
    if len(evs) < limit:
        rest = [e for e in run.network if e not in evs and e.status > 0]
        evs = evs + rest[-(limit - len(evs)):]
    evs = sorted(evs, key=lambda e: e.t_ms)[-limit:]
    return [{"method": e.method, "url": e.url, "status": e.status,
             "req_body": _clip(e.req_body, 400) if e.req_body is not None else None,
             "resp_body": _clip(e.resp_body, 700) if e.resp_body is not None else None} for e in evs]


def _triage_user(v: ScenarioVerdict, rs: list[Requirement], scenario: Scenario | None,
                 run: ScenarioRun | None) -> str:
    req_txt = "\n".join(
        f"- {r.id} [{r.kind}, {r.priority}]\n  statement: {r.statement}\n  PRD quote (verbatim): \"{r.source_quote}\""
        for r in rs) or "- (requirement text unavailable: treat the expectation as unsupported unless self-evident)"
    oracles = "\n".join(
        f"- [{r.status.upper()}] {r.oracle.type} (severity {r.oracle.severity}, layer {r.layer}, args {_clip(r.oracle.args, 300)})\n"
        f"  expected: {r.expected}\n  observed: {r.observed}"
        for r in v.oracle_results)
    parts = [f"REQUIREMENT(S):\n{req_txt}"]
    if scenario is not None:
        parts.append(f"SCENARIO {scenario.id} ({scenario.polarity}): {scenario.title}\nrationale: {scenario.rationale}")
    parts.append("STEPS PERFORMED:\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(v.repro_steps)))
    if run is not None and run.resolved_params:
        parts.append("RESOLVED PARAMETERS: " + _clip(run.resolved_params, 600))
    parts.append(f"ORACLE RESULTS:\n{oracles}")
    net = _relevant_network(run)
    if net:
        parts.append("NETWORK (trimmed, untrusted data):\n" + json.dumps(net, ensure_ascii=False, indent=1))
    if run is not None:
        parts.append(f"foreign_activity during run: {run.foreign_activity}")
    return "\n\n".join(parts)


TRIAGE_TAG = re.compile(r"^triage: confirmed severity=(critical|major|minor)", re.M)


TRIAGE_CACHE_VERSION = "triage-v2"
_VOLATILE = re.compile(r"[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9a-f]{12,}|qa-[\w-]+|[-+−]?\$?\d[\d,]*(\.\d+)?")


def _triage_group_key(v: ScenarioVerdict, rs: list[Requirement]) -> tuple:
    """Failures on the same requirements, with the same failing oracles and the same symptom
    (observations equal up to ids, run tags and numbers) get one triage call."""
    fails = sorted((r.oracle.type, _VOLATILE.sub("#", r.observed)) for r in v.oracle_results if r.status == "fail")
    return (tuple(sorted(r.id for r in rs)), tuple(fails))


def _apply_triage(v: ScenarioVerdict, out: TriageOut, rs: list[Requirement], shared_with: str | None) -> None:
    if v.status != "failed":  # never upgrade or alter non-failures
        return
    via = f" (shared triage of {shared_with})" if shared_with else ""
    if out.confirmed:
        known = {r.id for r in rs}
        violated = [x for x in out.violated_requirement_ids if x in known]
        attr = f"violates: {', '.join(violated)}\n" if violated and len(violated) < len(known) else ""
        v.judge_notes = (f"triage: confirmed severity={out.severity}\n{attr}expected: {out.expected}\n"
                         f"observed: {out.observed}\nrationale: {out.rationale}{via}\n---\n{v.judge_notes or ''}").strip()
    else:
        v.status = "unverified"
        v.confidence = 0.3
        v.judge_notes = f"triage: not confirmed{via}; {out.rationale}\n---\n{v.judge_notes or ''}".strip()


async def triage_failures(verdicts: list[ScenarioVerdict], reqs: Any, llm: LLM, *,
                          plan: TestPlan | None = None, runs: Any = None, concurrency: int = 3) -> None:
    """Skeptical second look at each failed scenario. May downgrade failed -> unverified only.

    Cost: identical failures are triaged once (see _triage_group_key); the first call runs alone
    so it writes the prompt cache that the concurrent rest then read; results are cached on disk.
    """
    req_list = _req_list(reqs)
    by_run = _runs_by_id(runs)
    scen = {s.id: s for s in plan.scenarios} if plan else {}
    sem = asyncio.Semaphore(concurrency)
    system = load_prompt("triage_system.md")

    groups: dict[tuple, list[tuple[ScenarioVerdict, list[Requirement]]]] = {}
    for v in verdicts:
        if v.status != "failed":
            continue
        rs = _reqs_for(v.scenario_id, req_list, plan)
        groups.setdefault(_triage_group_key(v, rs), []).append((v, rs))

    async def one(members: list[tuple[ScenarioVerdict, list[Requirement]]]) -> None:
        v, rs = members[0]
        user = _triage_user(v, rs, scen.get(v.scenario_id), by_run.get(v.scenario_id))
        async with sem:
            try:
                out = await llm.complete(
                    agent="verifier", purpose="triage", model=REASONING_MODEL, effort="low",
                    system=system, user=user, output_model=TriageOut, max_tokens=2000, optional=True,
                    cache_parts=(TRIAGE_CACHE_VERSION,),
                )
            except BudgetExceeded:
                for m, _ in members:
                    m.judge_notes = f"{m.judge_notes or ''}\ntriage skipped: budget".strip()
                return
            except Exception as e:  # noqa: BLE001 - optional; deterministic failure stands
                for m, _ in members:
                    m.judge_notes = f"{m.judge_notes or ''}\ntriage skipped: {type(e).__name__}".strip()
                return
        for i, (m, mrs) in enumerate(members):
            _apply_triage(m, out, mrs, v.scenario_id if i else None)

    jobs = list(groups.values())
    if not jobs:
        return
    await one(jobs[0])  # alone: writes the prompt cache for the rest
    await asyncio.gather(*(one(g) for g in jobs[1:]))


# ---------------------------------------------------------------------------
# Requirement roll-up
# ---------------------------------------------------------------------------


_VIOLATES = re.compile(r"^violates: (.*)$", re.M)


def _attributed_elsewhere(v: ScenarioVerdict | None, rid: str) -> bool:
    """True when v is a confirmed failure whose triage names violated requirements excluding rid."""
    if v is None or v.status != "failed":
        return False
    head = (v.judge_notes or "").split("\n---\n")[0]
    if not TRIAGE_TAG.search(head):
        return False
    m = _VIOLATES.search(head)
    if not m:
        return False
    ids = {x.strip() for x in m.group(1).split(",") if x.strip()}
    return bool(ids) and rid not in ids


def _triage_fields(v: ScenarioVerdict) -> dict[str, str] | None:
    notes = v.judge_notes or ""
    m = TRIAGE_TAG.search(notes)
    if not m:
        return None
    exp = re.search(r"^expected: (.*)$", notes, re.M)
    obs = re.search(r"^observed: (.*)$", notes, re.M)
    return {"severity": m.group(1), "expected": exp.group(1) if exp else "", "observed": obs.group(1) if obs else ""}


def _short_reason(v: ScenarioVerdict | None) -> str:
    if v is None:
        return "not executed"
    note = (v.judge_notes or v.status).split("\n---\n")[0].replace("\n", " ")
    if v.flaky:
        note = "flaky: " + note
    return note[:300]


def rollup(reqs: Any, plan: TestPlan, verdicts: list[ScenarioVerdict]) -> list[RequirementVerdict]:
    req_list = _req_list(reqs)
    by_vid: dict[str, ScenarioVerdict] = {}
    for v in verdicts:
        by_vid[v.scenario_id] = v
    scen_by_req: dict[str, list[Scenario]] = {}
    for s in plan.scenarios:
        for rid in s.requirement_ids:
            scen_by_req.setdefault(rid, []).append(s)
    by_sid = {s.id: s for s in plan.scenarios}
    for rid, sids in plan.coverage.items():
        lst = scen_by_req.setdefault(rid, [])
        for sid in sids:
            if sid in by_sid and by_sid[sid] not in lst:
                lst.append(by_sid[sid])

    out: list[RequirementVerdict] = []
    seen: set[str] = set()
    for r in req_list:
        if r.id in seen:
            continue
        seen.add(r.id)
        scens = scen_by_req.get(r.id, [])
        sids = [s.id for s in scens]
        if not scens:
            reason = ("manual_only: requires manual verification" if r.testability == "manual_only"
                      else "no scenarios planned")
            out.append(RequirementVerdict(requirement_id=r.id, status="unverified", reason=reason, scenario_ids=[]))
            continue
        # A confirmed failure that triage attributed to other requirements of a multi-requirement
        # scenario says nothing about this one: leave it out (unless it is the only evidence).
        unrelated = [s for s in scens if _attributed_elsewhere(by_vid.get(s.id), r.id)]
        if unrelated and len(unrelated) < len(scens):
            scens = [s for s in scens if s not in unrelated]
        vs = {s.id: by_vid.get(s.id) for s in scens}
        failed = [s for s in scens if vs[s.id] is not None and vs[s.id].status == "failed"
                  and s not in unrelated]
        if failed:
            out.append(_failed_verdict(r, sids, [(s, vs[s.id]) for s in failed]))
            continue
        all_passed = all(v is not None and v.status == "passed" for v in vs.values())
        pos_passed = any(s.polarity == "positive" and vs[s.id] is not None and vs[s.id].status == "passed" for s in scens)
        # Validation / scope / security requirements are verified by rejections, so a passing
        # negative scenario counts as the "positive" evidence for them.
        if not pos_passed and r.kind in ("validation", "negative_scope", "security"):
            pos_passed = any(vs[s.id] is not None and vs[s.id].status == "passed" for s in scens)
        # A requirement that is itself about an edge case (e.g. what an empty result shows) may only
        # get edge scenarios; when no positive scenario was planned, a passing edge scenario is the
        # direct evidence.
        if not pos_passed and not any(s.polarity == "positive" for s in scens):
            pos_passed = any(s.polarity == "edge" and vs[s.id] is not None and vs[s.id].status == "passed"
                             for s in scens)
        if all_passed and pos_passed:
            out.append(RequirementVerdict(
                requirement_id=r.id, status="passed", scenario_ids=sids,
                reason=f"all {len(scens)} scenario(s) passed: " + ", ".join(s.id for s in scens)
                + (f" ({', '.join(s.id for s in unrelated)} failed on another requirement)" if unrelated else "")))
            continue
        reasons = []
        if all_passed and not pos_passed:
            reasons.append("no positive scenario verified the requirement")
        for s in scens:
            v = vs[s.id]
            if v is None or v.status != "passed":
                reasons.append(f"{s.id}: {_short_reason(v)}")
        if r.testability == "manual_only":
            reasons.insert(0, "manual_only")
        out.append(RequirementVerdict(requirement_id=r.id, status="unverified", scenario_ids=sids,
                                      reason="; ".join(reasons)[:1000]))
    return out


def _failed_verdict(r: Requirement, sids: list[str], failed: list[tuple[Scenario, ScenarioVerdict]]) -> RequirementVerdict:
    expected, observed, reasons = [], [], []
    evidence: list[Evidence] = []
    sev_rank = 0
    for s, v in failed:
        fails = [x for x in v.oracle_results if x.status == "fail"]
        tri = _triage_fields(v)
        rank = max((SEVERITY_RANK[x.oracle.severity] for x in fails), default=SEVERITY_RANK["major"])
        if tri:
            rank = SEVERITY_RANK[tri["severity"]]
            expected.append(tri["expected"] or "; ".join(x.expected for x in fails))
            observed.append(tri["observed"] or "; ".join(x.observed for x in fails))
        else:
            expected.append("; ".join(x.expected for x in fails))
            observed.append("; ".join(x.observed for x in fails))
        sev_rank = max(sev_rank, rank)
        reasons.append(f"{s.id} ({s.polarity}) failed: " + ", ".join(x.oracle.type for x in fails))
        for x in fails:
            for e in x.evidence:
                if e not in evidence:
                    evidence.append(e)
    severity = {3: "critical", 2: "major", 1: "minor"}[sev_rank or 2]
    multi = len(failed) > 1
    def join(parts: list[str]) -> str:
        if not multi:
            return parts[0]
        return " | ".join(f"[{s.id}] {p}" for (s, _), p in zip(failed, parts))
    return RequirementVerdict(
        requirement_id=r.id, status="failed", reason="; ".join(reasons), scenario_ids=sids,
        expected=join(expected), observed=join(observed), evidence=evidence, severity=severity,
    )
