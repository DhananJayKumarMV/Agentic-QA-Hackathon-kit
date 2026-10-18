"""ParamBinding resolution and ``{{name}}`` substitution (CONVENTIONS §3).

Also hosts tolerant readers for API objects (accounts, transactions) so that the
state manager, the probes and the executor all agree on how to read field-name variants.
Money is always ``Decimal``; values handed back to scenarios are strings.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..contracts import ParamBinding, Scenario

TWO_DP = Decimal("0.01")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")

# ---------------------------------------------------------------------------------------------
# Tolerant readers for API objects
# ---------------------------------------------------------------------------------------------

_ID_KEYS = ("id", "uuid", "account_id", "transaction_id", "tx_id")
_BALANCE_KEYS = ("balance", "available_balance", "current_balance", "ledger_balance", "balance_amount")
_TIME_KEYS = ("occurred_at", "datetime", "timestamp", "created_at", "posted_at", "date", "time", "booked_at")
_DELTA_LIST_KEYS = ("account_deltas", "entries", "deltas", "legs", "postings")
_DELTA_AMOUNT_KEYS = ("amount_delta", "delta", "amount", "change")
_MEMO_KEYS = ("memo", "description", "note", "notes", "reference", "narrative")


class UnresolvableParam(Exception):
    def __init__(self, name: str, why: str = "") -> None:
        super().__init__(f"unresolvable param {name}" + (f": {why}" if why else ""))
        self.name = name
        self.why = why


def to_decimal(value: Any) -> Decimal | None:
    """Parse API or UI money into Decimal ("$1,234.50", "−12.00", 3.5). None if not money."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        for k in ("amount", "value"):
            if k in value:
                return to_decimal(value[k])
        return None
    s = str(value).strip().replace("−", "-").replace(",", "")
    m = re.search(r"([+-]?)\s*[^\d+\-.]*?(\d+(?:\.\d+)?|\.\d+)", s)
    if not m:
        return None
    try:
        d = Decimal(m.group(2))
    except InvalidOperation:
        return None
    return -d if m.group(1) == "-" or s.startswith("(") else d


def fmt_money(d: Decimal) -> str:
    return str(d.quantize(TWO_DP, rounding=ROUND_HALF_UP))


