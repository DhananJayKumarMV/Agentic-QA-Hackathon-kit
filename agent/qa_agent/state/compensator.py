"""Reverse money a scenario moved, so later scenarios start from the same balances.

The transfer request shape is learned from a successful POST the UI itself made (field names
mirrored, accounts swapped), so nothing about the target's API is hardcoded.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..contracts import Compensation, NetworkEvent, ScenarioRun
from ..dsl.params import fmt_money, obj_id, tx_deltas, tx_memo
from .ledger import balance_deltas, posted_tx_ids

if TYPE_CHECKING:
    from .snapshot import StateManager

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SRC_HINTS = ("source", "from", "debit", "origin", "sender")
_DST_HINTS = ("destination", "dest", "to_", "credit", "target", "recipient", "beneficiary", "payee")
_MEMO_HINTS = ("memo", "description", "note", "reference", "narrative", "comment")


def _is_amount_value(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) or (isinstance(v, str) and bool(_NUM_RE.match(v.strip())))


def _name_role(key: str) -> str | None:
    k = key.lower()
    if any(h in k for h in _SRC_HINTS):
        return "src"
    if any(h in k for h in _DST_HINTS) or k in ("to", "to_account", "to_account_id"):
        return "dst"
    return None


def learn_transfer_template(network: list[NetworkEvent], account_ids: set[str]) -> dict[str, Any] | None:
    """Find a successful POST whose body names two of our accounts and an amount."""
    from .snapshot import path_of

    for ev in reversed(network):
        if ev.method.upper() != "POST" or not (200 <= ev.status < 300) or not isinstance(ev.req_body, dict):
            continue
        body = ev.req_body
        acct_keys = [k for k, v in body.items() if v is not None and str(v) in account_ids]
        if len(acct_keys) != 2:
            continue
        rest = [k for k in body if k not in acct_keys]
        amount_keys = [k for k in rest if "amount" in k.lower() and _is_amount_value(body[k])] or \
                      [k for k in rest if _is_amount_value(body[k])]
        if not amount_keys:
            continue
        roles = {k: _name_role(k) for k in acct_keys}
        if roles[acct_keys[0]] == "dst" or roles[acct_keys[1]] == "src":
            src_key, dst_key = acct_keys[1], acct_keys[0]
        elif roles[acct_keys[0]] == "src" or roles[acct_keys[1]] == "dst":
            src_key, dst_key = acct_keys
        else:
            # Fall back on the response: the debited account is the source.
            src_key, dst_key = acct_keys
            d = None
            if isinstance(ev.resp_body, dict):
                for v in [ev.resp_body, *[x for x in ev.resp_body.values() if isinstance(x, dict)]]:
                    d = tx_deltas(v)
                    if d:
                        break
            if d and d.get(str(body[acct_keys[1]]), Decimal(0)) < 0:
                src_key, dst_key = acct_keys[1], acct_keys[0]
        memo_key = next((k for k in rest if any(h in k.lower() for h in _MEMO_HINTS)), None)
        return {
            "method": "POST",
            "path": path_of(ev.url),
            "body": dict(body),
            "src_key": src_key,
            "dst_key": dst_key,
            "amount_key": amount_keys[0],
            "memo_key": memo_key,
        }
    return None


def build_transfer_body(tpl: dict[str, Any], src: str, dst: str, amount: Decimal, memo: str) -> dict[str, Any]:
    body = dict(tpl["body"])
    body[tpl["src_key"]] = src
    body[tpl["dst_key"]] = dst
    orig = tpl["body"][tpl["amount_key"]]
    if isinstance(orig, str):
        body[tpl["amount_key"]] = fmt_money(amount)
    elif isinstance(orig, int) and not isinstance(orig, bool) and amount == amount.to_integral_value():
        body[tpl["amount_key"]] = int(amount)
    else:
        body[tpl["amount_key"]] = float(amount)
    if tpl.get("memo_key"):
        body[tpl["memo_key"]] = memo
    for k in list(body):
        if "idempot" in k.lower() or k.lower() in ("request_id", "client_request_id", "nonce"):
            body[k] = str(uuid.uuid4())
    return body


def _foreign_deltas(state: "StateManager", run: ScenarioRun, after_ids: list[str]) -> dict[str, Decimal]:
    """Balance movement explained by transactions that are not ours (so we don't reverse them)."""
    ctx = state.contexts.get(run.scenario_id)
    tag = ctx.run_tag if ctx else None
    ours = set(posted_tx_ids(run)) | state.own_tx_ids
    by_id = {obj_id(t): t for t in state.transactions}
    before = set(run.state_before.tx_ids) if run.state_before else set()
    out: dict[str, Decimal] = {}
    for tid in after_ids:
        if tid in before or tid in ours:
            continue
        tx = by_id.get(tid) or {}
        if tag and tag in tx_memo(tx):
            continue
        for k, v in (tx_deltas(tx) or {}).items():
            out[k] = out.get(k, Decimal(0)) + v
    return out


def _net(state: "StateManager", run: ScenarioRun, snap) -> dict[str, Decimal]:
    moved = balance_deltas(run.state_before, snap)
    foreign = _foreign_deltas(state, run, snap.tx_ids)
    net = {k: v - foreign.get(k, Decimal(0)) for k, v in moved.items()}
    return {k: v for k, v in net.items() if v != 0}


async def compensate_run(state: "StateManager", run: ScenarioRun) -> list[Compensation]:
    if run.state_before is None:
        ctx = state.contexts.get(run.scenario_id)
        if ctx is not None and ctx.scenario.mutates_state:
            return [Compensation(reason=f"{run.scenario_id}: no baseline snapshot, cannot check money movement", ok=False)]
        return []
    try:
        now = await state.snapshot()
    except Exception as e:  # noqa: BLE001
        return [Compensation(reason=f"{run.scenario_id}: snapshot failed before compensation: {e}", ok=False)]
    net = _net(state, run, now)
    if not net:
        return []

    account_ids = {obj_id(a) for a in state.accounts if obj_id(a)}
    tpl = learn_transfer_template(run.network, account_ids)  # type: ignore[arg-type]
    if tpl is not None:
        state.transfer_template = tpl
    tpl = state.transfer_template
    deltas_s = {k: fmt_money(v) for k, v in net.items()}
    if tpl is None:
        return [Compensation(
            reason=f"{run.scenario_id}: money moved but transfer request shape is unknown",
            deltas=deltas_s, ok=False)]

    ctx = state.contexts.get(run.scenario_id)
    tag = ctx.run_tag if ctx else state.run_tag(run.scenario_id)
    gainers = sorted(((k, v) for k, v in net.items() if v > 0), key=lambda x: -x[1])
    losers = sorted(((k, -v) for k, v in net.items() if v < 0), key=lambda x: -x[1])
    comps: list[Compensation] = []
    li = 0
    losers_rem = [list(x) for x in losers]
    for g, g_amt in gainers:
        rem = g_amt
        while rem > 0 and li < len(losers_rem):
            l_id, l_amt = losers_rem[li]
            amt = min(rem, l_amt)
            body = build_transfer_body(tpl, g, l_id, amt, f"{tag} compensation")
            try:
                ev = await state.api(tpl["method"], tpl["path"], body)
                ok = 200 <= ev.status < 300
                tx_ids = posted_tx_ids(ScenarioRun(scenario_id="_", attempt=0, network=[ev])) if ok else []
                err = None if ok else f"HTTP {ev.status}: {str(ev.resp_body)[:200]}"
            except Exception as e:  # noqa: BLE001
                ok, tx_ids, err = False, [], str(e)
            state.own_tx_ids.update(tx_ids)
            comps.append(Compensation(
                reason=f"{run.scenario_id}: reverse {fmt_money(amt)} from {g} to {l_id}" + (f" ({err})" if err else ""),
                tx_id=tx_ids[0] if tx_ids else None,
                deltas={g: fmt_money(-amt), l_id: fmt_money(amt)},
                ok=ok,
            ))
            rem -= amt
            losers_rem[li][1] = l_amt - amt
            if losers_rem[li][1] == 0:
                li += 1
    paired = {}
    for c in comps:
        for k, v in c.deltas.items():
            paired[k] = paired.get(k, Decimal(0)) + Decimal(v)
    unbalanced = {k: fmt_money(v + paired.get(k, Decimal(0))) for k, v in net.items()
                  if v + paired.get(k, Decimal(0)) != 0}
    if unbalanced:
        comps.append(Compensation(
            reason=f"{run.scenario_id}: net movement does not balance across accounts; cannot reverse with a transfer",
            deltas=unbalanced, ok=False))

    # Verify against a fresh snapshot.
    try:
        verify = await state.snapshot()
        residual = _net(state, run, verify)
    except Exception as e:  # noqa: BLE001
        residual = {"?": Decimal(1)}
        comps.append(Compensation(reason=f"{run.scenario_id}: verification snapshot failed: {e}", ok=False))
    if residual:
        for c in comps:
            if c.ok:
                c.ok = False
                c.reason += f"; residual after compensation: { {k: fmt_money(v) for k, v in residual.items()} }"
    return comps
