"""Mutation ledger: which transactions a scenario created and whether the PRD wanted them."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..contracts import LedgerEntry, ScenarioRun, Snapshot
from ..dsl.params import fmt_money, obj_id, substitute, to_decimal, tx_deltas, tx_memo

if TYPE_CHECKING:
    from .snapshot import ScenarioContext


def balance_deltas(before: Snapshot | None, after: Snapshot | None) -> dict[str, Decimal]:
    """Signed per-account change after-before (accounts missing on either side are skipped)."""
    if before is None or after is None:
        return {}
    out: dict[str, Decimal] = {}
    for aid, b in before.balances.items():
        if aid in after.balances:
            d = Decimal(after.balances[aid]) - Decimal(b)
            if d != 0:
                out[aid] = d
    return out


def new_tx_ids(before: Snapshot | None, after: Snapshot | None) -> list[str]:
    if before is None or after is None:
        return []
    seen = set(before.tx_ids)
    return [t for t in after.tx_ids if t not in seen]


def _ids_in(body: Any, depth: int = 0) -> list[str]:
    """Transaction-ish ids in a POST response body: top-level id, or id of a nested object."""
    if not isinstance(body, dict) or depth > 2:
        return []
    out: list[str] = []
    if obj_id(body):
        out.append(obj_id(body))  # type: ignore[arg-type]
    for k, v in body.items():
        if isinstance(v, dict) and ("transaction" in k.lower() or "transfer" in k.lower() or k in ("data", "result")):
            out.extend(_ids_in(v, depth + 1))
    return out


def posted_tx_ids(run: ScenarioRun) -> list[str]:
    """Ids returned by the scenario's own successful mutating requests."""
    ids: list[str] = []
    for ev in run.network:
        if ev.method.upper() in ("POST", "PUT", "PATCH") and 200 <= ev.status < 300:
            ids.extend(i for i in _ids_in(ev.resp_body) if i not in ids)
    return ids


def foreign_tx_ids(run: ScenarioRun, run_tag: str | None, transactions: list[dict], own: set[str]) -> list[str]:
    by_id = {obj_id(t): t for t in transactions}
    ours = set(posted_tx_ids(run)) | own
    foreign = []
    for tid in new_tx_ids(run.state_before, run.state_after):
        if tid in ours:
            continue
        tx = by_id.get(tid)
        if tx is not None and run_tag and run_tag in tx_memo(tx):
            continue
        foreign.append(tid)
    return foreign


def _expected_nonzero(ctx: "ScenarioContext | None") -> bool:
    if ctx is None:
        return False
    for v in ctx.scenario.expected_money_delta.values():
        d = to_decimal(substitute(str(v), ctx.params))
        if d is None or d != 0:
            return True
    return False


def ledger_entries(run: ScenarioRun, ctx: "ScenarioContext | None", transactions: list[dict]) -> list[LedgerEntry]:
    """One entry per new transaction attributable to this scenario; plus one for untracked balance drift."""
    tag = ctx.run_tag if ctx else ""
    intended = _expected_nonzero(ctx)
    by_id = {obj_id(t): t for t in transactions}
    new = new_tx_ids(run.state_before, run.state_after)
    posted = set(posted_tx_ids(run))
    bal = balance_deltas(run.state_before, run.state_after)
    entries: list[LedgerEntry] = []
    explained: dict[str, Decimal] = {}
    attributable = [t for t in new if t in posted or (tag and tag in tx_memo(by_id.get(t, {})))]
    for tid in attributable:
        tx = by_id.get(tid, {})
        d = tx_deltas(tx)
        if d is None and len(attributable) == 1:
            d = dict(bal)
        d = d or {}
        for k, v in d.items():
            explained[k] = explained.get(k, Decimal(0)) + v
        entries.append(LedgerEntry(
            scenario_id=run.scenario_id, tx_id=tid,
            deltas={k: fmt_money(v) for k, v in d.items()},
            intended=intended, memo_tag=tag,
        ))
    # Money that moved without an attributable transaction (or beyond what the txs explain).
    foreign = set(new) - set(attributable)
    if not foreign:
        drift = {k: v - explained.get(k, Decimal(0)) for k, v in bal.items()}
        drift = {k: v for k, v in drift.items() if v != 0}
        if drift:
            entries.append(LedgerEntry(
                scenario_id=run.scenario_id, tx_id=None,
                deltas={k: fmt_money(v) for k, v in drift.items()},
                intended=False, memo_tag=tag,
            ))
    return entries
