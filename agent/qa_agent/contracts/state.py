"""Snapshots and the mutation ledger used to keep shared demo data consistent."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taken_at: datetime
    balances: dict[str, str]  # account id -> decimal string
    tx_ids: list[str]


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    tx_id: str | None = None
    deltas: dict[str, str] = {}  # account id -> signed decimal string
    intended: bool  # False when money moved although the PRD says it should not
    memo_tag: str


class Compensation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    tx_id: str | None = None
    deltas: dict[str, str] = {}
    ok: bool
