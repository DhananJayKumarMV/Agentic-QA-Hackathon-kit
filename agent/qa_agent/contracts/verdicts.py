"""Verdicts produced by the Verifier."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .execution import Evidence
from .scenarios import Oracle

Status = Literal["passed", "failed", "unverified"]


class OracleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oracle: Oracle
    status: Literal["pass", "fail", "error", "skipped"]
    expected: str
    observed: str
    evidence: list[Evidence] = []
    layer: Literal["ui", "api", "both"] = "ui"


class ScenarioVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    status: Status
    confidence: float
    oracle_results: list[OracleResult] = []
    attempts: int = 1
    flaky: bool = False
    judge_notes: str | None = None
    repro_steps: list[str] = []


class RequirementVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    status: Status
    reason: str
    scenario_ids: list[str] = []
    expected: str = ""
    observed: str = ""
    evidence: list[Evidence] = []
    severity: Literal["critical", "major", "minor"] | None = None
