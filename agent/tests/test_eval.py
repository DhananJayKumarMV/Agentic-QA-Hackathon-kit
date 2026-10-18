import json
import shutil
from pathlib import Path

import yaml

from eval import multi_run, score
from qa_agent.agents import reporter
from tests.test_reporter import FIX, load_inputs


def _write_run(tmp_path: Path, name: str = "run1", **overrides) -> Path:
    inputs = load_inputs()
    inputs.update(overrides)
    rep = reporter.build_report(**inputs)
    d = tmp_path / name
    shutil.copytree(FIX / "evidence", d / "evidence")
    reporter.render(rep, d, formats=("json",))
    return d


TRUTH = [
    # matches REQ-TRF-001 (agent: failed) -> detected
    {"anchor": "A successful transfer must debit the source account by exactly the transfer amount",
     "expected_status": "failed", "symptom": "over-debit", "severity": "critical"},
    # matches REQ-ACC-001 (agent: passed) -> false pass
    {"anchor": "Protected pages must redirect signed-out visitors to the landing page",
     "expected_status": "failed", "symptom": "no redirect", "severity": "major"},
    # matches REQ-UI-001 (agent: unverified)
    {"anchor": "The layout must work at 375px width without horizontal scrolling",
     "expected_status": "failed", "symptom": "overflow", "severity": "minor"},
    # matches REQ-PRO-001 (agent: failed) but truth says passed -> false fail
    {"anchor": "The profile page shows the customer's display name and email",
     "expected_status": "passed", "symptom": "", "severity": "minor"},
    # matches nothing
    {"anchor": "Statements can be exported as CSV files", "expected_status": "failed",
     "symptom": "", "severity": "minor"},
]


def test_jaccard():
    assert score.jaccard("the quick brown fox", "quick brown fox") == 1.0
    assert score.jaccard("alpha beta", "gamma delta") == 0.0


def test_score_metrics(tmp_path):
    run = _write_run(tmp_path)
    truth_file = tmp_path / "truth.yaml"
    truth_file.write_text(yaml.safe_dump(TRUTH))
    assert score.main([str(run), "--truth", str(truth_file)]) == 0
    res = json.loads((run / "eval_score.json").read_text())
    outcomes = [m["outcome"] for m in res["matches"]]
    assert outcomes == ["correct", "false_pass", "unverified", "false_fail", "unmatched"]
    assert res["defect_recall"] == 1 / 4  # 4 ground-truth failures, 1 detected
    # agent failed 4 reqs: TRF-001 true, PRO-001 false (known passed), TRF-003 + HIS-001 unlabeled.
    # Precision is over labeled failures only; the strict variant counts unlabeled as false.
    assert res["precision_on_failures"] == 1 / 2
    assert res["precision_all_failures_strict"] == 1 / 4
    assert res["true_failures"] == ["REQ-TRF-001"] and res["false_failures"] == ["REQ-PRO-001"]
    assert res["false_pass_count"] == 1
    assert res["false_fail_count"] == 1
    assert sorted(res["unlabeled_failures"]) == ["REQ-HIS-001", "REQ-TRF-003"]
    assert res["unverified_rate"] == 1 / 6
    assert res["wall_time_s"] == 250
    assert res["cost_usd"] > 0


def test_example_truth_is_valid_and_generic():
    entries, path = score.load_truth(score.EXAMPLE_TRUTH)
    assert len(entries) == 2
    for e in entries:
        assert set(e) == {"anchor", "expected_status", "symptom", "severity"}
        assert e["expected_status"] in {"failed", "passed"}


def test_multi_run_compare(tmp_path):
    r1 = _write_run(tmp_path, "a")
    inputs = load_inputs()
    flipped = [v.model_copy(update={"status": "passed"}) if v.requirement_id == "REQ-HIS-001" else v
               for v in inputs["requirement_verdicts"]]
    r2 = _write_run(tmp_path, "b", requirement_verdicts=flipped, run_id="run-b")
    res = multi_run.compare([multi_run.load(r1), multi_run.load(r2)])
    assert res["requirements"] == 6
    assert list(res["unstable"]) == ["REQ-HIS-001"]
    assert res["stability_rate"] == 5 / 6
    assert res["cost_usd"]["stdev"] == 0.0
    assert multi_run.main(["--reports", str(r1), str(r2), "--runs-dir", str(tmp_path)]) == 0
    assert list(tmp_path.glob("multi_run_*.json"))


def test_multi_run_out_dir_parsing():
    assert multi_run._runs_dir(["--url", "x", "--out", "elsewhere"], Path("runs")) == Path("elsewhere")
    assert multi_run._runs_dir(["--url", "x"], Path("runs")) == Path("runs")


def test_containment_matches_long_anchor_and_multi_match():
    assert score.containment("amount must be positive", "The amount must be positive and two decimals") == 1.0
    anchor = "Amount must be a positive number greater than zero, with no more than two decimals"
    # The requirement restates the anchor with extra wording: Jaccard < 0.5, containment = 1.
    req = ("The transfer amount entered on the transfer form must be a positive number strictly greater "
           "than zero and accept no more than two decimals; validation errors appear inline next to the field")
    assert score.jaccard(anchor, req) < 0.5
    assert score.containment(anchor, req) == 1.0 and score.similarity(anchor, req, "") == 1.0


def test_anchor_matches_several_requirements_detected_if_any_failed(tmp_path):
    from qa_agent.contracts import AuditReport
    run = _write_run(tmp_path)
    report = AuditReport.model_validate_json((run / "report.json").read_text())
    texts = score._req_texts(report, run)
    rids = list(texts)[:2]
    # Give two requirements the same quote; fail only the second.
    idx = report.summary.setdefault("requirements_index", {})
    for rid in rids:
        idx[rid] = {"source_quote": "Overdrafts must be rejected and balances unchanged", "statement": ""}
    report.requirements = [rv.model_copy(update={"status": "passed" if rv.requirement_id == rids[0] else
                                                 ("failed" if rv.requirement_id == rids[1] else rv.status)})
                           for rv in report.requirements]
    res = score.score(report, [{"anchor": "Overdrafts must be rejected and balances unchanged",
                                "expected_status": "failed"}], run_dir=run)
    m = res["matches"][0]
    assert set(m["requirement_ids"]) >= set(rids) and m["outcome"] == "correct"
    assert res["defect_recall"] == 1.0 and rids[1] in res["true_failures"]
