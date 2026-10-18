"""Playwright implementation of BrowserPort (async API, Chromium)."""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright

from ..contracts import Evidence, NetworkEvent, SiteMap, Target
from . import a11y, waits
from .chaos import Chaos, ChaosConfig
from .locators import LocatorResolver

log = logging.getLogger(__name__)

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
TRACKED_TYPES = {"xhr", "fetch", "document"}
API_TYPES = {"xhr", "fetch"}
STALE_REQUEST_S = 8.0  # in-flight requests older than this are ignored by settle (polling, held)
MAX_TEXT_BODY = 4_000
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


_SECRET_KEY = re.compile(r"pass(word)?|secret|token|authorization|cookie|api[_-]?key|otp|pin$", re.I)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _parse_body(text: str | None) -> Any:
    if text is None or text == "":
        return None
    try:
        return _redact(json.loads(text))
    except (ValueError, TypeError):
        return text[:MAX_TEXT_BODY]


class PlaywrightElement:
    """ResolvedElement over a Playwright Locator."""

    def __init__(self, locator: Locator, timeout_ms: int = 10_000):
        self.locator = locator
        self.timeout_ms = timeout_ms

    async def _one(self) -> Locator:
        return self.locator.first if await self.locator.count() > 1 else self.locator

    async def click(self) -> None:
        await (await self._one()).click(timeout=self.timeout_ms)

    async def fill(self, value: str) -> None:
        await (await self._one()).fill(value, timeout=self.timeout_ms)

    async def select(self, value: str) -> None:
        loc = await self._one()
        try:
            await loc.select_option(value=value, timeout=2_000)
            return
        except Exception:
            pass
        try:
            await loc.select_option(label=value, timeout=2_000)
            return
        except Exception:
            pass
        # Last resort: first option whose label contains the value (case-insensitive).
        options = await loc.locator("option").evaluate_all(
            "opts => opts.map(o => ({value: o.value, label: (o.label || o.textContent || '').trim()}))"
        )
        for opt in options:
            if value.lower() in opt["label"].lower():
                await loc.select_option(value=opt["value"], timeout=self.timeout_ms)
                return
        raise ValueError(f"no option matching {value!r}; options: {[o['label'] for o in options]}")

    async def text(self) -> str:
        return (await (await self._one()).inner_text(timeout=self.timeout_ms)).strip()

    async def all_texts(self) -> list[str]:
        return [t.strip() for t in await self.locator.all_inner_texts()]

    async def attribute(self, name: str) -> str | None:
        return await (await self._one()).get_attribute(name, timeout=self.timeout_ms)

    async def is_visible(self) -> bool:
        n = await self.locator.count()
        for i in range(min(n, 20)):
            if await self.locator.nth(i).is_visible():
                return True
        return False

    async def is_disabled(self) -> bool:
        loc = await self._one()
        if await loc.is_disabled():
            return True
        return (await loc.get_attribute("aria-disabled")) == "true"

    async def count(self) -> int:
        return await self.locator.count()


