"""Data formats shared by all agents. Frozen after Phase 0: change only via the integrator."""
from .cost import CostReport, LlmCall
from .execution import Evidence, NetworkEvent, ScenarioRun, StepResult
from .report import AuditReport
from .requirements import Requirement, RequirementSet
from .scenarios import Oracle, ParamBinding, Scenario, Step, Target, TestPlan
from .sitemap import ApiEndpoint, ElementRef, PageModel, SiteMap
from .state import Compensation, LedgerEntry, Snapshot
from .verdicts import OracleResult, RequirementVerdict, ScenarioVerdict

__all__ = [
    "ApiEndpoint", "AuditReport", "Compensation", "CostReport", "ElementRef", "Evidence",
    "LedgerEntry", "LlmCall", "NetworkEvent", "Oracle", "OracleResult", "PageModel",
    "ParamBinding", "Requirement", "RequirementSet", "RequirementVerdict", "Scenario",
    "ScenarioRun", "ScenarioVerdict", "SiteMap", "Snapshot", "Step", "StepResult", "Target",
    "TestPlan",
]
