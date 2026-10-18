"""Reporter: turns verdicts, the state ledger and cost accounting into the audit report.

`build_report` is deterministic and makes no LLM calls. `add_executive_summary` can
optionally ask the fast model for a prose summary (optional call, budget-guarded).
`render` writes report.json / report.md / report.html; `render_pdf` prints the HTML
with Playwright.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jinja2 import Environment, FileSystemLoader

from ..contracts import (
    AuditReport,
    Compensation,
    CostReport,
    Evidence,
    LedgerEntry,
    LlmCall,
    RequirementSet,
    RequirementVerdict,
    ScenarioVerdict,
    TestPlan,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "report_templates"

STATUSES = ("passed", "failed", "unverified")
POLARITIES = ("positive", "negative", "edge")
SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
PRIORITY_DEFAULT_SEVERITY = {"P0": "critical", "P1": "major", "P2": "minor"}
# Worst status wins when several scenarios cover one cell of the coverage matrix.
STATUS_RANK = {"failed": 0, "unverified": 1, "passed": 2}
MAX_EMBED_BYTES = 3_000_000
COST_FIELDS = ("calls", "input_tokens", "output_tokens", "cache_read", "cache_write", "usd", "artifact_cache_hits")


# --------------------------------------------------------------------------- build


def _norm_symptom(text: str) -> str:
    """Normalize an observed-behaviour string so equal symptoms with different values merge."""
    t = text.lower()
    t = re.sub(r"\d+([.,]\d+)*", "#", t)
    t = re.sub(r"[^a-z#]+", " ", t)
    return " ".join(t.split())


def _worst(statuses: Iterable[str]) -> str | None:
    ss = [s for s in statuses if s in STATUS_RANK]
    return min(ss, key=STATUS_RANK.__getitem__) if ss else None


def _cost_buckets(calls: list[LlmCall], key: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: {f: 0.0 for f in COST_FIELDS})
    for c in calls:
        b = out[getattr(c, key)]
        b["calls"] += 1
        b["input_tokens"] += c.input_tokens
        b["output_tokens"] += c.output_tokens
        b["cache_read"] += c.cache_read
        b["cache_write"] += c.cache_write
        b["usd"] += c.usd
        b["artifact_cache_hits"] += int(c.cached_artifact_hit)
    return {k: dict(v) for k, v in out.items()}


def cost_summary(cost: CostReport) -> dict[str, Any]:
    """Totals plus a cold-vs-warm estimate. Recomputes buckets from calls when present."""
    calls = cost.calls
    by_agent = _cost_buckets(calls, "agent") if calls else {k: dict(v) for k, v in cost.by_agent.items()}
    by_model = _cost_buckets(calls, "model") if calls else {k: dict(v) for k, v in cost.by_model.items()}
    totals = {f: 0.0 for f in COST_FIELDS}
    for b in by_agent.values():
        for f in COST_FIELDS:
            totals[f] += float(b.get(f, 0.0))
    total_usd = sum(c.usd for c in calls) if calls else cost.total_usd
    totals["usd"] = total_usd

    # Cold estimate: each artifact-cache hit would have cost about the average live call
    # of the same agent+purpose (else same agent, else overall).
    live = [c for c in calls if not c.cached_artifact_hit]
    def _avg(cs: list[LlmCall]) -> float | None:
        return sum(c.usd for c in cs) / len(cs) if cs else None
    overall = _avg(live) or 0.0
    hits = [c for c in calls if c.cached_artifact_hit]
    est_extra = 0.0
    for h in hits:
        same = _avg([c for c in live if c.agent == h.agent and c.purpose == h.purpose])
        if same is None:
            same = _avg([c for c in live if c.agent == h.agent])
        est_extra += same if same is not None else overall
    n_calls = len(calls) if calls else int(totals["calls"])
    agent_models: dict[str, list[str]] = defaultdict(list)
    for c in calls:
        if c.model not in agent_models[c.agent]:
            agent_models[c.agent].append(c.model)
    return {
        "agent_models": {k: sorted(v) for k, v in agent_models.items()},
        "share": {k: (float(b.get("usd", 0.0)) / total_usd if total_usd else 0.0) for k, b in by_agent.items()},
        "by_agent": dict(sorted(by_agent.items())),
        "by_model": dict(sorted(by_model.items())),
        "totals": totals,
        "total_usd": total_usd,
        "budget_usd": cost.budget_usd,
        "budget_used_pct": (100.0 * total_usd / cost.budget_usd) if cost.budget_usd else 0.0,
        "degraded": cost.degraded,
        "live_calls": len(live) if calls else n_calls,
        "artifact_cache_hits": len(hits),
        "warm": bool(hits),
        "estimated_cold_usd": total_usd + est_extra,
        "cache_read_share": (totals["cache_read"] / (totals["cache_read"] + totals["input_tokens"]))
        if (totals["cache_read"] + totals["input_tokens"]) else 0.0,
    }


def _repro_for(rv: RequirementVerdict, sv_by_id: dict[str, ScenarioVerdict], plan_by_id: dict, target_url: str) -> list[str]:
    failed = [sv_by_id[s] for s in rv.scenario_ids if s in sv_by_id and sv_by_id[s].status == "failed"]
    for sv in failed:
        if sv.repro_steps:
            return list(sv.repro_steps)
    for sid in rv.scenario_ids:
        sc = plan_by_id.get(sid)
        if sc is not None and sc.steps:
            steps = [f"Open {target_url}"] + [s.note for s in sc.steps]
            return steps
    return [f"Open {target_url}", "No automated reproduction steps were recorded; see the expected/observed values."]


def _failing_oracles(rv: RequirementVerdict, sv_by_id: dict[str, ScenarioVerdict]) -> tuple[list[dict], list[Evidence]]:
    oracles, evidence = [], []
    for sid in rv.scenario_ids:
        sv = sv_by_id.get(sid)
        if not sv or sv.status != "failed":
            continue
        for o in sv.oracle_results:
            if o.status == "fail":
                oracles.append({"scenario_id": sid, "type": o.oracle.type, "severity": o.oracle.severity,
                                "expected": o.expected, "observed": o.observed, "layer": o.layer})
                evidence.extend(o.evidence)
    return oracles, evidence


def _dedupe_evidence(items: Iterable[Evidence]) -> list[dict]:
    seen, out = set(), []
    for e in items:
        if e.path in seen:
            continue
        seen.add(e.path)
        out.append(e.model_dump())
    return out


def build_defects(
    requirements: RequirementSet,
    plan: TestPlan,
    scenario_verdicts: list[ScenarioVerdict],
    requirement_verdicts: list[RequirementVerdict],
    target_url: str,
) -> list[dict[str, Any]]:
    reqs = {r.id: r for r in requirements.requirements}
    sv_by_id = {v.scenario_id: v for v in scenario_verdicts}
    plan_by_id = {s.id: s for s in plan.scenarios}

    groups: dict[str, list[RequirementVerdict]] = {}
    for rv in requirement_verdicts:
        if rv.status != "failed":
            continue
        key = _norm_symptom(rv.observed)
        if not key:
            key = f"__{rv.requirement_id}"
        else:
            area = reqs[rv.requirement_id].area if rv.requirement_id in reqs else ""
            key = f"{area}|{key}"
        groups.setdefault(key, []).append(rv)

    defects = []
    for members in groups.values():
        def sev(rv: RequirementVerdict) -> str:
            if rv.severity:
                return rv.severity
            r = reqs.get(rv.requirement_id)
            return PRIORITY_DEFAULT_SEVERITY.get(r.priority, "major") if r else "major"

        members.sort(key=lambda rv: (SEVERITY_RANK[sev(rv)],
                                     PRIORITY_RANK.get(reqs[rv.requirement_id].priority, 9) if rv.requirement_id in reqs else 9,
                                     rv.requirement_id))
        lead = members[0]
        req = reqs.get(lead.requirement_id)
        oracles, oracle_ev = [], []
        evidence: list[Evidence] = []
        scenario_ids: list[str] = []
        for rv in members:
            o, ev = _failing_oracles(rv, sv_by_id)
            oracles += [x for x in o if x not in oracles]
            oracle_ev += ev
            evidence += rv.evidence
            scenario_ids += [s for s in rv.scenario_ids if s not in scenario_ids]
        confs = [sv_by_id[s].confidence for s in scenario_ids if s in sv_by_id and sv_by_id[s].status == "failed"]
        defects.append({
            "id": "",
            "title": (req.statement if req else lead.reason) or lead.reason,
            "severity": sev(lead),
            "priority": req.priority if req else None,
            "area": req.area if req else None,
            "requirement_ids": [rv.requirement_id for rv in members],
            "requirement_quote": req.source_quote if req else "",
            "statement": req.statement if req else "",
            "reason": lead.reason,
            "expected": lead.expected,
            "observed": lead.observed,
            "repro_steps": _repro_for(lead, sv_by_id, plan_by_id, target_url),
            "evidence": _dedupe_evidence(list(evidence) + oracle_ev),
            "scenario_ids": scenario_ids,
            "failing_oracles": oracles,
            "confidence": max(confs) if confs else None,
            "flaky": any(sv_by_id[s].flaky for s in scenario_ids if s in sv_by_id),
        })

    defects.sort(key=lambda d: (SEVERITY_RANK.get(d["severity"], 9), PRIORITY_RANK.get(d["priority"], 9),
                                d["requirement_ids"][0]))
    for i, d in enumerate(defects, 1):
        d["id"] = f"DEF-{i:03d}"
    # Cross-reference defects whose failing checks come from the same scenarios (one bug can
    # break several requirements; readers should know they share a root cause).
    for d in defects:
        mine = {o["scenario_id"] for o in d["failing_oracles"]}
        d["related"] = [o["id"] for o in defects if o is not d
                        and mine & {x["scenario_id"] for x in o["failing_oracles"]}]
    return defects


def build_summary(
    requirements: RequirementSet,
    plan: TestPlan,
    scenario_verdicts: list[ScenarioVerdict],
    requirement_verdicts: list[RequirementVerdict],
    defects: list[dict],
    cost: CostReport,
    started_at: datetime,
    finished_at: datetime,
    target_url: str,
) -> dict[str, Any]:
    reqs = {r.id: r for r in requirements.requirements}
    plan_by_id = {s.id: s for s in plan.scenarios}
    sv_by_id = {v.scenario_id: v for v in scenario_verdicts}

    by_status = Counter({s: 0 for s in STATUSES})
    by_status.update(rv.status for rv in requirement_verdicts)
    by_area: dict[str, dict[str, int]] = defaultdict(lambda: {s: 0 for s in STATUSES})
    by_priority: dict[str, dict[str, int]] = defaultdict(lambda: {s: 0 for s in STATUSES})
    for rv in requirement_verdicts:
        r = reqs.get(rv.requirement_id)
        by_area[r.area if r else "unknown"][rv.status] += 1
        by_priority[r.priority if r else "unknown"][rv.status] += 1

    by_polarity: dict[str, dict[str, int]] = {p: {s: 0 for s in STATUSES} for p in POLARITIES}
    for sv in scenario_verdicts:
        sc = plan_by_id.get(sv.scenario_id)
        pol = sc.polarity if sc else "positive"
        by_polarity.setdefault(pol, {s: 0 for s in STATUSES})[sv.status] += 1

    # Coverage matrix: requirement x polarity -> worst scenario status (None = not covered).
    coverage: dict[str, dict[str, Any]] = {}
    for rv in requirement_verdicts:
        cells: dict[str, list[str]] = {p: [] for p in POLARITIES}
        elsewhere: list[str] = []
        sids = set(rv.scenario_ids) | set(plan.coverage.get(rv.requirement_id, []))
        for sid in sorted(sids):
            sc = plan_by_id.get(sid)
            if sc is None:
                continue
            st = sv_by_id[sid].status if sid in sv_by_id else "unverified"
            if st == "failed" and rv.status == "passed":
                # The rollup attributed this failure to another requirement of the scenario.
                elsewhere.append(sid)
                continue
            cells[sc.polarity].append(st)
        coverage[rv.requirement_id] = {p: _worst(cells[p]) for p in POLARITIES}
        coverage[rv.requirement_id]["overall"] = rv.status
        if elsewhere:
            coverage[rv.requirement_id]["failed_elsewhere"] = elsewhere

    requirements_index = {
        r.id: {"area": r.area, "statement": r.statement, "source_quote": r.source_quote, "kind": r.kind,
               "priority": r.priority, "testability": r.testability}
        for r in requirements.requirements
    }
    scenario_index = {
        s.id: {"title": s.title, "polarity": s.polarity, "requirement_ids": s.requirement_ids, "phase": s.phase}
        for s in plan.scenarios
    }
    sev_counts = Counter({k: 0 for k in SEVERITY_RANK})
    sev_counts.update(d["severity"] for d in defects)
    total = len(requirement_verdicts)
    wall = max(0.0, (finished_at - started_at).total_seconds())
    cs = cost_summary(cost)
    verified = by_status["passed"] + by_status["failed"]

    exec_lines = [
        f"Audited {total} requirements against {target_url}: {by_status['passed']} passed, "
        f"{by_status['failed']} failed, {by_status['unverified']} unverified.",
        f"{len(defects)} distinct defects ({sev_counts['critical']} critical, {sev_counts['major']} major, "
        f"{sev_counts['minor']} minor) across {len(scenario_verdicts)} executed scenarios.",
        f"Estimated model cost ${cs['total_usd']:.4f} ({cs['live_calls']} API calls, "
        f"{cs['artifact_cache_hits']} artifact-cache hits); wall time {wall:.0f}s.",
    ]
    if defects:
        top = defects[0]
        exec_lines.append(f"Most severe: {top['id']} [{top['severity']}] {top['title']}")
    if cost.degraded:
        exec_lines.append("Budget guard engaged: some optional LLM checks were skipped and reported as unverified.")

    return {
        "total_requirements": total,
        "by_status": dict(by_status),
        "by_area": {k: by_area[k] for k in sorted(by_area)},
        "by_priority": {k: by_priority[k] for k in sorted(by_priority)},
        "by_polarity": by_polarity,
        "scenarios_total": len(scenario_verdicts),
        "scenarios_flaky": sum(1 for v in scenario_verdicts if v.flaky),
        "pass_rate": (by_status["passed"] / verified) if verified else 0.0,
        "verified_rate": (verified / total) if total else 0.0,
        "defects_total": len(defects),
        "defects_by_severity": dict(sev_counts),
        "coverage": coverage,
        "requirements_index": requirements_index,
        "scenario_index": scenario_index,
        "wall_time_s": wall,
        "cost_usd": cs["total_usd"],
        "usd_per_requirement": (cs["total_usd"] / total) if total else 0.0,
        "executive_summary": " ".join(exec_lines),
        "executive_summary_source": "deterministic",
    }


def build_report(
    run_id: str,
    target_url: str,
    prd_sha256: str,
    started_at: datetime,
    finished_at: datetime,
    requirements: RequirementSet,
    plan: TestPlan,
    scenario_verdicts: list[ScenarioVerdict],
    requirement_verdicts: list[RequirementVerdict],
    ledger: list[LedgerEntry],
    compensations: list[Compensation],
    cost: CostReport,
    environment: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> AuditReport:
    defects = build_defects(requirements, plan, scenario_verdicts, requirement_verdicts, target_url)
    summary = build_summary(requirements, plan, scenario_verdicts, requirement_verdicts, defects, cost,
                            started_at, finished_at, target_url)
    order = {r.id: i for i, r in enumerate(requirements.requirements)}
    rvs = sorted(requirement_verdicts, key=lambda v: (order.get(v.requirement_id, 1 << 30), v.requirement_id))
    lims = list(limitations or [])
    if any(not c.ok for c in compensations):
        lims.append("At least one state compensation failed; shared demo data may differ from its starting state.")
    if cost.degraded:
        lims.append("The LLM budget guard dropped optional checks; affected requirements are reported as unverified.")
    return AuditReport(
        run_id=run_id, target_url=target_url, prd_sha256=prd_sha256,
        started_at=started_at, finished_at=finished_at, summary=summary,
        requirements=rvs, scenarios=list(scenario_verdicts), defects=defects,
        state_ledger=list(ledger), compensations=list(compensations), cost=cost,
        environment=dict(environment or {}), limitations=lims,
    )


async def add_executive_summary(report: AuditReport, llm: Any) -> AuditReport:
    """Optionally replace the deterministic summary with a short prose one from the fast model.

    The call is optional: on budget refusal or any error the deterministic summary stays.
    """
    from ..config import FAST_MODEL

    facts = {
        "summary": {k: report.summary.get(k) for k in ("by_status", "defects_by_severity", "cost_usd", "wall_time_s")},
        "defects": [{k: d.get(k) for k in ("id", "severity", "title", "expected", "observed")} for d in report.defects[:8]],
    }
    try:
        text = await llm.complete(
            agent="reporter", purpose="executive_summary", model=FAST_MODEL,
            system=("You write the executive summary of a QA audit report for engineering leads. "
                    "Use only the facts given. The status counts are requirements (not tests). "
                    "3-5 sentences, plain text, no markdown, no speculation."),
            user=json.dumps(facts, default=str), max_tokens=600, optional=True,
        )
    except Exception:  # noqa: BLE001 - optional call; keep deterministic summary
        return report
    if isinstance(text, str) and text.strip():
        report.summary["executive_summary"] = text.strip()
        report.summary["executive_summary_source"] = "llm"
    return report


# --------------------------------------------------------------------------- render


def _env(autoescape: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=autoescape,
        trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
        # Markdown viewers render raw HTML: neutralise angle brackets in every value.
        finalize=None if autoescape else _md_finalize,
    )
    env.filters["usd"] = lambda v: f"${float(v or 0):,.4f}"
    env.filters["num"] = lambda v: f"{int(round(float(v or 0))):,}"
    env.filters["pct"] = lambda v: f"{100 * float(v or 0):.0f}%"
    env.filters["mdcell"] = _md_cell
    return env


def _md_finalize(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    return v.replace("->", "→").replace("<", "&lt;").replace(">", "&gt;")


def _md_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _resolve_evidence(path: str, out_dir: Path, base_dirs: Iterable[Path]) -> Path | None:
    p = Path(path)
    cands = [p] if p.is_absolute() else [out_dir / p, *[b / p for b in base_dirs], Path.cwd() / p]
    for c in cands:
        try:
            if c.is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def _rel_link(path: str, resolved: Path | None, out_dir: Path) -> str:
    if resolved is None:
        return path
    try:
        return resolved.relative_to(out_dir.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _evidence_view(evs: list[dict], out_dir: Path, base_dirs: list[Path], embed: bool) -> list[dict]:
    out = []
    for e in evs:
        resolved = _resolve_evidence(e["path"], out_dir, base_dirs)
        item = {**e, "link": _rel_link(e["path"], resolved, out_dir), "exists": resolved is not None, "data_uri": None}
        if embed and resolved is not None and e.get("kind") == "screenshot":
            mime = mimetypes.guess_type(resolved.name)[0] or "image/png"
            if mime.startswith("image/") and resolved.stat().st_size <= MAX_EMBED_BYTES:
                item["data_uri"] = f"data:{mime};base64," + base64.b64encode(resolved.read_bytes()).decode()
        out.append(item)
    return out


# --------------------------------------------------------------------------- readability helpers

LABEL_KEYS = ("name", "display_name", "label")
MAX_LABEL_FILES = 500
MAX_SHOTS_PER_DEFECT = 3
MAX_EMBED_TOTAL = 12_000_000
_PART_SPLIT = re.compile(r"\s\|\s(?=\[[^\]]+\]\s)")
_PART_HEAD = re.compile(r"^\[([^\]]+)\]\s+(.*)$", re.S)
_ATTEMPT = re.compile(r"__a(\d+)__(.+)\.(png|jpe?g)$", re.I)


def harvest_labels(dirs: Iterable[Path]) -> dict[str, str]:
    """Map opaque ids seen in recorded API responses to their human names.

    Scans the network logs saved as evidence: any JSON object with a long string `id` and a
    name-like field contributes `id -> name`. Used to make repro steps and ledgers readable.
    """
    labels: dict[str, str] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            i = o.get("id")
            if isinstance(i, str) and len(i) >= 8:
                for k in LABEL_KEYS:
                    v = o.get(k)
                    if isinstance(v, str) and v.strip() and len(v) <= 80:
                        labels.setdefault(i, v.strip())
                        break
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    n = 0
    for d in dirs:
        for f in sorted(Path(d).glob("evidence/**/*network*.json")):
            if n >= MAX_LABEL_FILES:
                return labels
            n += 1
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            for ent in data if isinstance(data, list) else [data]:
                walk(ent.get("resp_body") if isinstance(ent, dict) and "resp_body" in ent else ent)
    return labels


def humanize(text: Any, labels: dict[str, str]) -> Any:
    """Replace known opaque ids in a string with their human names."""
    if not isinstance(text, str) or not labels:
        return text
    for i, name in labels.items():
        if i in text:
            text = text.replace(i, name)
    return text


def humanize_step(step: str, labels: dict[str, str], target_url: str) -> str:
    step = humanize(step, labels)
    # "Open the X page (value: "/path")" -> show the full URL a human can paste.
    m = re.match(r'^(Open\b.*?)\s*\(value:\s*"(/[^"]*)"\)\s*$', step)
    if m and target_url:
        return f"{m.group(1)}: {target_url.rstrip('/')}{m.group(2)}"
    return step


def split_parts(text: Any) -> list[dict[str, str]]:
    """Split '[SCN-1] a | [SCN-2] b' into [{'scenario': 'SCN-1', 'text': 'a'}, ...]."""
    if not isinstance(text, str) or not text:
        return []
    out = []
    for part in _PART_SPLIT.split(text):
        m = _PART_HEAD.match(part.strip())
        out.append({"scenario": m.group(1), "text": m.group(2)} if m else {"scenario": "", "text": part.strip()})
    return out


def _shorten(text: Any, n: int = 260) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def discover_evidence(scenario_ids: Iterable[str], dirs: Iterable[Path], max_shots: int = MAX_SHOTS_PER_DEFECT) -> list[dict]:
    """Find screenshots / network logs saved under <dir>/evidence for the given scenarios.

    The executor names files `<scenario>__a<attempt>__<label>.png` and `<scenario>/a<attempt>_network.json`;
    the latest attempt wins. Paths are returned relative to the directory that holds them.
    """
    dirs = [Path(d) for d in dirs]
    out: list[dict] = []
    shots = 0
    for sid in scenario_ids:
        for d in dirs:
            ev = d / "evidence"
            if not ev.is_dir():
                continue
            files = []
            for f in ev.glob(f"{sid}__a*"):
                m = _ATTEMPT.search(f.name)
                if m and f.name.startswith(f"{sid}__"):
                    files.append((int(m.group(1)), m.group(2), f))
            if not files:
                continue
            last = max(a for a, _, _ in files)
            for a, label, f in sorted(files, key=lambda t: (t[1] != "final", t[1])):
                if a != last or shots >= max_shots:
                    continue
                out.append({"kind": "screenshot", "path": f.relative_to(d).as_posix(),
                            "caption": f"{sid}: {label.replace('_', ' ')} screen (attempt {a})"})
                shots += 1
            net = ev / sid / f"a{last}_network.json"
            if net.is_file():
                out.append({"kind": "network", "path": net.relative_to(d).as_posix(),
                            "caption": f"{sid}: API calls recorded (attempt {last})"})
            break
    return out


_UNVERIFIED_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"manual_only", re.I),
     "The spec analyst marked this requirement as needing human judgement; it is listed for manual review."),
    (re.compile(r"no scenarios planned", re.I),
     "No test scenario covered this requirement, so nothing was executed for it."),
    (re.compile(r"could not resolve element '([^']+)'(?:\s*\(([^)]*)\))?", re.I),
     "The agent could not find the UI element '{0}'{1} on the page, so the check was inconclusive "
     "rather than failed. The screenshot shows the page at that moment; a human should confirm "
     "whether the control is missing (a defect) or just labelled differently."),
    (re.compile(r"no positive scenario verified", re.I),
     "Every covering scenario passed, but they only exercised rejection or edge paths; none "
     "directly demonstrated the behaviour this requirement asserts, so the agent does not claim a pass."),
    (re.compile(r"timed? ?out", re.I),
     "The scenario timed out before its checks could finish, so the result is inconclusive."),
    (re.compile(r"budget", re.I),
     "The LLM budget guard skipped an optional judgement check for this requirement."),
    (re.compile(r"judge|confidence", re.I),
     "An LLM judgement check could not reach a confident verdict, so the agent did not guess."),
]


def explain_unverified(reason: str) -> str:
    """Plain-language explanation of why a requirement ended up unverified."""
    for pat, text in _UNVERIFIED_RULES:
        m = pat.search(reason or "")
        if m:
            g = m.groups()
            if not g:
                return text
            loc = f" ({g[1]})" if len(g) > 1 and g[1] else ""
            return text.format(g[0] or "", loc)
    return "The checks were inconclusive; see the technical reason."


def _fmt_value(v: Any) -> str:
    if v is None or v == "" or v == {}:
        return "off"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {_fmt_value(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(_fmt_value(x) for x in v)
    return str(v)


def _resilience_view(env: dict[str, Any]) -> dict[str, Any] | None:
    res = env.get("resilience")
    chaos = env.get("chaos")
    if not res and not chaos:
        return None
    res = res if isinstance(res, dict) else {}
    stats = dict(res.get("chaos_stats") or {})
    for w in res.get("chaos_stats_workers") or []:
        for k, v in (w or {}).items():
            stats[k] = stats.get(k, 0) + v
    recs = []
    for r in res.get("recoveries") or []:
        obs = [" ".join(str(o).replace("\\n", " ").split()) for o in (r.get("observations") or [])]
        recs.append({"after": r.get("after", ""), "observations": obs})
    return {
        "chaos": chaos if isinstance(chaos, dict) else None,
        "chaos_stats": stats,
        "recoveries": recs,
        "parallel_contexts": res.get("parallel_contexts"),
        "parallel_scenarios": res.get("parallel_scenarios"),
    }


def _context(report: AuditReport, out_dir: Path, embed: bool, base_dirs: list[Path]) -> dict[str, Any]:
    s = report.summary
    req_index = s.get("requirements_index", {})
    scen_index = s.get("scenario_index", {})
    coverage = s.get("coverage", {})
    defect_by_req = {rid: d["id"] for d in report.defects for rid in d["requirement_ids"]}
    dirs = [out_dir, *base_dirs]
    labels = harvest_labels(dirs)
    hz = lambda t: humanize(t, labels)  # noqa: E731
    sv_status = {sv.scenario_id: sv.status for sv in report.scenarios}
    budget = [MAX_EMBED_TOTAL]

    def ev_view(evs: list[dict], do_embed: bool) -> list[dict]:
        view = _evidence_view(evs, out_dir, base_dirs, do_embed and budget[0] > 0)
        for e in view:
            if e["data_uri"]:
                budget[0] -= len(e["data_uri"])
        return view

    rows = []
    for rv in report.requirements:
        meta = req_index.get(rv.requirement_id, {})
        cov = dict(coverage.get(rv.requirement_id, {}))
        if rv.status == "passed" and "failed_elsewhere" not in cov:
            # Older report.json: recompute the cells without failures attributed elsewhere.
            elsewhere = [x for x in rv.scenario_ids if sv_status.get(x) == "failed"]
            if elsewhere:
                cells: dict[str, list[str]] = {p: [] for p in POLARITIES}
                for x in rv.scenario_ids:
                    pol = scen_index.get(x, {}).get("polarity")
                    if x not in elsewhere and pol in cells:
                        cells[pol].append(sv_status.get(x, "unverified"))
                cov.update({p: _worst(cells[p]) for p in POLARITIES}, failed_elsewhere=elsewhere)
        rows.append({
            "id": rv.requirement_id, "status": rv.status, "area": meta.get("area", ""),
            "priority": meta.get("priority", ""), "statement": meta.get("statement", ""),
            "quote": meta.get("source_quote", ""), "reason": hz(rv.reason), "expected": rv.expected,
            "observed": rv.observed, "severity": rv.severity, "scenario_ids": rv.scenario_ids,
            "coverage": cov, "failed_elsewhere": cov.get("failed_elsewhere") or [],
            "defect": defect_by_req.get(rv.requirement_id),
            "evidence": _evidence_view([e.model_dump() for e in rv.evidence], out_dir, base_dirs, False),
        })

    unverified = []
    for row in rows:
        if row["status"] != "unverified":
            continue
        evs = [e.model_dump() for rv in report.requirements if rv.requirement_id == row["id"] for e in rv.evidence]
        if not any(e.get("kind") == "screenshot" for e in evs):
            evs += discover_evidence(row["scenario_ids"], dirs, max_shots=1)
        unverified.append({
            **row,
            "why": explain_unverified(row["reason"]),
            "scenarios": [{"id": sid, "polarity": scen_index.get(sid, {}).get("polarity", ""),
                           "status": sv_status.get(sid, "not run")} for sid in row["scenario_ids"]],
            "evidence": ev_view(evs, embed),
        })

    fail_sids = {d["id"]: {o["scenario_id"] for o in d.get("failing_oracles", [])} for d in report.defects}
    defects = []
    for d in report.defects:
        related = d.get("related")
        if related is None:  # report.json written before cross-referencing existed
            related = [o for o, sids in fail_sids.items() if o != d["id"] and sids & fail_sids[d["id"]]]
        evs = list(d.get("evidence", []))
        if not any(e.get("kind") == "screenshot" for e in evs):
            failing = [o["scenario_id"] for o in d.get("failing_oracles", [])]
            order = list(dict.fromkeys(failing + [x for x in d.get("scenario_ids", []) if sv_status.get(x) == "failed"]))
            evs += discover_evidence(order, dirs)
        defects.append({
            **d,
            "expected": hz(d.get("expected", "")), "observed": hz(d.get("observed", "")),
            "expected_parts": split_parts(hz(d.get("expected", ""))),
            "observed_parts": split_parts(hz(d.get("observed", ""))),
            "headline": _shorten(split_parts(hz(d.get("observed", "")))[0]["text"] if d.get("observed") else d.get("reason", "")),
            "repro_steps": [humanize_step(x, labels, report.target_url) for x in d.get("repro_steps", [])],
            "failing_oracles": [{**o, "expected": hz(o.get("expected")), "observed": hz(o.get("observed"))}
                                for o in d.get("failing_oracles", [])],
            "related": related,
            "evidence": ev_view(evs, embed),
        })

    scenarios = []
    for sv in report.scenarios:
        meta = scen_index.get(sv.scenario_id, {})
        scenarios.append({"id": sv.scenario_id, "status": sv.status, "title": meta.get("title", ""),
                          "polarity": meta.get("polarity", ""), "confidence": sv.confidence,
                          "attempts": sv.attempts, "flaky": sv.flaky,
                          "requirement_ids": meta.get("requirement_ids", [])})
    ledger = [{"scenario_id": e.scenario_id, "tx_id": e.tx_id, "intended": e.intended, "memo_tag": e.memo_tag,
               "deltas": {hz(k): v for k, v in e.deltas.items()}} for e in report.state_ledger]
    comps = [{"reason": hz(c.reason), "tx_id": c.tx_id, "ok": c.ok,
              "deltas": {hz(k): v for k, v in c.deltas.items()}} for c in report.compensations]
    env = {k: _fmt_value(v) for k, v in report.environment.items() if k != "resilience"}
    return {
        "r": report, "s": s, "rows": rows, "defects": defects, "scenarios": scenarios,
        "unverified": unverified, "ledger": ledger, "compensations": comps,
        "env": env, "resilience": _resilience_view(report.environment),
        "any_failed_elsewhere": any(r["failed_elsewhere"] for r in rows),
        "cost": cost_summary(report.cost), "statuses": STATUSES, "polarities": POLARITIES,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def render(
    report: AuditReport,
    out_dir: Path,
    formats: Iterable[str] = ("json", "md", "html"),
    *,
    evidence_base_dirs: Iterable[Path] = (),
) -> dict[str, Path]:
    """Write the requested formats into out_dir. "pdf" is ignored here; use render_pdf."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = list(formats)
    bases = [Path(b) for b in evidence_base_dirs]
    written: dict[str, Path] = {}
    if "json" in formats:
        p = out_dir / "report.json"
        p.write_text(report.model_dump_json(indent=2))
        written["json"] = p
    if "md" in formats:
        p = out_dir / "report.md"
        p.write_text(_env(False).get_template("report.md.j2").render(**_context(report, out_dir, False, bases)))
        written["md"] = p
    if "html" in formats:
        p = out_dir / "report.html"
        p.write_text(_env(True).get_template("report.html.j2").render(**_context(report, out_dir, True, bases)))
        written["html"] = p
    return written


async def render_pdf(html_path: Path) -> Path:
    """Print report.html to report.pdf (same directory) with headless Chromium."""
    from playwright.async_api import async_playwright

    html_path = Path(html_path).resolve()
    pdf_path = html_path.with_suffix(".pdf")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.goto(html_path.as_uri(), wait_until="load")
            await page.emulate_media(media="print")
            await page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
            await page.pdf(path=str(pdf_path), format="A4", print_background=True,
                           margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
        finally:
            await browser.close()
    return pdf_path
