import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from qa_agent.agents import reporter
from qa_agent.contracts import (
    AuditReport, Compensation, CostReport, LedgerEntry, RequirementSet, RequirementVerdict,
    ScenarioVerdict, TestPlan,
)
from qa_agent.llm.client import FakeLLM

FIX = Path(__file__).parent / "fixtures" / "report"


def load_inputs() -> dict:
    d = json.loads((FIX / "inputs.json").read_text())
    return dict(
        run_id=d["run_id"], target_url=d["target_url"], prd_sha256=d["prd_sha256"],
        started_at=datetime.fromisoformat(d["started_at"]), finished_at=datetime.fromisoformat(d["finished_at"]),
        requirements=RequirementSet.model_validate(d["requirements"]),
        plan=TestPlan.model_validate(d["plan"]),
        scenario_verdicts=[ScenarioVerdict.model_validate(v) for v in d["scenario_verdicts"]],
        requirement_verdicts=[RequirementVerdict.model_validate(v) for v in d["requirement_verdicts"]],
        ledger=[LedgerEntry.model_validate(v) for v in d["ledger"]],
        compensations=[Compensation.model_validate(v) for v in d["compensations"]],
        cost=CostReport.model_validate(d["cost"]),
        environment=d["environment"], limitations=d["limitations"],
    )


@pytest.fixture
def report() -> AuditReport:
    return reporter.build_report(**load_inputs())


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    shutil.copytree(FIX / "evidence", tmp_path / "evidence")
    return tmp_path


def test_summary_counts(report):
    s = report.summary
    assert s["by_status"] == {"passed": 1, "failed": 4, "unverified": 1}
    assert s["by_area"]["transfers"] == {"passed": 0, "failed": 2, "unverified": 0}
    assert s["by_polarity"]["positive"]["failed"] == 2
    assert s["by_polarity"]["negative"] == {"passed": 1, "failed": 1, "unverified": 0}
    assert s["coverage"]["REQ-TRF-003"] == {"positive": "failed", "negative": None, "edge": "failed", "overall": "failed"}
    assert s["coverage"]["REQ-ACC-001"]["negative"] == "passed"
    assert s["wall_time_s"] == 250
    # requirements kept in PRD order
    assert [r.requirement_id for r in report.requirements][0] == "REQ-ACC-001"


def test_defects_deduped_and_ranked(report):
    ds = report.defects
    assert [d["id"] for d in ds] == ["DEF-001", "DEF-002", "DEF-003"]
    assert [d["severity"] for d in ds] == ["critical", "major", "minor"]
    # same symptom with different numbers merges into one defect
    assert ds[0]["requirement_ids"] == ["REQ-TRF-001", "REQ-TRF-003"]
    assert ds[0]["requirement_quote"].startswith("A successful transfer")
    assert ds[0]["repro_steps"][0] == "Open the site and enter the demo"
    assert {e["path"] for e in ds[0]["evidence"]} >= {"evidence/SC-TRF-001_after.png"}
    assert ds[0]["failing_oracles"][0]["type"] == "balance_delta"
    # severity defaults from priority when the verifier left it empty
    assert ds[1]["requirement_ids"] == ["REQ-PRO-001"]


def test_cost_totals(report):
    cs = reporter.cost_summary(report.cost)
    calls = report.cost.calls
    assert cs["totals"]["calls"] == len(calls) == 6
    assert cs["total_usd"] == pytest.approx(sum(c.usd for c in calls))
    assert cs["totals"]["usd"] == pytest.approx(sum(b["usd"] for b in cs["by_model"].values()))
    assert cs["totals"]["input_tokens"] == sum(c.input_tokens for c in calls)
    assert cs["by_agent"]["verifier"]["calls"] == 3
    assert cs["by_agent"]["verifier"]["artifact_cache_hits"] == 1
    assert cs["artifact_cache_hits"] == 2 and cs["warm"]
    assert cs["estimated_cold_usd"] > cs["total_usd"]


