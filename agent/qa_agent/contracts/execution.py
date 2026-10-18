"""What the Executor observed while running one scenario."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .state import Snapshot


class NetworkEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    url: str
    status: int
    req_body: Any = None
    resp_body: Any = None
    t_ms: int


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["screenshot", "trace", "dom", "network", "aria", "console"]
    path: str
    caption: str


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_idx: int
    ok: bool
    locator_used: str | None = None
    healed: bool = False
    duration_ms: int
    error: str | None = None
    evidence: list[Evidence] = []


class ScenarioRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    attempt: int
    resolved_params: dict[str, str] = {}
    steps: list[StepResult] = []
    observations: dict[str, Any] = {}  # extracted UI values: balances, rows, messages, url
    network: list[NetworkEvent] = []
    state_before: Snapshot | None = None
    state_after: Snapshot | None = None
    foreign_activity: bool = False  # transactions appeared that this run did not create
    aborted_reason: str | None = None
