"""Run the agent N times and measure verdict stability and cost spread.

Usage:
    python -m eval.multi_run -n 3 -- --url http://localhost:5173 --prd ../test-website/PRD.md
    python -m eval.multi_run --reports runs/a runs/b runs/c      # analyse existing runs only

Each run executes `qa-agent run <args>` and is expected to write runs/<run_id>/report.json
(or <--out>/<run_id>/report.json). Writes <runs dir>/multi_run_<timestamp>.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from qa_agent.contracts import AuditReport


def _runs_dir(agent_args: list[str], default: Path) -> Path:
    for i, a in enumerate(agent_args):
        if a in ("--out", "--out-dir") and i + 1 < len(agent_args):
            return Path(agent_args[i + 1])
        if a.startswith("--out=") or a.startswith("--out-dir="):
            return Path(a.split("=", 1)[1])
    return default


def _report_dirs(runs_dir: Path) -> set[Path]:
    return {p.parent.resolve() for p in runs_dir.glob("*/report.json")} if runs_dir.exists() else set()


def _command() -> list[str]:
    exe = shutil.which("qa-agent")
    if exe:
        return [exe, "run"]
    return [sys.executable, "-m", "qa_agent.cli", "run"]


def run_once(agent_args: list[str], runs_dir: Path) -> tuple[Path | None, int, float]:
    before = _report_dirs(runs_dir)
    t0 = time.monotonic()
    proc = subprocess.run(_command() + agent_args)
    elapsed = time.monotonic() - t0
    new = sorted(_report_dirs(runs_dir) - before, key=lambda p: (p / "report.json").stat().st_mtime)
    return (new[-1] if new else None), proc.returncode, elapsed


def load(run_dir: Path) -> AuditReport:
    run_dir = Path(run_dir)
    path = run_dir / "report.json" if run_dir.is_dir() else run_dir
    return AuditReport.model_validate_json(path.read_text())


def compare(reports: list[AuditReport]) -> dict[str, Any]:
    """Per-requirement verdict stability and cost/time spread across runs."""
    per_req: dict[str, list[str | None]] = {}
    ids = sorted({rv.requirement_id for r in reports for rv in r.requirements})
    for rid in ids:
        per_req[rid] = [next((rv.status for rv in r.requirements if rv.requirement_id == rid), None) for r in reports]
    unstable = {rid: sts for rid, sts in per_req.items() if len(set(sts)) > 1}
    costs = [r.cost.total_usd for r in reports]
    walls = [(r.finished_at - r.started_at).total_seconds() for r in reports]

    def spread(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {}
        return {"mean": statistics.fmean(xs), "min": min(xs), "max": max(xs),
                "stdev": statistics.stdev(xs) if len(xs) > 1 else 0.0}

    return {
        "runs": [r.run_id for r in reports],
        "requirements": len(ids),
        "stable_requirements": len(ids) - len(unstable),
        "stability_rate": ((len(ids) - len(unstable)) / len(ids)) if ids else 1.0,
        "unstable": unstable,
        "majority": {rid: Counter(sts).most_common(1)[0][0] for rid, sts in per_req.items()},
        "status_counts": [dict(Counter(rv.status for rv in r.requirements)) for r in reports],
        "defects": [len(r.defects) for r in reports],
        "cost_usd": spread(costs),
        "wall_time_s": spread(walls),
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    agent_args: list[str] = []
    if "--" in argv:
        k = argv.index("--")
        argv, agent_args = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=3, help="number of agent runs")
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument("--reports", nargs="*", type=Path, help="analyse these existing run dirs instead of running")
    args = ap.parse_args(argv)

    runs_dir = _runs_dir(agent_args, args.runs_dir)
    run_dirs: list[Path] = []
    if args.reports:
        run_dirs = list(args.reports)
    else:
        if not agent_args:
            ap.error("pass agent arguments after `--`, e.g. -- --url URL --prd PRD.md")
        for i in range(args.n):
            print(f"[multi_run] run {i + 1}/{args.n}: {' '.join(_command() + agent_args)}", flush=True)
            d, rc, elapsed = run_once(agent_args, runs_dir)
            if d is None:
                print(f"[multi_run] run {i + 1} produced no report.json (exit {rc}, {elapsed:.0f}s)", flush=True)
                continue
            run_dirs.append(d)
    if not run_dirs:
        print("[multi_run] no reports to compare")
        return 1

    result = compare([load(d) for d in run_dirs])
    result["run_dirs"] = [str(d) for d in run_dirs]
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / f"multi_run_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(result, indent=2))

    c = result["cost_usd"]
    print(f"runs: {len(run_dirs)}  requirements: {result['requirements']}  "
          f"stable: {result['stable_requirements']} ({result['stability_rate']:.0%})")
    print(f"cost $: mean {c['mean']:.4f}  min {c['min']:.4f}  max {c['max']:.4f}  stdev {c['stdev']:.4f}")
    for rid, sts in result["unstable"].items():
        print(f"  unstable {rid}: {sts}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
