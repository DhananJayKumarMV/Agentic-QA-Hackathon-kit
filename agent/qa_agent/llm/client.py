"""The single entry point for every Claude call made by the agents.

Handles model choice, prompt caching, structured output, the on-disk artifact
cache, refusal handling and cost accounting. Agents never import `anthropic`
directly, so tests can swap in FakeLLM and spend no tokens.
"""
import logging
import time
from pathlib import Path
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from ..cache.artifacts import ArtifactCache, cache_key
from ..config import REASONING_MODEL
from ..contracts import LlmCall
from .cost import CostTracker, price

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class LlmRefusal(RuntimeError):
    pass


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


class LLM(Protocol):
    tracker: CostTracker

    async def complete(
        self,
        *,
        agent: str,
        purpose: str,
        model: str,
        system: str,
        user: str,
        output_model: type[T] | None = None,
        effort: str | None = None,
        max_tokens: int = 16000,
        optional: bool = False,
        cache_parts: tuple[Any, ...] | None = None,
    ) -> T | str: ...


class ClaudeLLM:
    """Real client. `system` is the static, cacheable prefix; `user` carries the variable part.

    Accounting: `usage.output_tokens` already includes adaptive-thinking tokens (thinking is
    billed as output even when its text is omitted), so `price()` over the usage block is the
    exact bill. `cache_read` / `cache_write` are logged per call so prompt-cache hits can be
    verified (a call that should share a prefix but shows cache_read=0 has a silent invalidator).
    """

    def __init__(self, tracker: CostTracker, cache: ArtifactCache, refusal_fallback: bool = True):
        self.client = anthropic.AsyncAnthropic(max_retries=3)
        self.tracker = tracker
        self.cache = cache
        self.refusal_fallback = refusal_fallback

    async def complete(
        self,
        *,
        agent: str,
        purpose: str,
        model: str,
        system: str,
        user: str,
        output_model: type[T] | None = None,
        effort: str | None = None,
        max_tokens: int = 16000,
        optional: bool = False,
        cache_parts: tuple[Any, ...] | None = None,
    ) -> T | str:
        key = None
        if cache_parts is not None:
            schema = output_model.model_json_schema() if output_model else None
            key = cache_key(model, system, user, schema, *cache_parts)
            hit = self.cache.get(agent, key)
            if hit is not None:
                meta = self.cache.get_meta(agent, key) or {}
                orig = meta.get("usd")
                self.tracker.record(
                    LlmCall(agent=agent, model=model, purpose=purpose, cached_artifact_hit=True),
                    cold_usd=float(orig) if isinstance(orig, (int, float)) else None,
                )
                return output_model.model_validate(hit) if output_model else hit

        self.tracker.check(optional=optional)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # Static instructions first with a cache breakpoint; variable content after it.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        if effort:
            kwargs["output_config"] = {"effort": effort}
        if model == REASONING_MODEL and self.refusal_fallback:
            # Server-side refusal fallback (Claude API): reroutes a declined request by category.
            kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kwargs["extra_body"] = {"fallbacks": "default"}

        start = time.monotonic()
        # Always stream: large plans need high max_tokens, which the SDK refuses without streaming.
        if output_model:
            kwargs["output_format"] = output_model
        async with self.client.messages.stream(**kwargs) as stream:
            response = await stream.get_final_message()
        latency_ms = int((time.monotonic() - start) * 1000)

        u = response.usage
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        # output_tokens includes thinking tokens; there is no separate thinking charge.
        call = LlmCall(
            agent=agent, model=model, purpose=purpose,
            input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cache_read=cache_read, cache_write=cache_write,
            usd=price(model, u.input_tokens, u.output_tokens, cache_read, cache_write),
            latency_ms=latency_ms,
        )
        self.tracker.record(call)
        log.info(
            "llm %s/%s %s: in=%d out=%d cache_read=%d cache_write=%d $%.4f %dms",
            agent, purpose, model, call.input_tokens, call.output_tokens, cache_read, cache_write,
            call.usd, latency_ms,
        )
        meta = {"usd": call.usd, "model": model, "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens, "cache_read": cache_read, "cache_write": cache_write}

        if response.stop_reason == "refusal":
            raise LlmRefusal(f"{agent}/{purpose}: model declined the request")
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"{agent}/{purpose}: output truncated at max_tokens={max_tokens}")

        if output_model:
            result: Any = response.parsed_output
            if key:
                self.cache.put(agent, key, result.model_dump(mode="json"), meta=meta)
            return result
        text = "".join(b.text for b in response.content if b.type == "text")
        if key:
            self.cache.put(agent, key, text, meta=meta)
        return text


class FakeLLM:
    """Test double: replays canned responses keyed by (agent, purpose). Costs nothing."""

    def __init__(self, responses: dict[tuple[str, str], Any], tracker: CostTracker | None = None):
        self.responses = responses
        self.tracker = tracker or CostTracker(budget_usd=1.0)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        agent: str,
        purpose: str,
        model: str,
        system: str,
        user: str,
        output_model: type[T] | None = None,
        effort: str | None = None,
        max_tokens: int = 16000,
        optional: bool = False,
        cache_parts: tuple[Any, ...] | None = None,
    ) -> T | str:
        self.tracker.check(optional=optional)
        self.calls.append({"agent": agent, "purpose": purpose, "model": model, "system": system, "user": user})
        if (agent, purpose) not in self.responses:
            raise KeyError(f"FakeLLM has no response for ({agent!r}, {purpose!r})")
        raw = self.responses[(agent, purpose)]
        self.tracker.record(LlmCall(agent=agent, model=model, purpose=purpose))
        return output_model.model_validate(raw) if output_model else raw
