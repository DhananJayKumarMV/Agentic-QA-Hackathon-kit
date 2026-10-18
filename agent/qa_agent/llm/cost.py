"""Per-agent token accounting and the budget guard."""
from collections import defaultdict

from ..config import PRICING
from ..contracts import CostReport, LlmCall


class BudgetExceeded(RuntimeError):
    pass


def price(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    p_in, p_out, p_read, p_write = PRICING[model]
    return (
        input_tokens * p_in + output_tokens * p_out + cache_read * p_read + cache_write * p_write
    ) / 1_000_000


class CostTracker:
    """Records every LLM call and enforces the run budget.

    At 80% of budget, optional calls (judge, triage, summaries) are refused so the
    caller can mark those checks unverified. At 100%, all calls are refused.

    Warm vs cold: `usd` is what this run actually paid. A call served from the on-disk
    artifact cache costs $0 here, so a warm rerun is cheap. To report what the same run
    would cost from scratch, each bucket also carries `cold_usd`: live calls count at
    their real cost, and artifact-cache hits count at the cost the original call paid
    (stored next to the cached artifact). A cold run still uses the *prompt* cache
    within the run (planner calls share one cached system prompt), so `cold_usd` is the
    realistic first-run cost, not an uncached-token upper bound. Hits whose original
    cost is unknown (cached before costs were recorded) add 0 and are counted in
    `cold_unknown_hits`.
    """

    SOFT_LIMIT = 0.8

    def __init__(self, budget_usd: float):
        self.budget_usd = budget_usd
        self.calls: list[LlmCall] = []
        self.degraded = False
        # Original cost of artifact-cache hits, by index into `calls`.
        self.cold_usd: dict[int, float] = {}

    @property
    def total_usd(self) -> float:
        return sum(c.usd for c in self.calls)

    def check(self, optional: bool) -> None:
        spent = self.total_usd
        if spent >= self.budget_usd:
            self.degraded = True
            raise BudgetExceeded(f"budget ${self.budget_usd:.2f} reached (${spent:.4f} spent)")
        if optional and spent >= self.budget_usd * self.SOFT_LIMIT:
            self.degraded = True
            raise BudgetExceeded(f"soft limit reached (${spent:.4f} of ${self.budget_usd:.2f}); skipping optional call")

    def record(self, call: LlmCall, cold_usd: float | None = None) -> None:
        """Record a call. For artifact-cache hits, `cold_usd` is what the original call cost."""
        if cold_usd is not None:
            self.cold_usd[len(self.calls)] = cold_usd
        self.calls.append(call)

    @property
    def cold_total_usd(self) -> float:
        return sum(self.cold_usd.get(i, c.usd) for i, c in enumerate(self.calls))

    def report(self) -> CostReport:
        by_agent: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for i, c in enumerate(self.calls):
            cold = self.cold_usd.get(i, c.usd)
            unknown = int(c.cached_artifact_hit and i not in self.cold_usd)
            for bucket in (by_agent[c.agent], by_model[c.model]):
                bucket["calls"] += 1
                bucket["input_tokens"] += c.input_tokens
                bucket["output_tokens"] += c.output_tokens
                bucket["cache_read"] += c.cache_read
                bucket["cache_write"] += c.cache_write
                bucket["usd"] += c.usd
                bucket["artifact_cache_hits"] += int(c.cached_artifact_hit)
                bucket["cold_usd"] += cold
                bucket["cold_unknown_hits"] += unknown
        return CostReport(
            calls=self.calls,
            by_agent={k: dict(v) for k, v in by_agent.items()},
            by_model={k: dict(v) for k, v in by_model.items()},
            total_usd=round(self.total_usd, 6),
            budget_usd=self.budget_usd,
            degraded=self.degraded,
        )