def extract_list(body: Any, prefer: Iterable[str] = ()) -> list[dict]:
    """Find the list of objects in an API response (bare list, or {"accounts": [...]}, etc.)."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if not isinstance(body, dict):
        return []
    for k in prefer:
        v = body.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    for k in ("items", "data", "results", "records"):
        v = body.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            inner = extract_list(v, prefer)
            if inner:
                return inner
    for v in body.values():
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            return list(v)
    for v in body.values():
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def obj_id(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    for k in _ID_KEYS:
        if obj.get(k) not in (None, ""):
            return str(obj[k])
    return None


def account_balance(acct: dict) -> Decimal | None:
    for k in _BALANCE_KEYS:
        if k in acct:
            d = to_decimal(acct[k])
            if d is not None:
                return d
    for k, v in acct.items():
        if "balance" in k.lower():
            d = to_decimal(v)
            if d is not None:
                return d
    return None


def tx_time(tx: dict) -> str | None:
    for k in _TIME_KEYS:
        v = tx.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def tx_memo(tx: dict) -> str:
    for k in _MEMO_KEYS:
        v = tx.get(k)
        if isinstance(v, str):
            return v
    return ""


def tx_deltas(tx: dict) -> dict[str, Decimal] | None:
    """Per-account signed deltas if the transaction states them, else None."""
    for k in _DELTA_LIST_KEYS:
        lst = tx.get(k)
        if isinstance(lst, list) and lst:
            out: dict[str, Decimal] = {}
            for e in lst:
                if not isinstance(e, dict):
                    continue
                aid = e.get("account_id") or obj_id(e.get("account")) or e.get("id")
                amt = None
                for ak in _DELTA_AMOUNT_KEYS:
                    if ak in e:
                        amt = to_decimal(e[ak])
                        break
                if aid is not None and amt is not None:
                    out[str(aid)] = out.get(str(aid), Decimal(0)) + amt
            if out:
                return out
    return None


def _ref_id(v: Any) -> str | None:
    if isinstance(v, dict):
        return obj_id(v)
    if isinstance(v, (str, int)) and v != "":
        return str(v)
    return None


def tx_source(tx: dict) -> str | None:
    for k in ("source_account", "source_account_id", "from_account", "from_account_id", "source", "from"):
        if k in tx and tx[k] is not None:
            r = _ref_id(tx[k])
            if r:
                return r
    return None


def tx_destination(tx: dict) -> str | None:
    for k in ("destination_account", "destination_account_id", "to_account", "to_account_id",
              "destination", "to", "target_account", "target_account_id"):
        if k in tx and tx[k] is not None:
            r = _ref_id(tx[k])
            if r:
                return r
    return None


def tx_account_ids(tx: dict) -> list[str]:
    ids: list[str] = []
    d = tx_deltas(tx)
    if d:
        ids.extend(d.keys())
    for r in (tx_source(tx), tx_destination(tx), _ref_id(tx.get("account_id")), _ref_id(tx.get("account"))):
        if r and r not in ids:
            ids.append(r)
    return ids


def parse_dt(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def tz_of(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except Exception:  # unknown tz name
        return ZoneInfo("UTC")


def local_date(iso: str | None, tz_name: str | None) -> str | None:
    if not iso:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso.strip()):
        return iso.strip()
    dt = parse_dt(iso)
    if dt is None:
        return None
    return dt.astimezone(tz_of(tz_name)).date().isoformat()


# ---------------------------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------------------------


def make_run_tag(run_id: str, scenario_id: str) -> str:
    """Short, unique per run+scenario, safe in memos and URLs."""
    h = hashlib.sha1(f"{run_id}|{scenario_id}".encode()).hexdigest()[:6]
    short = re.sub(r"[^A-Za-z0-9]", "", run_id)[-6:] or "run"
    return f"qa-{short}-{h}"


def make_unique_amount(run_id: str, ordinal: int) -> str:
    """Small unique amount 1.01..1.99, distinct for the first 99 scenarios of a run."""
    base = int(hashlib.sha1(run_id.encode()).hexdigest(), 16) % 99
    cents = (base + ordinal) % 99 + 1
    return f"1.{cents:02d}"


# ---------------------------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------------------------


@dataclass
class ParamContext:
    accounts: list[dict] = field(default_factory=list)
    transactions: list[dict] = field(default_factory=list)
    timezone: str | None = None
    builtins: dict[str, str] = field(default_factory=dict)


def _find_account(value: str, accounts: list[dict]) -> dict | None:
    for a in accounts:
        if obj_id(a) == value:
            return a
    for a in accounts:
        for k in ("name", "masked_number", "number", "nickname"):
            if a.get(k) is not None and str(a[k]) == value:
                return a
    return None


def _select_accounts(sel: str, accounts: list[dict], resolved: dict[str, str]) -> list[dict]:
    sel = (sel or "index=0").strip()
    key, _, val = sel.partition("=")
    key, val = key.strip().lower(), val.strip()
    if key == "type":
        return [a for a in accounts if str(a.get("type", a.get("kind", ""))).lower() == val.lower()]
    if key == "index":
        try:
            return [accounts[int(val)]]
        except (ValueError, IndexError):
            return []
    if key == "not":
        excluded: set[str] = set()
        for name in val.split(","):
            name = name.strip()
            if name not in resolved:
                raise KeyError(name)
            acc = _find_account(resolved[name], accounts)
            if acc is not None:
                excluded.add(obj_id(acc) or "")
        return [a for a in accounts if obj_id(a) not in excluded]
    if key in ("id", "name"):
        return [a for a in accounts if str(a.get(key)) == val]
    return []


def _account_field(acct: dict, fld: str) -> str | None:
    fld = fld or "id"
    if fld == "id":
        return obj_id(acct)
    if fld == "balance":
        b = account_balance(acct)
        return fmt_money(b) if b is not None else None
    v = acct.get(fld)
    return None if v is None else str(v)


def _resolve_account_ref(ref: str, ctx: ParamContext, resolved: dict[str, str]) -> dict | None:
    if ref in resolved:
        return _find_account(resolved[ref], ctx.accounts)
    if "=" in ref:
        found = _select_accounts(ref, ctx.accounts, resolved)
        return found[0] if found else None
    return _find_account(ref, ctx.accounts)


def _resolve_one(b: ParamBinding, ctx: ParamContext, resolved: dict[str, str]) -> str:
    q = dict(b.query or {})
    src = b.source

    if src == "literal":
        if "value" not in q:
            raise UnresolvableParam(b.name, "literal without value")
        return substitute(str(q["value"]), {**ctx.builtins, **resolved}, strict=True)

    if src == "derived":
        expr = str(q.get("expr", q.get("derived", ""))).strip()
        if expr in ctx.builtins:
            return ctx.builtins[expr]
        m = re.fullmatch(r"balance\(\s*(\w+)\s*\)\s*(?:([+-])\s*([\d.]+))?", expr)
        if m:
            plus = m.group(3) or "0"
            plus = f"-{plus}" if m.group(2) == "-" else plus
            return _resolve_one(ParamBinding(name=b.name, source="balance", query={"of": m.group(1), "plus": plus}),
                                ctx, resolved)
        raise UnresolvableParam(b.name, f"unknown derived expr {expr!r}")

    if src == "account":
        sel = q.get("select", q.get("account", "index=0"))
        try:
            found = _select_accounts(str(sel), ctx.accounts, resolved)
        except KeyError as e:
            raise _Deferred(str(e)) from None
        if not found:
            raise UnresolvableParam(b.name, f"no account matches {sel!r}")
        val = _account_field(found[0], str(q.get("field", "id")))
        if val is None:
            raise UnresolvableParam(b.name, f"account has no field {q.get('field')!r}")
        return val

    if src == "balance":
        ref = str(q.get("of", q.get("account", "")))
        if not ref:
            raise UnresolvableParam(b.name, "balance without 'of'")
        if ref in _pending_names(ctx) and ref not in resolved:
            raise _Deferred(ref)
        acct = _resolve_account_ref(ref, ctx, resolved)
        if acct is None:
            raise UnresolvableParam(b.name, f"unknown account {ref!r}")
        bal = account_balance(acct)
        if bal is None:
            raise UnresolvableParam(b.name, "account has no balance")
        plus = to_decimal(substitute(str(q.get("plus", "0")), {**ctx.builtins, **resolved})) or Decimal(0)
        return fmt_money(bal + plus)

    if src == "transaction":
        pick = str(q.get("pick", q.get("transaction", "any"))).lower()
        if pick not in ("earliest", "latest", "any"):
            pick = "any"
        where = dict(q.get("where") or {})
        txs = list(ctx.transactions)
        for k in ("type", "status"):
            if where.get(k):
                want = substitute(str(where[k]), resolved).lower()
                txs = [t for t in txs if str(t.get(k, "")).lower() == want]
        if where.get("account"):
            ref = str(where["account"])
            if ref in _pending_names(ctx) and ref not in resolved:
                raise _Deferred(ref)
            acct = _resolve_account_ref(ref, ctx, resolved)
            aid = obj_id(acct) if acct else resolved.get(ref, ref)
            txs = [t for t in txs if aid in tx_account_ids(t)]
        if not txs:
            raise UnresolvableParam(b.name, f"no transaction matches {where}")
        if pick in ("earliest", "latest"):
            dated = [(parse_dt(tx_time(t) or ""), t) for t in txs]
            dated = [(d, t) for d, t in dated if d is not None]
            if not dated:
                raise UnresolvableParam(b.name, "transactions have no timestamps")
            dated.sort(key=lambda x: x[0])
            tx = dated[0][1] if pick == "earliest" else dated[-1][1]
        else:
            tx = txs[0]
        fld = str(q.get("field", "id"))
        if fld == "id":
            v = obj_id(tx)
        elif fld == "local_date":
            v = local_date(tx_time(tx), ctx.timezone)
        elif fld == "amount":
            d = to_decimal(tx.get("amount"))
            v = fmt_money(d) if d is not None else None
        elif fld in ("datetime", "occurred_at", "time"):
            v = tx_time(tx)
        else:
            v = tx.get(fld)
        if v is None:
            raise UnresolvableParam(b.name, f"transaction has no {fld!r}")
        return str(v)

    if src == "date":
        frm = str(q.get("from", "today"))
        if frm in _pending_names(ctx) and frm not in resolved:
            raise _Deferred(frm)
        if frm in resolved:
            base_s = resolved[frm]
        elif frm == "today":
            base_s = datetime.now(tz_of(ctx.timezone)).date().isoformat()
        else:
            base_s = frm
        base_s = local_date(base_s, ctx.timezone) or ""
        try:
            base = date.fromisoformat(base_s)
        except ValueError:
            raise UnresolvableParam(b.name, f"not a date: {base_s!r}") from None
        return (base + timedelta(days=int(q.get("offset_days", 0) or 0))).isoformat()

    raise UnresolvableParam(b.name, f"unknown source {src!r}")


class _Deferred(Exception):
    """Binding depends on a param that is not resolved yet."""


_PENDING_KEY = "__pending_names__"


def _pending_names(ctx: ParamContext) -> set[str]:
    return getattr(ctx, _PENDING_KEY, set())


def resolve_params(bindings: list[ParamBinding], ctx: ParamContext) -> dict[str, str]:
    """Resolve bindings (any order; dependencies on other params are followed).

    Returns builtins + resolved params. Raises UnresolvableParam on the first failure.
    """
    resolved: dict[str, str] = {}
    pending = list(bindings)
    setattr(ctx, _PENDING_KEY, {b.name for b in bindings})
    try:
        while pending:
            progressed = False
            for b in list(pending):
                try:
                    resolved[b.name] = _resolve_one(b, ctx, resolved)
                except _Deferred:
                    continue
                pending.remove(b)
                progressed = True
            if not progressed:
                raise UnresolvableParam(pending[0].name, "circular or missing dependency")
    finally:
        setattr(ctx, _PENDING_KEY, set())
    return {**ctx.builtins, **resolved}


def resolve_scenario_params(scenario: Scenario, ctx: ParamContext) -> dict[str, str]:
    return resolve_params(scenario.params, ctx)


# ---------------------------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------------------------


def placeholders(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, str):
        out.update(PLACEHOLDER_RE.findall(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out |= placeholders(k) | placeholders(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= placeholders(v)
    return out


def substitute(obj: Any, params: dict[str, str], *, strict: bool = False) -> Any:
    """Replace ``{{name}}`` recursively in strings, dict keys/values and lists."""
    if isinstance(obj, str):
        def rep(m: re.Match) -> str:
            name = m.group(1)
            if name in params:
                return str(params[name])
            if strict:
                raise UnresolvableParam(name, "unknown placeholder")
            return m.group(0)
        return PLACEHOLDER_RE.sub(rep, obj)
    if isinstance(obj, dict):
        return {substitute(k, params, strict=strict): substitute(v, params, strict=strict) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, params, strict=strict) for v in obj]
    if isinstance(obj, tuple):
        return tuple(substitute(v, params, strict=strict) for v in obj)
    return obj