def test_render_all_formats(report, out_dir):
    paths = reporter.render(report, out_dir)
    assert set(paths) == {"json", "md", "html"}
    again = AuditReport.model_validate_json(paths["json"].read_text())
    assert again == report

    md = paths["md"].read_text()
    for section in ("## Executive summary", "## Scorecard", "## Coverage matrix", "## Defects",
                    "## Unverified requirements", "## State ledger", "## Cost", "## Environment", "## Limitations"):
        assert section in md
    assert "### DEF-001" in md and "REQ-UI-001" in md
    assert "1. Open the site and enter the demo" in md
    assert "Cold vs warm" in md
    assert f"${report.cost.total_usd:,.4f}" in md
    assert "](evidence/SC-TRF-001_after.png)" in md
    assert "<script>" not in md and "&lt;script&gt;" in md


def test_html_escapes_and_embeds(report, out_dir):
    html = reporter.render(report, out_dir, formats=("html",))["html"].read_text()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.count("<script>") == 1  # only our own filter/lightbox script
    assert "data:image/png;base64," in html
    assert "prefers-color-scheme: dark" in html and "@media print" in html
    assert 'id="DEF-001"' in html and 'data-filter="failed"' in html


def test_missing_evidence_does_not_crash(report, tmp_path):
    html = reporter.render(report, tmp_path, formats=("html",))["html"].read_text()
    assert "file not found" in html


def test_no_failures_report():
    inputs = load_inputs()
    inputs["requirement_verdicts"] = [v.model_copy(update={"status": "passed"}) for v in inputs["requirement_verdicts"]]
    rep = reporter.build_report(**inputs)
    assert rep.defects == []
    assert rep.summary["pass_rate"] == 1.0


async def test_llm_executive_summary_optional(report):
    llm = FakeLLM({("reporter", "executive_summary"): "Four requirements failed."})
    await reporter.add_executive_summary(report, llm)
    assert report.summary["executive_summary"] == "Four requirements failed."
    assert llm.calls[0]["model"] == "claude-haiku-4-5"

    broken = FakeLLM({})  # KeyError -> deterministic summary kept
    rep2 = reporter.build_report(**load_inputs())
    before = rep2.summary["executive_summary"]
    await reporter.add_executive_summary(rep2, broken)
    assert rep2.summary["executive_summary"] == before


async def test_render_pdf(report, out_dir):
    pw = pytest.importorskip("playwright.async_api")
    try:
        async with pw.async_playwright() as p:
            b = await p.chromium.launch()
            await b.close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"chromium unavailable: {e}")
    html = reporter.render(report, out_dir, formats=("html",))["html"]
    pdf = await reporter.render_pdf(html)
    assert pdf.exists() and pdf.read_bytes()[:4] == b"%PDF"


# --------------------------------------------------------------------------- readability


def _seed_run_dir(out_dir: Path, sid: str) -> None:
    """Lay out evidence the way the executor does: <sid>__a<n>__<label>.png + <sid>/a<n>_network.json."""
    ev = out_dir / "evidence"
    png = (FIX / "evidence" / "SC-TRF-001_after.png").read_bytes()
    for name in (f"{sid}__a1__final.png", f"{sid}__a2__final.png", f"{sid}__a2__judge.png"):
        (ev / name).write_bytes(png)
    (ev / sid).mkdir(parents=True, exist_ok=True)
    (ev / sid / "a2_network.json").write_text(json.dumps([
        {"method": "GET", "url": "/api/accounts", "status": 200, "resp_body": {"accounts": [
            {"id": "acct-0000-0001", "name": "Main Checking"}, {"id": "acct-0000-0002", "name": "Rainy Savings"}]}},
        {"method": "GET", "url": "/api/me", "status": 0, "resp_body": None},
    ]))


def test_discover_evidence_latest_attempt(out_dir):
    _seed_run_dir(out_dir, "SC-PRO-001")
    found = reporter.discover_evidence(["SC-PRO-001", "SC-NOPE"], [out_dir])
    shots = [e for e in found if e["kind"] == "screenshot"]
    assert [e["path"] for e in shots] == ["evidence/SC-PRO-001__a2__final.png", "evidence/SC-PRO-001__a2__judge.png"]
    assert any(e["kind"] == "network" and e["path"].endswith("a2_network.json") for e in found)
    assert reporter.discover_evidence(["SC-PRO-001"], [out_dir], max_shots=1)[0]["caption"].startswith("SC-PRO-001: final")


