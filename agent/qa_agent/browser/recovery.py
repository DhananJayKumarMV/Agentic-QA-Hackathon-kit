"""Generic recovery handlers run by the Executor when a step fails or the page looks wrong.

Each handler fires at most once per call to ``recover`` and returns observations so the
run records what happened (recoveries are evidence, never silent).
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from ..contracts import ElementRef, Target

log = logging.getLogger(__name__)

RETRY_NAME = re.compile(r"^\s*(try again|retry|reload|refresh|reconnect)\b", re.I)
NOT_FOUND = re.compile(r"\b(404|page not found|not found|could not be found|doesn.t exist|does not exist)\b", re.I)
ACCESS_REQUIRED = re.compile(
    r"\b(sign in|log in|login|session (has )?(ended|expired)|enter the demo|access (is )?required|not signed in)\b",
    re.I,
)


def _entry_ref(sitemap: Any) -> ElementRef | None:
    if sitemap is None:
        return None
    entry = (sitemap.auth or {}).get("entry")
    if not entry:
        return None
    data = {k: v for k, v in dict(entry).items() if k in ElementRef.model_fields}
    data.setdefault("key", "auth.entry")
    try:
        return ElementRef(**data)
    except Exception:
        return None


async def _entry_locator(browser: Any) -> Any | None:
    ref = _entry_ref(browser.sitemap)
    if ref is None:
        return None
    page = browser.page
    for _, loc in browser.resolver._strategies(page, ref):
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


async def on_landing_page(browser: Any) -> bool:
    """True if the page shows the site's entry action (landing / access-required page)."""
    sitemap = browser.sitemap
    path = urlparse(browser.page.url).path or "/"
    landing = (sitemap.auth or {}).get("landing_route") if sitemap else None
    entry = await _entry_locator(browser)
    if entry is not None:
        return True
    return bool(landing and path.rstrip("/") == str(landing).rstrip("/") and sitemap and sitemap.auth.get("entry"))


async def reenter(browser: Any) -> bool:
    entry = await _entry_locator(browser)
    if entry is None:
        landing = (browser.sitemap.auth or {}).get("landing_route") or "/"
        await browser.goto(landing)
        entry = await _entry_locator(browser)
        if entry is None:
            return False
    await entry.click()
    await browser.settle()
    return True


async def visible_alert_text(page: Any) -> str:
    try:
        texts = await page.get_by_role("alert").all_inner_texts()
    except Exception:
        return ""
    return " ".join(t.strip() for t in texts if t.strip())


ERROR_STATE = re.compile(
    r"\b(something went wrong|unexpected error|error occurred|failed to load|could not load|couldn.t load|"
    r"unable to load|service unavailable|server error|try again|network error|temporarily unavailable)\b",
    re.I,
)


async def error_state(browser: Any) -> str:
    """Return the visible error text if the page currently shows a generic error state, else ''.

    Used between scenarios so the next one never starts on a broken page left by the previous one.
    """
    page = getattr(browser, "page", None)
    if page is None:
        return ""
    alert = await visible_alert_text(page)
    if alert and ERROR_STATE.search(alert):
        return alert[:200]
    try:
        heading = " ".join(await page.get_by_role("heading").all_inner_texts())
    except Exception:
        heading = ""
    if ERROR_STATE.search(heading):
        return heading.strip()[:200]
    return ""


async def recover(browser: Any, *, expect_logged_in: bool = True) -> list[str]:
    obs: list[str] = []
    page = browser.page
    if page is None:
        return obs

    # 1. Landed on the landing / access-required page while the scenario expects a session.
    access_msg = ""
    if expect_logged_in and browser.sitemap is not None and browser.sitemap.auth.get("entry"):
        try:
            access_msg = " ".join(await page.get_by_role("heading").all_inner_texts()) + " " + await visible_alert_text(page)
        except Exception:
            access_msg = ""
    if expect_logged_in and (await on_landing_page(browser) or ACCESS_REQUIRED.search(access_msg)):
        before = page.url
        ok = await reenter(browser)
        obs.append(f"recovery: session missing at {before}; re-entered via auth.entry -> {'ok' if ok else 'failed'}")

    # 2. Error state offering a retry-like button: click it once.
    alert = await visible_alert_text(page)
    if alert:
        retry = page.get_by_role("button", name=RETRY_NAME)
        try:
            if await retry.count() > 0 and await retry.first.is_visible():
                await retry.first.click()
                await browser.settle()
                obs.append(f"recovery: error state ({alert[:120]!r}); clicked retry once")
        except Exception as exc:
            obs.append(f"recovery: retry click failed: {exc.__class__.__name__}")

    # 3. Not-found page: record, do not act.
    try:
        heading = " ".join(await page.get_by_role("heading", level=1).all_inner_texts())
        title = await page.title()
    except Exception:
        heading, title = "", ""
    if NOT_FOUND.search(heading) or NOT_FOUND.search(title):
        obs.append(f"observation: not-found page at {urlparse(page.url).path} ({heading.strip()[:80]!r})")
    return obs

