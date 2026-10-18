"""What the Explorer learned about the live site."""
from typing import Any

from pydantic import BaseModel, ConfigDict


class ElementRef(BaseModel):
    """How to find one UI element. Resolved in order: test_id, role+name, label, css."""

    model_config = ConfigDict(extra="forbid")

    key: str  # semantic key, e.g. "transfer.amount", "nav.transactions"
    role: str | None = None
    name: str | None = None
    test_id: str | None = None
    label: str | None = None
    css: str | None = None
    multiple: bool = False
    healed_from: str | None = None  # previous locator description if an LLM repaired it


class PageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    title: str
    purpose: str
    elements: dict[str, ElementRef] = {}
    forms: dict[str, list[str]] = {}  # form key -> element keys
    observed_states: list[str] = []  # "loading", "empty", "error", "success"
    hints: list[str] = []  # visible helper text, e.g. timezone notes on date inputs


class ApiEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    path_template: str
    query_params: list[str] = []
    sample_status: int
    response_shape: dict[str, Any] = {}  # shallow key -> type map, values redacted


class SiteMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    api_base: str | None = None
    pages: dict[str, PageModel] = {}  # route -> page
    api: list[ApiEndpoint] = []
    auth: dict[str, Any] = {}  # entry action, logout action, protected routes
    entities: dict[str, Any] = {}  # live data: accounts, tx count, date span, types, statuses