class PlaywrightBrowser:
    """BrowserPort implementation. Extra (non-port) helpers are documented inline."""

    def __init__(self, base_url: str, *, headed: bool, evidence_dir: Path, llm: Any, sitemap: SiteMap | None,
                 chaos: ChaosConfig | Chaos | None = None, name: str = ""):
        self.base_url = base_url.rstrip("/")
        self.headed = headed
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.resolver = LocatorResolver(sitemap, llm)
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._viewport = dict(DEFAULT_VIEWPORT)
        self._t0 = time.monotonic()
        self._log: list[NetworkEvent] = []
        # Browser console errors/warnings and uncaught page errors, in order (never cleared).
        self.console_log: list[dict[str, Any]] = []
        self._all_log: list[NetworkEvent] = []  # never cleared; used for API base detection
        self._inflight: dict[Any, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._api_base_override: str | None = sitemap.api_base if sitemap else None
        self._delays: list[tuple[str, Any]] = []
        self._guard_allow: list[str] | None = None  # None = mutation guard off
        self.blocked_requests: list[str] = []
        self.trace_paths: list[Path] = []
        self._ctx_n = 0
        self.last_settle: dict[str, Any] = {}
        if isinstance(chaos, ChaosConfig):
            chaos = Chaos(chaos) if chaos.enabled else None
        self.chaos: Chaos | None = chaos
        self.name = name  # worker label; prefixes trace files so parallel workers never collide
        self._parent: PlaywrightBrowser | None = None
        self._last_storage_state: Any = None
        self._last_url: str | None = None
        self._relaunch_lock = asyncio.Lock()
        self.relaunches = 0

    # ------------------------------------------------------------------ lifecycle
    @property
    def sitemap(self) -> SiteMap | None:
        return self.resolver.sitemap

    @sitemap.setter
    def sitemap(self, value: SiteMap | None) -> None:
        self.resolver.sitemap = value
        if value is not None and value.api_base:
            self._api_base_override = value.api_base

    async def _start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=not self.headed)
        await self._open_context(storage_state=None)

    async def _open_context(self, storage_state: Any) -> None:
        assert self._browser is not None
        self._ctx_n += 1
        self.context = await self._browser.new_context(
            viewport=self._viewport, storage_state=storage_state, base_url=self.base_url
        )
        self.context.set_default_timeout(15_000)
        await self.context.add_init_script(waits.MUTATION_OBSERVER_JS)
        try:
            await self.context.tracing.start(screenshots=True, snapshots=True, sources=False)
        except Exception as exc:
            log.warning("tracing unavailable: %s", exc)
        self.context.on("request", self._on_request)
        self.context.on("requestfinished", self._on_finished)
        self.context.on("requestfailed", self._on_failed)
        # Routes run in reverse registration order: chaos first registered = runs last, after
        # the mutation guard (which may abort) and any delay_requests holds.
        if self.chaos is not None:
            await self.chaos.install(self.context)
        for glob, handler in self._delays:
            await self.context.route(glob, handler)
        if self._guard_allow is not None:
            await self.context.route("**/*", self._guard_handler)
        self.page = await self.context.new_page()
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_pageerror)

    async def _close_context(self) -> None:
        if self.context is None:
            return
        await self._drain()
        try:
            prefix = f"trace-{self.name}-" if self.name else "trace-"
            path = self.evidence_dir / f"{prefix}{self._ctx_n}.zip"
            await self.context.tracing.stop(path=str(path))
            self.trace_paths.append(path)
        except Exception as exc:
            log.debug("tracing stop failed: %s", exc)
        try:
            await self.context.close()
        except Exception:
            pass
        self.context = None
        self.page = None
        self._inflight.clear()

    def _on_console(self, msg: Any) -> None:
        if msg.type in ("error", "warning"):
            self.console_log.append({"type": msg.type, "text": msg.text[:500],
                                     "t_ms": int((time.monotonic() - self._t0) * 1000)})

    def _on_pageerror(self, err: Any) -> None:
        self.console_log.append({"type": "pageerror", "text": str(err)[:500],
                                 "t_ms": int((time.monotonic() - self._t0) * 1000)})

    async def new_context(self, fresh: bool = False) -> None:
        state = None
        if not fresh and self.context is not None:
            try:
                state = await asyncio.wait_for(self.context.storage_state(), 10)
            except Exception:
                state = self._last_storage_state
        if state is not None:
            self._last_storage_state = state
        await self._close_context()
        await self._ensure_browser()
        await self._open_context(storage_state=state)

    async def close(self) -> None:
        await self._close_context()
        if self._parent is not None:  # a fork shares its parent's Chromium; never close it
            return
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------------ resilience (extras)
    def _browser_connected(self) -> bool:
        try:
            return self._browser is not None and self._browser.is_connected()
        except Exception:
            return False

    async def _ensure_browser(self) -> None:
        """Relaunch Chromium if it died. Forks ask their parent so all workers share one instance."""
        if self._parent is not None:
            await self._parent._ensure_browser()
            self._browser = self._parent._browser
            return
        async with self._relaunch_lock:
            if self._browser_connected():
                return
            log.warning("browser disconnected; relaunching")
            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=not self.headed)
            self.relaunches += 1

    async def remember_session(self) -> None:
        """Extra: cache the current cookies/storage so a crash can be recovered without re-login."""
        if self.context is None:
            return
        try:
            self._last_storage_state = await asyncio.wait_for(self.context.storage_state(), 10)
            if self.page is not None and self.page.url.startswith("http"):
                self._last_url = self.page.url
        except Exception:
            pass

    async def _resume_at(self, url: str | None) -> None:
        """Load `url` in a freshly opened context so scenarios never start on about:blank."""
        if not url or not url.startswith("http"):
            return
        try:
            await self.goto(url)
        except Exception as exc:
            log.debug("resume at %s failed: %s", url, exc)

    async def is_healthy(self, timeout_s: float = 5.0) -> bool:
        """Extra: True if Chromium is connected and the page answers a trivial script."""
        if not self._browser_connected() or self.context is None or self.page is None or self.page.is_closed():
            return False
        try:
            return await asyncio.wait_for(self.page.evaluate("() => 1"), timeout_s) == 1
        except Exception:
            return False

    async def ensure_alive(self) -> list[str]:
        """Extra: repair a crashed page/context/browser. Returns observations (empty if healthy)."""
        if await self.is_healthy():
            return []
        obs: list[str] = []
        was_connected = self._browser_connected()
        if not was_connected:
            obs.append("browser process lost; relaunched Chromium")
            self.context = None  # the old context died with the process
            self.page = None
            self._inflight.clear()
        else:
            obs.append("page or context unresponsive; opened a new context")
            # Close the dead context without blocking on a hung trace export.
            ctx, self.context, self.page = self.context, None, None
            self._inflight.clear()
            if ctx is not None:
                try:
                    await asyncio.wait_for(ctx.close(), 10)
                except Exception:
                    pass
        await self._ensure_browser()
        await self._open_context(storage_state=self._last_storage_state)
        await self._resume_at(self._last_url or self.base_url)
        return obs

    async def set_chaos(self, chaos: ChaosConfig | Chaos | None) -> None:
        """Extra: enable/disable chaos on the current context and every later one."""
        if isinstance(chaos, ChaosConfig):
            chaos = Chaos(chaos) if chaos.enabled else None
        if self.chaos is not None and self.context is not None:
            await self.chaos.uninstall(self.context)
        self.chaos = chaos
        if chaos is not None and self.context is not None:
            await chaos.install(self.context)

    async def fork(self, name: str) -> "PlaywrightBrowser":
        """Extra: a sibling BrowserPort in its own context on the same Chromium, carrying this
        session's cookies/storage, sitemap, LLM and chaos settings. Used for parallel read-only work.
        """
        assert self._browser is not None
        child = PlaywrightBrowser(self.base_url, headed=self.headed, evidence_dir=self.evidence_dir, llm=self.llm,
                                  sitemap=self.sitemap, name=name,
                                  chaos=self.chaos.config if self.chaos else None)
        child._parent = self
        child._browser = self._browser
        child._api_base_override = self._api_base_override or self.api_base
        state = None
        if self.context is not None:
            try:
                state = await self.context.storage_state()
            except Exception:
                state = self._last_storage_state
        child._last_storage_state = state
        await child._open_context(storage_state=state)
        url = self.page.url if self.page is not None else None
        await child._resume_at(url if url and url.startswith("http") else self.base_url)
        return child

    # ------------------------------------------------------------------ network capture
    def _t(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def _on_request(self, request: Any) -> None:
        if request.resource_type in TRACKED_TYPES:
            self._inflight[request] = time.monotonic()

    def _on_failed(self, request: Any) -> None:
        self._inflight.pop(request, None)
        if request.resource_type in API_TYPES:
            ev = NetworkEvent(method=request.method, url=request.url, status=0,
                              req_body=_parse_body(request.post_data), resp_body=None, t_ms=self._t())
            self._log.append(ev)
            self._all_log.append(ev)

    def _on_finished(self, request: Any) -> None:
        if request.resource_type not in API_TYPES:
            self._inflight.pop(request, None)
            return
        task = asyncio.ensure_future(self._record(request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _record(self, request: Any) -> None:
        try:
            response = await request.response()
            status = response.status if response else 0
            body: Any = None
            if response is not None and request.method != "OPTIONS":
                try:
                    body = _parse_body(await response.text())
                except Exception:
                    body = None
            ev = NetworkEvent(method=request.method, url=request.url, status=status,
                              req_body=_parse_body(request.post_data), resp_body=body, t_ms=self._t())
            if request.method != "OPTIONS":
                self._log.append(ev)
                self._all_log.append(ev)
        except Exception as exc:
            log.debug("network record failed: %s", exc)
        finally:
            self._inflight.pop(request, None)

    async def _drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def inflight_count(self) -> int:
        now = time.monotonic()
        return sum(1 for t in self._inflight.values() if now - t < STALE_REQUEST_S)

    def network_log(self) -> list[NetworkEvent]:
        return list(self._log)

    def clear_network_log(self) -> None:
        self._log.clear()

    async def flush_network(self) -> list[NetworkEvent]:
        """Extra: wait for pending response bodies, then return the log."""
        await self._drain()
        return self.network_log()

    @property
    def api_base(self) -> str | None:
        """API origin: SiteMap value if known, else the origin serving most JSON XHR/fetch traffic."""
        if self._api_base_override:
            return self._api_base_override
        counts = Counter(
            _origin(e.url) for e in self._all_log
            if isinstance(e.resp_body, (dict, list)) and e.url.startswith("http")
        )
        if counts:
            return counts.most_common(1)[0][0]
        return None

    def all_network(self) -> list[NetworkEvent]:
        """Extra: every captured API event since launch (not cleared by clear_network_log)."""
        return list(self._all_log)

    async def api_request(self, method: str, path: str, body: Any = None) -> NetworkEvent:
        assert self.context is not None
        await self._drain()
        if path.startswith("http"):
            url = path
        else:
            base = self.api_base or self.base_url
            url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        kwargs: dict[str, Any] = {"method": method.upper(), "headers": {"Origin": _origin(self.base_url)}}
        if body is not None:
            kwargs["data"] = body
        t = self._t()
        resp = await self.context.request.fetch(url, **kwargs)
        try:
            text = await resp.text()
        except Exception:
            text = None
        return NetworkEvent(method=method.upper(), url=url, status=resp.status,
                            req_body=body, resp_body=_parse_body(text), t_ms=t)

    async def delay_requests(self, method: str, url_glob: str, delay_ms: int) -> None:
        assert self.context is not None
        glob = url_glob if url_glob.startswith(("http", "*")) else "**" + url_glob
        want = method.upper()

        async def handler(route: Any, request: Any) -> None:
            if want in ("*", "ANY") or request.method == want:
                await asyncio.sleep(delay_ms / 1000)
            await route.fallback()

        self._delays.append((glob, handler))
        await self.context.route(glob, handler)

    async def clear_delays(self) -> None:
        """Extra: remove all delay_requests routes."""
        if self.context is not None:
            for glob, handler in self._delays:
                try:
                    await self.context.unroute(glob, handler)
                except Exception:
                    pass
        self._delays.clear()

    async def _guard_handler(self, route: Any, request: Any) -> None:
        allow = self._guard_allow
        if allow is not None and request.method in MUTATING_METHODS and request.resource_type in API_TYPES | {"document"}:
            if not any(fnmatch.fnmatch(request.url, g) for g in allow):
                self.blocked_requests.append(f"{request.method} {request.url}")
                await route.abort("blockedbyclient")
                return
        await route.fallback()

    async def set_mutation_guard(self, enabled: bool, allow: list[str] | None = None) -> None:
        """Extra: when enabled, abort every POST/PUT/PATCH/DELETE not matching an `allow` glob.

        Used by the Explorer so that browsing (including GET-only filter forms) never changes data.
        """
        assert self.context is not None
        if enabled and self._guard_allow is None:
            self._guard_allow = list(allow or [])
            await self.context.route("**/*", self._guard_handler)
        elif enabled:
            self._guard_allow = list(allow or [])
        elif not enabled and self._guard_allow is not None:
            self._guard_allow = None
            try:
                await self.context.unroute("**/*", self._guard_handler)
            except Exception:
                pass

    # ------------------------------------------------------------------ navigation
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else urljoin(self.base_url + "/", path.lstrip("/"))

    async def goto(self, path: str) -> None:
        assert self.page is not None
        await self.page.goto(self._url(path), wait_until="domcontentloaded")
        await self.settle()

    async def reload(self) -> None:
        assert self.page is not None
        await self.page.reload(wait_until="domcontentloaded")
        await self.settle()

    async def back(self) -> None:
        assert self.page is not None
        await self.page.go_back(wait_until="domcontentloaded")
        await self.settle()

    async def set_viewport(self, width: int, height: int) -> None:
        assert self.page is not None
        self._viewport = {"width": width, "height": height}
        await self.page.set_viewport_size(self._viewport)

    async def current_url(self) -> str:
        assert self.page is not None
        return self.page.url

    async def settle(self, timeout_ms: int = 10_000) -> None:
        assert self.page is not None
        self.last_settle = await waits.settle(self.page, inflight=self.inflight_count, timeout_ms=timeout_ms)
        await self._drain()

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        assert self.page is not None
        return await self.page.evaluate(js, arg)

    # ------------------------------------------------------------------ elements
    async def resolve(self, target: Target, *, wait_ms: int = 3_000, heal: bool = True) -> tuple[PlaywrightElement, str, bool]:
        """Resolve a Target. Extras: `wait_ms` (poll for appearance), `heal=False` skips the LLM."""
        assert self.page is not None
        loc, desc, healed = await self.resolver.resolve(self.page, target, wait_ms=wait_ms, heal=heal)
        return PlaywrightElement(loc), desc, healed

    async def screenshot(self, name: str, highlight: Target | None = None) -> Evidence:
        assert self.page is not None
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "shot"
        path = self.evidence_dir / f"{safe}.png"
        handle = None
        caption = name
        if highlight is not None:
            try:
                loc, desc, _ = await self.resolver.resolve(self.page, highlight, wait_ms=500, heal=False)
                handle = loc.first if await loc.count() > 1 else loc
                await handle.evaluate(
                    "el => { el.dataset.qaOutline = el.style.outline; el.style.outline = '3px solid #e5007a';"
                    " el.style.outlineOffset = '2px'; el.scrollIntoView({block: 'center'}); }"
                )
                caption = f"{name} (highlight: {highlight.key})"
            except Exception as exc:
                log.debug("highlight failed: %s", exc)
                handle = None
        try:
            await self.page.screenshot(path=str(path), full_page=False)
        finally:
            if handle is not None:
                try:
                    await handle.evaluate("el => { el.style.outline = el.dataset.qaOutline || ''; el.style.outlineOffset = ''; }")
                except Exception:
                    pass
        return Evidence(kind="screenshot", path=str(path), caption=caption)

    async def aria_snapshot(self, max_chars: int = 12_000) -> str:
        assert self.page is not None
        return await a11y.snapshot(self.page, max_chars=max_chars)

    async def recover(self, *, expect_logged_in: bool = True) -> list[str]:
        """Extra: run generic recovery handlers (see recovery.py); returns observations."""
        from .recovery import recover

        return await recover(self, expect_logged_in=expect_logged_in)


async def launch(
    base_url: str,
    *,
    headed: bool = False,
    evidence_dir: Path,
    llm: Any = None,
    sitemap: SiteMap | None = None,
    chaos: ChaosConfig | None = None,
) -> PlaywrightBrowser:
    browser = PlaywrightBrowser(base_url, headed=headed, evidence_dir=evidence_dir, llm=llm, sitemap=sitemap,
                                chaos=chaos)
    await browser._start()
    return browser
