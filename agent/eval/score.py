"""Score one agent run against a hand-written ground truth.

Usage:
    python -m eval.score runs/<run_id> [--truth eval/ground_truth.local.yaml] [--threshold 0.5]

Reads runs/<run_id>/report.json and matches each ground-truth entry to requirement verdicts by
max(token-Jaccard, containment) between the entry's `anchor` and each requirement's
source_quote / statement. Containment is the share of anchor tokens found in the requirement
text, so a long anchor that the Spec Analyst split over several requirements still matches.
An anchor matches every requirement scoring >= threshold and within MATCH_MARGIN of its best
match; a "failed" entry counts as detected if ANY matched requirement failed.

Precision is computed over labeled failures only: an agent failure on a requirement that no
ground-truth entry matches is "unlabeled" (listed for manual review), not a false positive.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from qa_agent.contracts import AuditReport

EVAL_DIR = Path(__file__).resolve().parent
LOCAL_TRUTH = EVAL_DIR / "ground_truth.local.yaml"
EXAMPLE_TRUTH = EVAL_DIR / "ground_truth.example.yaml"

STOPWORDS = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "by", "is", "are", "be",
             "must", "should", "shall", "it", "its", "with", "as", "at", "that", "this", "from"}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def containment(anchor: str, text: str) -> float:
    """Share of the anchor's tokens that appear in text."""
    ta, tb = tokens(anchor), tokens(text)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


MATCH_MARGIN = 0.2


def similarity(anchor: str, quote: str, statement: str) -> float:
    return max(jaccard(anchor, quote), jaccard(anchor, statement),
               containment(anchor, quote), containment(anchor, statement))


def load_truth(path: Path | None = None) -> tuple[list[dict[str, Any]], Path]:
    if path is None:
        path = LOCAL_TRUTH if LOCAL_TRUTH.exists() else EXAMPLE_TRUTH
    data = yaml.safe_load(Path(path).read_text()) or []
    if isinstance(data, dict):  # tolerate {"entries": [...]}
        data = data.get("entries", [])
    return list(data), Path(path)


def load_report(run: Path) -> tuple[AuditReport, Path]:
    run = Path(run)
    report_path = run / "report.json" if run.is_dir() else run
    return AuditReport.model_validate_json(report_path.read_text()), report_path.parent


def _req_texts(report: AuditReport, run_dir: Path) -> dict[str, tuple[str, str]]:
    index = report.summary.get("requirements_index") or {}
    texts = {rid: (m.get("source_quote", ""), m.get("statement", "")) for rid, m in index.items()}
    req_file = run_dir / "requirements.json"
    if req_file.exists():  # fall back to the Spec Analyst artifact when present
        try:
            for r in json.loads(req_file.read_text()).get("requirements", []):
                texts.setdefault(r["id"], (r.get("source_quote", ""), r.get("statement", "")))
        except (ValueError, KeyError, AttributeError):
            pass
    return texts


