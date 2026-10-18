"""Chaos mode: inject latency and transient read failures into the app's own API traffic.

Used to self-test the agent's resilience: with chaos on, verdicts may degrade to
``unverified`` but must never flip between ``passed`` and ``failed``.

Rules (safety first):
- Only XHR/fetch requests issued by the page are affected; documents and assets are not.
- Latency (uniform 0..latency_ms) may be added to any API request.
- Errors (HTTP 503) are injected ONLY for GET requests. Mutations (POST/PUT/PATCH/DELETE)
  are never failed, so chaos can never corrupt or half-apply a change.
- The agent's own API calls (snapshots, compensation) use the context's request client,
  which bypasses page routing, so state bookkeeping is never perturbed.
- Decisions are derived from (seed, method, url, n-th occurrence) so they are reproducible
  regardless of how concurrent requests interleave.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

API_TYPES = {"xhr", "fetch"}
INJECTED_BODY = "Service Unavailable (fault injected by qa-agent chaos mode)"


@dataclass(frozen=True)
class ChaosConfig:
    latency_ms: int = 0
    error_rate: float = 0.0
    seed: int = 0

    @property
    def enabled(self) -> bool:
        return self.latency_ms > 0 or self.error_rate > 0

    def describe(self) -> str:
        return (f"chaos mode: up to {self.latency_ms} ms random latency on API requests, "
                f"{self.error_rate:.0%} of GET API requests answered with HTTP 503 (seed {self.seed}); "
                "mutating requests are never failed")

    def as_dict(self) -> dict[str, Any]:
        return {"latency_ms": self.latency_ms, "error_rate": self.error_rate, "seed": self.seed}


@dataclass
class Decision:
    delay_ms: int
    fail: bool


@dataclass
class Chaos:
    """Route handler implementing a ChaosConfig. One instance may serve several contexts."""

    config: ChaosConfig
    stats: Counter = field(default_factory=Counter)
    injected: list[tuple[str, str]] = field(default_factory=list)  # (method, url) answered with 503
    _seen: Counter = field(default_factory=Counter)

    def decide(self, method: str, url: str) -> Decision:
        method = method.upper()
        key = (method, url)
        n = self._seen[key]
        self._seen[key] += 1
        digest = hashlib.sha256(f"{self.config.seed}|{method}|{url}|{n}".encode()).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))
        delay = int(rng.uniform(0, self.config.latency_ms)) if self.config.latency_ms > 0 else 0
        fail = method == "GET" and self.config.error_rate > 0 and rng.random() < self.config.error_rate
        return Decision(delay, fail)

    def was_injected(self, method: str, url: str) -> bool:
        return (method.upper(), url) in self.injected

    async def handler(self, route: Any, request: Any) -> None:
        if request.resource_type not in API_TYPES or request.method == "OPTIONS":
            await route.fallback()
            return
        d = self.decide(request.method, request.url)
        if d.delay_ms:
            self.stats["delayed"] += 1
            await asyncio.sleep(d.delay_ms / 1000)
        if d.fail:
            self.stats["failed"] += 1
            self.injected.append((request.method.upper(), request.url))
            headers = {"Retry-After": "1", "Cache-Control": "no-store"}
            origin = (request.headers or {}).get("origin")
            if origin:  # cross-origin API: the browser must be allowed to read the error
                headers["Access-Control-Allow-Origin"] = origin
                headers["Access-Control-Allow-Credentials"] = "true"
                headers["Vary"] = "Origin"
            try:
                await route.fulfill(status=503, headers=headers, content_type="text/plain", body=INJECTED_BODY)
            except Exception as exc:  # page/context closing while the request was held
                log.debug("chaos fulfill failed: %s", exc)
            return
        self.stats["passed_through"] += 1
        try:
            await route.fallback()
        except Exception as exc:
            log.debug("chaos fallback failed: %s", exc)

    async def install(self, context: Any) -> None:
        await context.route("**/*", self.handler)

    async def uninstall(self, context: Any) -> None:
        try:
            await context.unroute("**/*", self.handler)
        except Exception:
            pass
