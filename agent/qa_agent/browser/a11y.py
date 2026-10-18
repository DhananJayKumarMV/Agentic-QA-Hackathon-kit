"""Compact accessibility snapshots for LLM prompts and evidence."""
from __future__ import annotations

import re
from typing import Any

# Lines that carry no meaning for locating or judging UI elements.
_NOISE = re.compile(
    r"^\s*-\s*(img|image|separator|presentation|none|generic)\s*(\"\"|)?\s*:?\s*$"
    r"|^\s*-\s*/url:\s*\"?#"
)
_ROW = re.compile(r"^(\s*)-\s*'?row\b")


def compact(snapshot: str, max_chars: int = 12_000, max_rows: int = 3) -> str:
    """Drop decorative noise, keep at most `max_rows` rows per table body, cap length."""
    out: list[str] = []
    lines = snapshot.splitlines()
    i = 0
    row_runs: dict[str, int] = {}  # indent -> rows kept in the current run
    while i < len(lines):
        line = lines[i]
        if not line.strip() or _NOISE.match(line):
            i += 1
            continue
        m = _ROW.match(line)
        if m:
            indent = m.group(1)
            kept = row_runs.get(indent, 0)
            if kept >= max_rows:
                # Skip this row and its children; count how many rows we drop in this run.
                skipped = 0
                while i < len(lines):
                    l2 = lines[i]
                    m2 = _ROW.match(l2)
                    if m2 and m2.group(1) == indent:
                        skipped += 1
                        i += 1
                        continue
                    if len(l2) - len(l2.lstrip()) > len(indent):
                        i += 1
                        continue
                    break
                out.append(f"{indent}- ... ({skipped} more rows)")
                row_runs[indent] = 0
                continue
            row_runs[indent] = kept + 1
        else:
            # Leaving a row run once we see a non-row sibling or shallower line.
            ind = len(line) - len(line.lstrip())
            for k in list(row_runs):
                if ind <= len(k):
                    row_runs.pop(k)
        out.append(line.rstrip())
        i += 1
    text = "\n".join(out)
    if len(text) > max_chars:
        cut = text.rfind("\n", 0, max_chars - 20)
        text = text[: cut if cut > 0 else max_chars - 20] + "\n- ... (truncated)"
    return text


async def snapshot(page: Any, max_chars: int = 12_000) -> str:
    try:
        raw = await page.locator("body").aria_snapshot(timeout=5_000)
    except Exception as exc:  # page navigating or closed
        return f"(aria snapshot unavailable: {exc.__class__.__name__})"
    return compact(raw, max_chars=max_chars)
