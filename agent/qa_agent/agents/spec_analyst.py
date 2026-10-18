"""Spec Analyst: turns PRD text into atomic, testable, traceable requirements.

One structured LLM call drafts the requirements; deterministic post-processing then
drops any requirement whose `source_quote` is not really in the PRD, repairs IDs and
orders the result, so downstream agents only see requirements grounded in the text.
"""
import hashlib
import logging
import re
import unicodedata

from pydantic import BaseModel, ConfigDict

from ..config import REASONING_MODEL
from ..contracts import Requirement, RequirementSet
from ..llm.client import LLM, load_prompt

log = logging.getLogger(__name__)

PROMPT_VERSION = "spec-v3"
AGENT = "spec_analyst"
EXTRACT_MODEL = REASONING_MODEL
# Measured (see docs/COST.md): output tokens are ~95% requirement JSON, so effort barely moves
# cost; "low" kept PRD coverage. Haiku 4.5 over-split the PRD (~2x requirements, doubling the
# planner's cost) and dropped quotes, so extraction stays on Sonnet.
EXTRACT_EFFORT: str | None = "low"

AREA_ORDER = ["access", "dashboard", "transfers", "history", "profile", "ui", "seed", "scope"]
AREA_CODES = {
    "access": "ACC", "dashboard": "DSH", "transfers": "TRF", "history": "HIS",
    "profile": "PRO", "ui": "GUI", "seed": "SED", "scope": "SCP",
}


class RequirementDraft(BaseModel):
    """What the LLM returns: the requirements only; run metadata is added in code."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[Requirement]


# ---------------------------------------------------------------- quote matching

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ", "…": "...",
})
_LINE_MARKER = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]*|[-*+][ \t]+|\d+[.)][ \t]+)", re.MULTILINE)
_INLINE_MARKUP = re.compile(r"\*\*|__|`|\*")
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Canonical form for quote matching: no markdown markers, plain punctuation, single spaces."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = _LINE_MARKER.sub("", text)
    text = _INLINE_MARKUP.sub("", text)
    return _WS.sub(" ", text).strip()


def _normalize_quote(quote: str) -> str:
    q = normalize_text(quote)
    # Models sometimes wrap the quote in quotation marks or add ellipses at the ends.
    q = q.strip('"\'').strip()
    q = re.sub(r"^(?:\.\.\.)\s*|\s*(?:\.\.\.)$", "", q).strip()
    return q


def find_quote(quote: str, normalized_prd: str) -> str | None:
    """Return the quote as it appears in the normalized PRD, or None if it is not there."""
    q = _normalize_quote(quote)
    if not q:
        return None
    if q in normalized_prd:
        return q
    folded = normalized_prd.casefold()
    idx = folded.find(q.casefold())
    if idx >= 0 and len(folded) == len(normalized_prd):
        return normalized_prd[idx: idx + len(q)]
    # Tolerate a missing or extra final period.
    trimmed = q.rstrip(".").rstrip()
    if trimmed and trimmed != q and trimmed in normalized_prd:
        return trimmed
    return None


# ---------------------------------------------------------------- post-processing

def _fix_ids(reqs: list[Requirement]) -> list[Requirement]:
    """Keep well-formed unique IDs; renumber malformed or colliding ones within their area."""
    used: dict[str, set[int]] = {a: set() for a in AREA_ORDER}
    keep: list[bool] = []
    for r in reqs:
        m = re.fullmatch(rf"REQ-{AREA_CODES[r.area]}-(\d{{3}})", r.id)
        n = int(m.group(1)) if m else None
        if n is not None and n > 0 and n not in used[r.area]:
            used[r.area].add(n)
            keep.append(True)
        else:
            keep.append(False)
    out: list[Requirement] = []
    for r, ok in zip(reqs, keep):
        if not ok:
            n = max(used[r.area], default=0) + 1
            used[r.area].add(n)
            new_id = f"REQ-{AREA_CODES[r.area]}-{n:03d}"
            log.info("spec_analyst: renumbered %s -> %s", r.id, new_id)
            r = r.model_copy(update={"id": new_id})
        out.append(r)
    return out


def postprocess(draft: list[Requirement], prd_text: str) -> list[Requirement]:
    """Ground quotes in the PRD, repair IDs and sort by area order then ID."""
    normalized_prd = normalize_text(prd_text)
    grounded: list[Requirement] = []
    for r in draft:
        quote = find_quote(r.source_quote, normalized_prd)
        if quote is None:
            log.warning("spec_analyst: dropped %s, source_quote not found in PRD: %r", r.id, r.source_quote)
            continue
        grounded.append(r.model_copy(update={
            "source_quote": quote,
            "statement": _WS.sub(" ", r.statement).strip(),
            "verification_hints": [h.strip() for h in r.verification_hints if h.strip()],
        }))
    fixed = _fix_ids(grounded)
    return sorted(fixed, key=lambda r: (AREA_ORDER.index(r.area), r.id))


def prd_sha256(prd_text: str) -> str:
    return hashlib.sha256(prd_text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- entry point

async def extract_requirements(prd_text: str, llm: LLM) -> RequirementSet:
    """Extract atomic, testable requirements from the PRD with one structured LLM call."""
    draft = await llm.complete(
        agent=AGENT,
        purpose="extract",
        model=EXTRACT_MODEL,
        system=load_prompt("spec_system.md"),
        user=prd_text,
        output_model=RequirementDraft,
        effort=EXTRACT_EFFORT,
        cache_parts=(PROMPT_VERSION,),
    )
    assert isinstance(draft, RequirementDraft)
    reqs = postprocess(draft.requirements, prd_text)
    log.info("spec_analyst: %d of %d drafted requirements kept", len(reqs), len(draft.requirements))
    return RequirementSet(prd_sha256=prd_sha256(prd_text), prompt_version=PROMPT_VERSION, requirements=reqs)
