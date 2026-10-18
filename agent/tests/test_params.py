from decimal import Decimal

import pytest

from qa_agent.contracts import ParamBinding
from qa_agent.dsl.params import (
    ParamContext,
    UnresolvableParam,
    local_date,
    make_run_tag,
    make_unique_amount,
    placeholders,
    resolve_params,
    substitute,
    to_decimal,
    tx_account_ids,
    tx_deltas,
)

ACCOUNTS = [
    {"id": "a1", "name": "Main", "type": "checking", "masked_number": "**** 11", "balance": "120.50"},
    {"id": "a2", "name": "Spare", "type": "savings", "masked_number": "**** 22", "available_balance": "9.99"},
]
TXS = [
    {"id": "t-late", "type": "deposit", "status": "completed", "amount": "3.00",
     "occurred_at": "2025-09-03T03:30:00Z", "account_deltas": [{"account_id": "a1", "amount_delta": "3.00"}]},
    {"id": "t-early", "type": "withdrawal", "status": "pending", "amount": "1.00",
     "date": "2025-07-01T06:30:00Z", "entries": [{"account_id": "a2", "delta": "-1.00"}]},
]


def ctx(tz=None):
    return ParamContext(accounts=ACCOUNTS, transactions=TXS, timezone=tz,
                        builtins={"run_tag": "qa-x-1", "unique_amount": "1.07"})


def B(name, source, **query):
    return ParamBinding(name=name, source=source, query=query)


def test_account_selectors_and_not():
    p = resolve_params([
        B("dst", "account", select="not=src", field="name"),  # declared before its dependency
        B("src", "account", select="type=checking", field="id"),
        B("second", "account", select="index=1", field="masked_number"),
    ], ctx())
    assert p["src"] == "a1" and p["dst"] == "Spare" and p["second"] == "**** 22"
    assert p["run_tag"] == "qa-x-1"


def test_balance_plus_and_variant_field():
    p = resolve_params([
        B("src", "account", select="type=checking"),
        B("over", "balance", of="src", plus="0.01"),
        B("less", "balance", of="type=savings", plus="-0.99"),
    ], ctx())
    assert p["over"] == "120.51"
    assert p["less"] == "9.00"


def test_derived_balance_expression():
    p = resolve_params([B("src", "account", select="index=0"), B("x", "derived", expr="balance(src) + 0.01"),
                        B("amt", "derived", expr="unique_amount")], ctx())
    assert p["x"] == "120.51" and p["amt"] == "1.07"


def test_transaction_local_date_uses_timezone():
    q = dict(pick="latest", field="local_date")
    assert resolve_params([B("d", "transaction", **q)], ctx())["d"] == "2025-09-03"
    # 03:30Z is still the previous day on the US west coast
    assert resolve_params([B("d", "transaction", **q)], ctx("America/Los_Angeles"))["d"] == "2025-09-02"
    p = resolve_params([B("e", "transaction", pick="earliest", field="id")], ctx())
    assert p["e"] == "t-early"


def test_transaction_where_account_and_date_offset():
    p = resolve_params([
        B("sav", "account", select="type=savings"),
        B("t", "transaction", pick="any", where={"account": "sav"}, field="id"),
        B("d0", "transaction", pick="earliest", field="local_date"),
        B("d1", "date", **{"from": "d0", "offset_days": -1}),
    ], ctx())
    assert p["t"] == "t-early" and p["d0"] == "2025-07-01" and p["d1"] == "2025-06-30"


def test_unresolvable():
    with pytest.raises(UnresolvableParam) as e:
        resolve_params([B("x", "account", select="type=brokerage")], ctx())
    assert e.value.name == "x"
    with pytest.raises(UnresolvableParam):
        resolve_params([B("x", "balance", of="nobody")], ctx())
    with pytest.raises(UnresolvableParam):
        resolve_params([B("x", "transaction", where={"status": "reversed"})], ctx())


def test_substitute_recursive_and_placeholders():
    obj = {"deltas": {"{{src}}": "-{{amt}}"}, "list": ["{{ src }}", 3], "keep": "{{unknown}}"}
    out = substitute(obj, {"src": "a1", "amt": "1.07"})
    assert out == {"deltas": {"a1": "-1.07"}, "list": ["a1", 3], "keep": "{{unknown}}"}
    assert placeholders(obj) == {"src", "amt", "unknown"}
    with pytest.raises(UnresolvableParam):
        substitute("{{nope}}", {}, strict=True)


def test_readers_and_builtins():
    assert to_decimal("$1,234.50") == Decimal("1234.50")
    assert to_decimal("−$12.00") == Decimal("-12.00")
    assert to_decimal("No change: $0.00") == Decimal("0.00")
    assert tx_deltas(TXS[1]) == {"a2": Decimal("-1.00")}
    assert tx_account_ids({"source_account": {"id": "a1"}, "destination_account_id": "a2"}) == ["a1", "a2"]
    assert local_date("2025-01-01", "America/Los_Angeles") == "2025-01-01"
    assert make_run_tag("run1", "S1") != make_run_tag("run1", "S2")
    amounts = {make_unique_amount("r", i) for i in range(50)}
    assert len(amounts) == 50 and all(Decimal(a) > 1 for a in amounts)
