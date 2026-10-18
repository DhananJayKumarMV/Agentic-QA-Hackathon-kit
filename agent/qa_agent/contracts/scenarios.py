"""Test plan produced by the Test Planner and run by the Executor.

The planner only uses semantic element keys, runtime parameter bindings and typed
oracles. It never writes raw selectors or hardcoded data values, which keeps the
agent generic: expected values come from the live site, not from known bugs.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Action = Literal[
    "goto", "click", "fill", "select", "submit", "reload", "back", "logout",
    "enter_demo", "set_viewport", "wait_for", "snapshot_state", "api_get", "api_post",
]

OracleType = Literal[
    "url_matches", "element_visible", "element_absent", "text_matches", "text_not_matches",
    "money_format", "masked_identifier", "balance_delta", "no_state_change", "new_tx_count",
    "tx_present_in_histories", "rows_satisfy_filters", "rows_equal_api_set", "row_count_equals",
    "empty_state_shown", "ui_matches_api", "api_status", "api_error_code_present",
    "no_success_ui", "button_disabled_during", "no_horizontal_overflow", "llm_judge",
]

Phase = Literal["read_only", "negative_mutation", "positive_mutation", "session_destructive"]


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str  # SiteMap element key
    nth: int | None = None
    text_contains: str | None = None


class ParamBinding(BaseModel):
    """A value resolved at runtime from the live state snapshot."""

    model_config = ConfigDict(extra="forbid")

    name: str
    source: Literal["account", "transaction", "balance", "date", "literal", "derived"]
    # Examples:
    #   {"account": "type=checking", "field": "id"}
    #   {"transaction": "any", "field": "local_date", "pick": "earliest"}
    #   {"derived": "balance(source) + 0.01"}
    query: dict[str, Any] = {}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    target: Target | None = None
    value: str | None = None  # may contain {{param}} placeholders
    note: str  # human-readable; used verbatim in repro steps


class Oracle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: OracleType
    args: dict[str, Any] = {}
    severity: Literal["critical", "major", "minor"] = "major"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    requirement_ids: list[str]
    polarity: Literal["positive", "negative", "edge"]
    title: str
    rationale: str
    preconditions: list[Literal["logged_in", "logged_out", "fresh_context"]] = ["logged_in"]
    params: list[ParamBinding] = []
    steps: list[Step]
    oracles: list[Oracle]
    mutates_state: bool = False
    # account param name -> signed decimal string the PRD says should move
    expected_money_delta: dict[str, str] = {}
    phase: Phase = "read_only"
    max_attempts: int = 2


class TestPlan(BaseModel):
    __test__ = False  # not a pytest test class
    model_config = ConfigDict(extra="forbid")

    requirement_set_sha: str
    sitemap_sha: str
    scenarios: list[Scenario]
    coverage: dict[str, list[str]] = {}  # requirement id -> scenario ids