def test_labels_humanize_steps(out_dir):
    _seed_run_dir(out_dir, "SC-PRO-001")
    labels = reporter.harvest_labels([out_dir])
    assert labels == {"acct-0000-0001": "Main Checking", "acct-0000-0002": "Rainy Savings"}
    step = reporter.humanize_step('Select source (value: "acct-0000-0001")', labels, "http://app")
    assert step == 'Select source (value: "Main Checking")'
    assert reporter.humanize_step('Open the Transfer page (value: "/transfer")', labels, "http://app/") == \
        "Open the Transfer page: http://app/transfer"


def test_defect_without_evidence_gets_discovered_screenshot(report, out_dir):
    _seed_run_dir(out_dir, "SC-PRO-001")
    d = next(d for d in report.defects if d["requirement_ids"] == ["REQ-PRO-001"])
    assert d["evidence"] == []
    html = reporter.render(report, out_dir, formats=("html",))["html"].read_text()
    block = html[html.index(f'id="{d["id"]}"'):]
    block = block[:block.index("</details>")]
    assert "data:image/png;base64," in block and "SC-PRO-001: final screen (attempt 2)" in block
    md = reporter.render(report, out_dir, formats=("md",))["md"].read_text()
    assert "](evidence/SC-PRO-001__a2__final.png)" in md


def test_split_parts_and_explanations():
    parts = reporter.split_parts("[SCN-1] zero accepted | [SCN-2] negative accepted (a | b)")
    assert parts == [{"scenario": "SCN-1", "text": "zero accepted"},
                     {"scenario": "SCN-2", "text": "negative accepted (a | b)"}]
    assert reporter.split_parts("plain") == [{"scenario": "", "text": "plain"}]
    why = reporter.explain_unverified(
        "SCN-1: element_visible could not verify: probe failed: ResolveError: could not resolve element "
        "'landing.entry' (role=button[name=\"Enter\"])")
    assert "'landing.entry' (role=button[name=\"Enter\"])" in why and "inconclusive" in why
    assert "rejection or edge paths" in reporter.explain_unverified("no positive scenario verified the requirement")
    assert "manual" in reporter.explain_unverified("manual_only: requires manual verification")
    assert "timed out" in reporter.explain_unverified("Probe timed out locating the dashboard")


def test_related_defects_and_unverified_section(report, out_dir):
    for d in report.defects:
        assert "related" in d
    md = reporter.render(report, out_dir, formats=("md",))["md"].read_text()
    sect = md[md.index("## Unverified requirements"):md.index("## All requirement results")]
    assert "Why unverified" in sect and "timed out" in sect and "SC-UI-001 (edge)" in sect


def test_coverage_marks_failures_attributed_elsewhere():
    inputs = load_inputs()
    rvs = inputs["requirement_verdicts"]
    # REQ-ACC-001 passes although SC-TRF-001 (also covering it) failed for another requirement.
    acc = next(v for v in rvs if v.requirement_id == "REQ-ACC-001")
    acc.scenario_ids.append("SC-TRF-001")
    rep = reporter.build_report(**inputs)
    cov = rep.summary["coverage"]["REQ-ACC-001"]
    assert cov["positive"] == "passed" and cov["failed_elsewhere"] == ["SC-TRF-001"]


def test_cost_models_and_share(report):
    cs = reporter.cost_summary(report.cost)
    assert set(cs["agent_models"]) == set(cs["by_agent"])
    assert sum(cs["share"].values()) == pytest.approx(1.0)


def test_resilience_and_environment_rendering(out_dir):
    inputs = load_inputs()
    inputs["environment"] = {
        "models": {"reasoning": "big", "fast": "small"}, "chaos": {"latency_ms": 800, "error_rate": 0.1, "seed": 7},
        "resilience": {"recoveries": [{"after": "SC-1", "observations": ["error state", "clicked retry once"]}],
                       "chaos_stats": {"delayed": 5, "failed": 2}, "chaos_stats_workers": [{"failed": 1}]},
    }
    rep = reporter.build_report(**inputs)
    paths = reporter.render(rep, out_dir, formats=("md", "html"))
    md, html = paths["md"].read_text(), paths["html"].read_text()
    assert "| models | reasoning: big, fast: small |" in md
    assert "## Resilience" in md and "Chaos mode was **on**" in md and "failed 3" in md
    assert "clicked retry once" in md and 'id="resilience"' in html
    assert "{'" not in md  # no raw Python dict reprs
