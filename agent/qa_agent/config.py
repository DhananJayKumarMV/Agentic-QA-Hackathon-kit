"""Run configuration and model tiers."""
from dataclasses import dataclass, field
from pathlib import Path

# Model tiers: Sonnet for reasoning-heavy stages, Haiku for cheap high-volume steps.
REASONING_MODEL = "claude-sonnet-5-5"
FAST_MODEL = "claude-haiku-4-5"

# USD per million tokens: (input, output, cache_read, cache_write_5m)
PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}


@dataclass
class RunConfig:
    target_url: str
    prd_path: Path
    out_dir: Path = Path("runs")
    cache_dir: Path = Path(".qa_cache")
    budget_usd: float = 1.00
    read_only: bool = False
    headed: bool = False
    reset_cmd: str | None = None
    only: list[str] = field(default_factory=list)  # requirement id globs
    use_cache: bool = True
    parallel: int = 3  # extra browser contexts for parallel-safe read-only scenarios (<=1 disables)
    scenario_timeout_s: float = 90.0  # wall-clock limit per scenario attempt -> unverified
    # Chaos mode (self-test of resilience): random API latency and 503s on GET API requests only.
    chaos_latency_ms: int = 0
    chaos_error_rate: float = 0.0
    chaos_seed: int = 0
    # Optional target source (local path or git URL): used only to add client-side routes to the crawl.
    repo: str | None = None
    # Server-side refusal fallback on Sonnet calls (Claude API only).
    refusal_fallback: bool = True
