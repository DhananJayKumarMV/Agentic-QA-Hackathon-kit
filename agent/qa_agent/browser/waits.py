"""Generic "page has settled" detection.

A page is settled when, all at once:
  * no tracked network request is in flight,
  * nothing is marked ``aria-busy="true"``,
  * no visible loading message is shown (a progress word followed by an ellipsis,
    e.g. "Loading…" or "Saving changes..."; a bare word such as an account type
    called "Checking" must never count),
  * the DOM has not mutated for ``quiet_ms``.

Timeouts are tolerated: ``settle`` logs and returns a stats dict instead of raising.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

# Shared by Python checks and the in-page probe. The ellipsis is mandatory and the
# text between the keyword and the ellipsis may not cross a line break.
LOADING_PATTERN = (
    r"\b(loading|submitting|checking|saving|processing|please wait|signing (in|out)"
    r"|logging (in|out)|entering|updating|fetching|sending|verifying|connecting)\b"
    r"[^…\n]{0,60}(…|\.\.\.)"
)
LOADING_RE = re.compile(LOADING_PATTERN, re.IGNORECASE)

# Installed both as an init script and lazily; records the time of the last DOM mutation.
MUTATION_OBSERVER_JS = """
(() => {
  if (window.__qaMutationObserverInstalled) return;
  window.__qaMutationObserverInstalled = true;
  window.__qaLastMutation = performance.now();
  const start = () => {
    const root = document.documentElement || document;
    new MutationObserver(() => { window.__qaLastMutation = performance.now(); })
      .observe(root, {subtree: true, childList: true, attributes: true, characterData: true});
  };
  if (document.documentElement) start();
  else document.addEventListener('DOMContentLoaded', start, {once: true});
})();
"""

# The loading regex source is passed in as an argument (never inlined) so Python
# escapes cannot break the JS regex literal.
_PROBE_JS = """
(pattern) => {
  if (!window.__qaMutationObserverInstalled) {
    window.__qaMutationObserverInstalled = true;
    window.__qaLastMutation = performance.now();
    new MutationObserver(() => { window.__qaLastMutation = performance.now(); })
      .observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
  }
  const visible = (el) => {
    if (!el) return false;
    if (el.checkVisibility) return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  };
  let busy = false;
  for (const el of document.querySelectorAll('[aria-busy="true"]')) {
    if (visible(el)) { busy = true; break; }
  }
  const re = new RegExp(pattern, 'i');
  let loading = null;
  const body = document.body;
  if (body) {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const t = node.textContent;
      if (!t || t.length > 300) continue;
      if (re.test(t) && visible(node.parentElement)) { loading = t.trim().slice(0, 120); break; }
    }
  }
  return {
    busy,
    loading,
    since_mutation: performance.now() - (window.__qaLastMutation || 0),
    ready: document.readyState,
    href: location.href,
  };
}
"""


def is_loading_text(text: str) -> bool:
    return any(LOADING_RE.search(line) for line in text.splitlines())


async def probe(page: Any) -> dict[str, Any] | None:
    try:
        return await page.evaluate(_PROBE_JS, LOADING_PATTERN)
    except Exception as exc:  # navigation in progress, context destroyed, closed page
        log.debug("settle probe failed: %s", exc)
        return None


async def settle(
    page: Any,
    *,
    inflight: Callable[[], int] = lambda: 0,
    timeout_ms: int = 10_000,
    quiet_ms: int = 250,
    min_ms: int = 300,
    poll_ms: int = 50,
) -> dict[str, Any]:
    """Wait until the page is settled. Never raises on timeout; returns stats."""
    start = time.monotonic()
    deadline = start + timeout_ms / 1000
    stats: dict[str, Any] = {"saw_loading": False, "saw_busy": False, "timed_out": False, "loading_texts": []}
    last: dict[str, Any] | None = None
    # A client-side URL change (router push after "Apply filters", a redirect) usually fires its data
    # request from an effect a moment later: restart the minimum window whenever the URL changes.
    window_start = start
    href: str | None = None
    while True:
        pending = inflight()
        state = await probe(page)
        if state is not None:
            last = state
            cur = state.get("href")
            if href is not None and cur is not None and cur != href:
                window_start = time.monotonic()
                stats["url_changed"] = True
            href = cur if cur is not None else href
            if state["busy"]:
                stats["saw_busy"] = True
            if state["loading"]:
                stats["saw_loading"] = True
                if state["loading"] not in stats["loading_texts"]:
                    stats["loading_texts"].append(state["loading"])
            if (
                pending == 0
                and not state["busy"]
                and not state["loading"]
                and state["ready"] != "loading"
                and state["since_mutation"] >= quiet_ms
                # An action's follow-up request (e.g. a fetch fired from an effect after a URL change)
                # may not have started yet when the page still looks quiet; always observe a minimum window.
                and (time.monotonic() - window_start) * 1000 >= min_ms
            ):
                break
        if time.monotonic() >= deadline:
            stats["timed_out"] = True
            log.warning("settle timed out after %dms (inflight=%d, last=%s)", timeout_ms, pending, last)
            break
        await asyncio.sleep(poll_ms / 1000)
    stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    return stats
