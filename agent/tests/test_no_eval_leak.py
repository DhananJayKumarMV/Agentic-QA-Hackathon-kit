"""Leak guard: the agent must derive everything from the PRD and the live site.

Fails if code or prompts under qa_agent/ reference the dev-only answer key or
hardcode values from the target's seed data.
"""
import re
from pathlib import Path

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "qa_agent"

FORBIDDEN = [
    "ground_truth",
    "eval/",
    r"from eval\b",
    r"import eval\b",
    # Seed values of the target app: the agent must read these live, never hardcode them.
    "4369.75",
    "8275.00",
    "Avery Demo",
    "1042",
    "7781",
]


def _sources() -> list[Path]:
    return [p for p in AGENT_SRC.rglob("*") if p.suffix in {".py", ".md", ".j2", ".html", ".txt"}]


@pytest.mark.parametrize("needle", FORBIDDEN)
def test_agent_source_does_not_contain(needle: str) -> None:
    pattern = re.compile(needle if needle.endswith(r"\b") else re.escape(needle))
    offenders = [str(p.relative_to(AGENT_SRC)) for p in _sources() if pattern.search(p.read_text(errors="ignore"))]
    assert not offenders, f"{needle!r} found in {offenders}"
