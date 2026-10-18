"""Element resolution: test_id -> role+name -> label -> css -> LLM heal.

Unique matches are preferred; an ElementRef with ``multiple=True`` accepts any count >= 1.
Healed locators are written back onto the in-memory SiteMap ElementRef (``healed_from``)
so later steps reuse them without another LLM call.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

from ..config import FAST_MODEL
from ..contracts import ElementRef, SiteMap, Target
from . import a11y

log = logging.getLogger(__name__)

HEAL_PROMPT_VERSION = "heal-v1"


class HealResult(BaseModel):
    found: bool = True
    role: str | None = None
    name: str | None = None
    test_id: str | None = None
    label: str | None = None


class ResolveError(LookupError):
    pass


def describe(ref: ElementRef) -> str:
    parts = []
    if ref.test_id:
        parts.append(f"test_id={ref.test_id}")
    if ref.role:
        parts.append(f'role={ref.role}[name="{ref.name}"]' if ref.name else f"role={ref.role}")
    if ref.label:
        parts.append(f'label="{ref.label}"')
    if ref.css:
        parts.append(f"css={ref.css}")
    return " | ".join(parts) or "(empty)"


def route_matches(template: str, path: str) -> bool:
    pattern = "^" + re.sub(r"\\\{[^}]+\\\}", r"[^/]+", re.escape(template.rstrip("/") or "/")) + "/?$"
    return re.match(pattern, path.rstrip("/") or "/") is not None


def find_ref(sitemap: SiteMap | None, key: str, current_path: str | None = None) -> ElementRef | None:
    if sitemap is None:
        return None
    hits: list[tuple[int, ElementRef]] = []
    for route, page in sitemap.pages.items():
        ref = page.elements.get(key)
        if ref is not None:
            score = 0 if current_path and route_matches(route, current_path) else 1
            hits.append((score, ref))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    return hits[0][1]


def _key_words(key: str) -> str:
    thing = key.split(".", 1)[-1]
    return re.sub(r"[_\-]+", " ", thing).strip()


class LocatorResolver:
    def __init__(self, sitemap: SiteMap | None = None, llm: Any = None):
        self.sitemap = sitemap
        self.llm = llm
        self._heal_cache: dict[str, ElementRef] = {}

    def _strategies(self, page: Any, ref: ElementRef) -> list[tuple[str, Any]]:
        s: list[tuple[str, Any]] = []
        if ref.test_id:
            s.append((f"test_id={ref.test_id}", page.get_by_test_id(ref.test_id)))
        if ref.role and ref.name:
            s.append((f'role={ref.role}[name="{ref.name}"]', page.get_by_role(ref.role, name=ref.name, exact=True)))
            s.append((f'role={ref.role}[name~="{ref.name}"]', page.get_by_role(ref.role, name=ref.name)))
        elif ref.role and not (ref.test_id or ref.label or ref.css):
            s.append((f"role={ref.role}", page.get_by_role(ref.role)))
        if ref.label:
            s.append((f'label="{ref.label}"', page.get_by_label(ref.label, exact=True)))
            s.append((f'label~="{ref.label}"', page.get_by_label(ref.label)))
        if ref.css:
            s.append((f"css={ref.css}", page.locator(ref.css)))
        return s

    def _guesses(self, page: Any, key: str) -> list[tuple[str, Any]]:
        """Cheap, generic guesses for keys that are not in the SiteMap."""
        words = _key_words(key)
        if not words:
            return []
        rx = re.compile(r"\b" + r"\W+".join(map(re.escape, words.split())) + r"\b", re.I)
        out: list[tuple[str, Any]] = []
        for role in ("button", "link", "textbox", "combobox", "checkbox", "tab", "menuitem"):
            out.append((f"guess role={role}[name~/{words}/]", page.get_by_role(role, name=rx)))
        out.append((f"guess label~/{words}/", page.get_by_label(rx)))
        out.append((f"guess test_id={words.replace(' ', '-')}", page.get_by_test_id(words.replace(" ", "-"))))
        return out

    @staticmethod
    def _narrow(loc: Any, target: Target) -> Any:
        if target.text_contains:
            loc = loc.filter(has_text=target.text_contains)
        if target.nth is not None:
            loc = loc.nth(target.nth)
        return loc

    async def _try(self, candidates: list[tuple[str, Any]], target: Target, multiple: bool) -> tuple[Any, str] | None:
        fallback: tuple[Any, str] | None = None
        for desc, loc in candidates:
            try:
                narrowed = self._narrow(loc, target)
                n = await narrowed.count()
            except Exception as exc:
                log.debug("locator %s failed: %s", desc, exc)
                continue
            if n == 0:
                continue
            if n == 1 or multiple or target.nth is not None:
                return narrowed, desc
            if fallback is None:
                fallback = (narrowed, desc)
        return fallback

    async def resolve(
        self, page: Any, target: Target, *, wait_ms: int = 3_000, heal: bool = True
    ) -> tuple[Any, str, bool]:
        path = urlparse(page.url).path or "/"
        ref = self._heal_cache.get(target.key) or find_ref(self.sitemap, target.key, path)
        multiple = bool(ref and ref.multiple)
        candidates = self._strategies(page, ref) if ref else self._guesses(page, target.key)

        deadline = asyncio.get_event_loop().time() + wait_ms / 1000
        while True:
            hit = await self._try(candidates, target, multiple)
            if hit:
                return hit[0], hit[1], False
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.2)

        healed = await self._heal(page, target, ref) if heal else None
        if healed is not None:
            hit = await self._try(self._strategies(page, healed), target, multiple)
            if hit:
                return hit[0], f"healed {hit[1]}", True
        raise ResolveError(f"could not resolve element {target.key!r} ({describe(ref) if ref else 'not in sitemap'})")

    async def _heal(self, page: Any, target: Target, ref: ElementRef | None) -> ElementRef | None:
        if self.llm is None:
            return None
        from ..llm.client import load_prompt

        snap = await a11y.snapshot(page, max_chars=6_000)
        old = describe(ref) if ref else "(none)"
        user = (
            f"Key: {target.key}\n"
            f"Meaning: {_key_words(target.key)}\n"
            + (f"Must contain text: {target.text_contains}\n" if target.text_contains else "")
            + f"Broken locator: {old}\n\nARIA snapshot:\n{snap}"
        )
        try:
            out = await self.llm.complete(
                agent="executor_heal", purpose="heal", model=FAST_MODEL,
                system=load_prompt("heal_system.md"), user=user, output_model=HealResult,
                max_tokens=400, cache_parts=(HEAL_PROMPT_VERSION, target.key, snap),
            )
        except Exception as exc:
            log.warning("heal for %s failed: %s", target.key, exc)
            return None
        if not isinstance(out, HealResult) or not out.found or not (out.test_id or out.role or out.label):
            return None
        new = ElementRef(
            key=target.key, role=out.role, name=out.name, test_id=out.test_id, label=out.label,
            multiple=bool(ref and ref.multiple), healed_from=old,
        )
        # Write back onto the live SiteMap ref so the repair is visible downstream.
        if ref is not None:
            ref.role, ref.name, ref.test_id, ref.label, ref.css = new.role, new.name, new.test_id, new.label, None
            ref.healed_from = old
            new = ref
        self._heal_cache[target.key] = new
        return new
