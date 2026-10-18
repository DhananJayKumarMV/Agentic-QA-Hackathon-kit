"""Requirements extracted from the PRD by the Spec Analyst."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

Area = Literal["access", "dashboard", "transfers", "history", "profile", "ui", "seed", "scope"]
Kind = Literal["functional", "validation", "security", "data_integrity", "ux", "negative_scope"]


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str  # e.g. "REQ-TRF-004" (area prefix + ordinal)
    area: Area
    statement: str  # normalized, atomic, testable
    source_quote: str  # verbatim PRD text; used for traceability and eval matching
    kind: Kind
    priority: Literal["P0", "P1", "P2"]
    testability: Literal["automatable", "partially", "manual_only"]
    verification_hints: list[str] = []
    mutates_state: bool = False


class RequirementSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prd_sha256: str
    prompt_version: str
    requirements: list[Requirement]
