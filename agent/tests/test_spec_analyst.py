import hashlib
from pathlib import Path
from typing import Any

import pytest

from qa_agent.agents.spec_analyst import (
    PROMPT_VERSION, RequirementDraft, extract_requirements, find_quote, normalize_text,
)
from qa_agent.config import REASONING_MODEL
from qa_agent.contracts import RequirementSet
from qa_agent.llm.client import FakeLLM

REAL_PRD = Path(__file__).resolve().parents[2] / "test-website" / "PRD.md"

PRD = """# Shop PRD

## Cart

- The user can add an item to the **cart** from the product page.
- The cart total equals the sum of line items.
  Quantities must be whole numbers.

## Out of scope

- Gift cards.
"""


def _req(id: str, area: str, quote: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id, "area": area, "statement": f"Statement for {id}.", "source_quote": quote,
        "kind": "functional", "priority": "P1", "testability": "automatable",
        "verification_hints": ["check it"], "mutates_state": False,
    }
    return base | kw


class RecordingLLM(FakeLLM):
    """FakeLLM that also records effort, cache_parts and output_model."""

    async def complete(self, **kw: Any) -> Any:
        out = await super().complete(**{k: v for k, v in kw.items() if k not in {"effort", "cache_parts"}})
        self.calls[-1].update(effort=kw.get("effort"), cache_parts=kw.get("cache_parts"),
                              output_model=kw.get("output_model"))
        return out


def _llm(reqs: list[dict[str, Any]]) -> RecordingLLM:
    return RecordingLLM({("spec_analyst", "extract"): {"requirements": reqs}})


async def test_valid_output_passes_through():
    reqs = [
        _req("REQ-TRF-001", "transfers", "The cart total equals the sum of line items."),
        _req("REQ-SCP-001", "scope", "Gift cards.", kind="negative_scope"),
        _req("REQ-DSH-001", "dashboard", "The user can add an item to the cart from the product page."),
    ]
    out = await extract_requirements(PRD, _llm(reqs))
    assert isinstance(out, RequirementSet)
    assert out.prompt_version == PROMPT_VERSION
    # Sorted by area order (dashboard < transfers < scope).
    assert [r.id for r in out.requirements] == ["REQ-DSH-001", "REQ-TRF-001", "REQ-SCP-001"]
    assert out.requirements[0].verification_hints == ["check it"]


async def test_markdown_and_line_breaks_tolerated():
    reqs = [
        _req("REQ-DSH-001", "dashboard", "add an item to the **cart** from"),
        _req("REQ-DSH-002", "dashboard", "- The cart total equals the sum of line items. Quantities must be whole numbers."),
        _req("REQ-DSH-003", "dashboard", "“Quantities must be whole numbers”"),
    ]
    out = await extract_requirements(PRD, _llm(reqs))
    assert len(out.requirements) == 3
    assert out.requirements[0].source_quote == "add an item to the cart from"


async def test_hallucinated_quote_is_dropped():
    reqs = [
        _req("REQ-DSH-001", "dashboard", "The cart total equals the sum of line items."),
        _req("REQ-DSH-002", "dashboard", "The cart total includes tax and shipping."),
        _req("REQ-DSH-003", "dashboard", ""),
    ]
    out = await extract_requirements(PRD, _llm(reqs))
    assert [r.id for r in out.requirements] == ["REQ-DSH-001"]


async def test_duplicate_and_malformed_ids_are_renumbered():
    q = "Quantities must be whole numbers."
    reqs = [
        _req("REQ-DSH-001", "dashboard", q),
        _req("REQ-DSH-001", "dashboard", q),
        _req("REQ-TRF-002", "dashboard", q),  # wrong area prefix
        _req("R1", "history", q),
    ]
    out = await extract_requirements(PRD, _llm(reqs))
    ids = [r.id for r in out.requirements]
    assert len(ids) == len(set(ids)) == 4
    assert ids == ["REQ-DSH-001", "REQ-DSH-002", "REQ-DSH-003", "REQ-HIS-001"]


async def test_sha_and_call_parameters():
    llm = _llm([])
    out = await extract_requirements(PRD, llm)
    assert out.prd_sha256 == hashlib.sha256(PRD.encode("utf-8")).hexdigest()
    assert out.requirements == []
    (call,) = llm.calls
    assert call["agent"] == "spec_analyst" and call["purpose"] == "extract"
    assert call["model"] == REASONING_MODEL
    assert call["user"] == PRD
    assert call["effort"] == "low"
    assert call["cache_parts"] == (PROMPT_VERSION,)
    assert call["output_model"] is RequirementDraft
    assert "source_quote" in call["system"]


def test_find_quote_rejects_paraphrase():
    norm = normalize_text(PRD)
    assert find_quote("The cart total is the sum of the line items.", norm) is None
    assert find_quote("the cart total equals the sum of line items", norm) is not None


@pytest.mark.skipif(not REAL_PRD.exists(), reason="target PRD not present")
async def test_quotes_match_real_prd():
    prd = REAL_PRD.read_text()
    reqs = [
        _req("REQ-ACC-001", "access",
             "The landing page provides a clearly labeled Enter Demo action."),
        _req("REQ-TRF-001", "transfers",
             "Same-account transfers must be rejected with a clear explanation and must not alter "
             "balances or create a transaction.", kind="validation", mutates_state=True),
        _req("REQ-HIS-001", "history",
             "The start and end dates are inclusive."),
        _req("REQ-GUI-001", "ui",
             "The target works in a current desktop browser at a 1280 × 800 viewport",
             testability="partially"),
        _req("REQ-SCP-001", "scope", "Signup, password reset, multi-factor authentication",
             kind="negative_scope"),
        _req("REQ-SCP-002", "scope", "The application offers a referral bonus program.",
             kind="negative_scope"),
    ]
    out = await extract_requirements(prd, _llm(reqs))
    assert [r.id for r in out.requirements] == [
        "REQ-ACC-001", "REQ-TRF-001", "REQ-HIS-001", "REQ-GUI-001", "REQ-SCP-001",
    ]