def score(report: AuditReport, truth: list[dict[str, Any]], threshold: float = 0.5,
          run_dir: Path | None = None) -> dict[str, Any]:
    texts = _req_texts(report, run_dir or Path("."))
    status = {rv.requirement_id: rv.status for rv in report.requirements}

    matches: list[dict[str, Any]] = []
    failed_entry_reqs: set[str] = set()  # agent requirements matched to a ground-truth failure
    passed_entry_reqs: set[str] = set()  # ... matched to a ground-truth pass
    for entry in truth:
        anchor = str(entry.get("anchor", ""))
        scored = sorted(((similarity(anchor, *texts.get(rid, ("", ""))), rid) for rid in status), reverse=True)
        best = scored[0][0] if scored else 0.0
        hits = [rid for sim, rid in scored if sim >= threshold and sim >= best - MATCH_MARGIN]
        expected = entry.get("expected_status", "failed")
        (failed_entry_reqs if expected == "failed" else passed_entry_reqs).update(hits)
        sts = [status[h] for h in hits]
        if not hits:
            outcome, agent_status = "unmatched", None
        elif expected == "failed":
            agent_status = "failed" if "failed" in sts else ("passed" if "passed" in sts else "unverified")
            outcome = {"failed": "correct", "passed": "false_pass", "unverified": "unverified"}[agent_status]
        else:
            agent_status = ("failed" if "failed" in sts else
                            "passed" if all(x == "passed" for x in sts) else "unverified")
            outcome = {"failed": "false_fail", "passed": "correct", "unverified": "unverified"}[agent_status]
            if expected != "passed" and agent_status != expected:
                outcome = "false_fail" if agent_status == "failed" else "unverified"
        matches.append({"anchor": anchor, "expected_status": expected, "severity": entry.get("severity"),
                        "symptom": entry.get("symptom"), "requirement_id": hits[0] if hits else None,
                        "requirement_ids": hits, "similarity": round(best, 3),
                        "agent_status": agent_status, "agent_statuses": dict(zip(hits, sts)), "outcome": outcome})

    gt_failed = [m for m in matches if m["expected_status"] == "failed"]
    detected = [m for m in gt_failed if m["agent_status"] == "failed"]
    agent_failed = [rid for rid, st in status.items() if st == "failed"]
    true_fail = [rid for rid in agent_failed if rid in failed_entry_reqs]
    false_fail_known = [rid for rid in agent_failed if rid in passed_entry_reqs and rid not in failed_entry_reqs]
    unlabeled_fail = [rid for rid in agent_failed if rid not in failed_entry_reqs | passed_entry_reqs]
    labeled = len(true_fail) + len(false_fail_known)
    total = len(status)
    wall = (report.finished_at - report.started_at).total_seconds()
    return {
        "run_id": report.run_id,
        "threshold": threshold,
        "ground_truth_entries": len(truth),
        "defect_recall": (len(detected) / len(gt_failed)) if gt_failed else None,
        "precision_on_failures": (len(true_fail) / labeled) if labeled else None,
        "precision_all_failures_strict": (len(true_fail) / len(agent_failed)) if agent_failed else None,
        "true_failures": true_fail,
        "false_failures": false_fail_known,
        "false_pass_count": sum(1 for m in matches if m["outcome"] == "false_pass"),
        "false_fail_count": len(false_fail_known),
        "unlabeled_failures": unlabeled_fail,
        "unmatched_entries": sum(1 for m in matches if m["outcome"] == "unmatched"),
        "unverified_rate": (sum(1 for st in status.values() if st == "unverified") / total) if total else 0.0,
        "requirements_total": total,
        "cost_usd": report.cost.total_usd,
        "wall_time_s": wall,
        "matches": matches,
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path, help="run directory (runs/<run_id>) or report.json path")
    ap.add_argument("--truth", type=Path, default=None, help="ground-truth YAML (default: local, else example)")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args(argv)

    report, run_dir = load_report(args.run)
    truth, truth_path = load_truth(args.truth)
    result = score(report, truth, args.threshold, run_dir)
    result["ground_truth_file"] = truth_path.name
    out = run_dir / "eval_score.json"
    out.write_text(json.dumps(result, indent=2))

    print(f"run {result['run_id']}  (truth: {truth_path.name}, {len(truth)} entries)")
    for k in ("defect_recall", "precision_on_failures", "precision_all_failures_strict", "false_pass_count",
              "false_fail_count", "unmatched_entries", "unverified_rate", "cost_usd", "wall_time_s"):
        print(f"  {k:<29} {_fmt(result[k])}")
    for m in result["matches"]:
        if m["outcome"] != "correct":
            print(f"  [{m['outcome']}] {m['anchor'][:70]!r} -> {m['agent_statuses'] or None} (sim {m['similarity']})")
    if result["unlabeled_failures"]:
        print("  unlabeled failures (review by hand): " + ", ".join(result["unlabeled_failures"]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
