"""qa-agent command line. `run` is the full pipeline; the others run one stage for debugging and demos."""
import asyncio
import logging
from pathlib import Path

import typer
from dotenv import load_dotenv

from .cache.artifacts import ArtifactCache
from .config import RunConfig
from .contracts import AuditReport, RequirementSet, SiteMap
from .llm.client import ClaudeLLM
from .llm.cost import CostTracker

app = typer.Typer(add_completion=False, help="Spec-driven multi-agent QA for web apps.")


def _setup(verbose: bool) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")


def _llm(budget: float, cache_dir: Path, use_cache: bool) -> ClaudeLLM:
    return ClaudeLLM(CostTracker(budget), ArtifactCache(cache_dir, enabled=use_cache))


@app.command()
def run(
    url: str = typer.Option(..., help="Target application URL"),
    prd: Path = typer.Option(..., exists=True, help="PRD markdown file"),
    out: Path = typer.Option(Path("runs"), help="Output directory"),
    budget_usd: float = typer.Option(1.0, help="Hard LLM budget for the run"),
    read_only: bool = typer.Option(False, help="Skip scenarios that change data"),
    headed: bool = typer.Option(False, help="Show the browser"),
    reset_cmd: str = typer.Option(None, help="Local only: command that resets target data before the run"),
    only: list[str] = typer.Option(None, help="Requirement id globs to run, e.g. 'REQ-TRF-*'"),
    no_cache: bool = typer.Option(False, help="Ignore the on-disk LLM artifact cache"),
    parallel: int = typer.Option(3, min=1, help="Browser contexts for parallel-safe read-only scenarios (1 = serial)"),
    scenario_timeout_s: float = typer.Option(90.0, min=5.0, help="Wall-clock limit per scenario attempt"),
    chaos_latency_ms: int = typer.Option(0, min=0, help="Chaos: add random 0..N ms latency to the app's API requests"),
    chaos_error_rate: float = typer.Option(0.0, min=0.0, max=1.0,
                                           help="Chaos: answer this fraction of GET API requests with HTTP 503 "
                                                "(mutations are never failed)"),
    chaos_seed: int = typer.Option(0, help="Chaos: RNG seed (decisions are reproducible per seed)"),
    repo: str = typer.Option(None, help="Optional target source (directory or git URL). Context only: its "
                                        "client-side routes are added to the crawl; verdicts never use it"),
    verbose: bool = typer.Option(False, "-v"),
) -> None:
    """Run the full pipeline and write report.{json,md,html,pdf}."""
    from .orchestrator import run_pipeline

    _setup(verbose)
    cfg = RunConfig(target_url=url, prd_path=prd, out_dir=out, budget_usd=budget_usd, read_only=read_only,
                    headed=headed, reset_cmd=reset_cmd, only=only or [], use_cache=not no_cache,
                    parallel=parallel, scenario_timeout_s=scenario_timeout_s, chaos_latency_ms=chaos_latency_ms,
                    chaos_error_rate=chaos_error_rate, chaos_seed=chaos_seed, repo=repo)
    asyncio.run(run_pipeline(cfg))


@app.command()
def extract(prd: Path = typer.Option(..., exists=True), out: Path = typer.Option(Path("requirements.json")),
            verbose: bool = typer.Option(False, "-v")) -> None:
    """PRD -> requirements.json"""
    from .agents.spec_analyst import extract_requirements

    _setup(verbose)
    llm = _llm(1.0, Path(".qa_cache"), True)
    reqs = asyncio.run(extract_requirements(prd.read_text(encoding="utf-8"), llm))
    out.write_text(reqs.model_dump_json(indent=2))
    typer.echo(f"{len(reqs.requirements)} requirements -> {out}  (${llm.tracker.total_usd:.4f})")


@app.command()
def explore(url: str = typer.Option(...), out: Path = typer.Option(Path("sitemap.json")),
            headed: bool = typer.Option(False), no_llm: bool = typer.Option(False, help="Heuristic labels only"),
            verbose: bool = typer.Option(False, "-v")) -> None:
    """Live site -> sitemap.json (never submits forms)."""
    from .agents.explorer import explore as do_explore
    from .browser.session import launch

    _setup(verbose)
    llm = None if no_llm else _llm(1.0, Path(".qa_cache"), True)

    async def go() -> SiteMap:
        browser = await launch(url, headed=headed, evidence_dir=Path("runs/_explore"), llm=llm)
        try:
            return await do_explore(browser, llm)
        finally:
            await browser.close()

    sitemap = asyncio.run(go())
    out.write_text(sitemap.model_dump_json(indent=2))
    typer.echo(f"{len(sitemap.pages)} pages -> {out}")


@app.command()
def plan(requirements: Path = typer.Option(..., exists=True), sitemap: Path = typer.Option(..., exists=True),
         out: Path = typer.Option(Path("plan.json")), verbose: bool = typer.Option(False, "-v")) -> None:
    """requirements.json + sitemap.json -> plan.json"""
    from .agents.test_planner import plan_tests

    _setup(verbose)
    llm = _llm(2.0, Path(".qa_cache"), True)
    reqs = RequirementSet.model_validate_json(requirements.read_text())
    sm = SiteMap.model_validate_json(sitemap.read_text())
    result = asyncio.run(plan_tests(reqs, sm, llm))
    out.write_text(result.model_dump_json(indent=2))
    typer.echo(f"{len(result.scenarios)} scenarios -> {out}  (${llm.tracker.total_usd:.4f})")


@app.command()
def report(run_dir: Path = typer.Argument(..., exists=True), pdf: bool = typer.Option(True)) -> None:
    """Re-render report.{md,html,pdf} from a run's report.json."""
    from .agents.reporter import render, render_pdf

    rep = AuditReport.model_validate_json((run_dir / "report.json").read_text())
    written = render(rep, run_dir, ("md", "html"), evidence_base_dirs=[run_dir])
    if pdf:
        written["pdf"] = asyncio.run(render_pdf(written["html"]))
    for k, v in written.items():
        typer.echo(f"{k}: {v}")


@app.command()
def cost(run_dir: Path = typer.Argument(..., exists=True)) -> None:
    """Print the cost breakdown of a run."""
    from .agents.reporter import cost_summary

    rep = AuditReport.model_validate_json((run_dir / "report.json").read_text())
    s = cost_summary(rep.cost)
    typer.echo(f"Total: ${rep.cost.total_usd:.4f} of ${rep.cost.budget_usd:.2f} budget")
    for agent, b in sorted(rep.cost.by_agent.items()):
        typer.echo(f"  {agent:<14} calls={int(b.get('calls', 0)):>3}  in={int(b.get('input_tokens', 0)):>7}  "
                   f"out={int(b.get('output_tokens', 0)):>6}  cache_read={int(b.get('cache_read', 0)):>7}  "
                   f"${b.get('usd', 0):.4f}")
    if "estimated_cold_usd" in s:
        typer.echo(f"Estimated cold-run cost: ${s['estimated_cold_usd']:.4f}")


if __name__ == "__main__":
    app()
