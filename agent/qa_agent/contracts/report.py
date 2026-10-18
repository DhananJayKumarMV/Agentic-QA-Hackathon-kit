"""The final audit report. report.json is this model; md/html/pdf are rendered from it."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .cost import CostReport
from .state import Compensation, LedgerEntry
from .verdicts import RequirementVerdict, ScenarioVerdict


class AuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    target_url: str
    prd_sha256: str
    started_at: datetime
    finished_at: datetime
    summary: dict[str, Any] = {}  # counts by status, area and polarity
    requirements: list[RequirementVerdict] = []
    scenarios: list[ScenarioVerdict] = []
    defects: list[dict[str, Any]] = []  # failed requirements, de-duplicated, ranked by severity
    state_ledger: list[LedgerEntry] = []
    compensations: list[Compensation] = []
    cost: CostReport
    environment: dict[str, Any] = {}
    limitations: list[str] = []
