"""Token and dollar accounting for every LLM call."""
from pydantic import BaseModel, ConfigDict


class LlmCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str
    model: str
    purpose: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    usd: float = 0.0
    cached_artifact_hit: bool = False  # served from the on-disk artifact cache, no API call
    latency_ms: int = 0


class CostReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[LlmCall] = []
    by_agent: dict[str, dict[str, float]] = {}
    by_model: dict[str, dict[str, float]] = {}
    total_usd: float = 0.0
    budget_usd: float
    degraded: bool = False  # optional LLM checks were dropped to stay within budget
