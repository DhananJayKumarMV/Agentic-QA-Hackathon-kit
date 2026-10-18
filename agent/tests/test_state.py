from datetime import datetime, timezone
from decimal import Decimal

from qa_agent.contracts import NetworkEvent, Scenario, ScenarioRun, Snapshot, Step
from qa_agent.evidence.store import redact, redact_event
from qa_agent.state.compensator import build_transfer_body, learn_transfer_template
from qa_agent.state.ledger import balance_deltas
from qa_agent.state.snapshot import StateManager

from .fakes import FakeBrowser


def scen(sid="S1", delta=None, mutates=True):
    return Scenario(id=sid, requirement_ids=["R"], polarity="positive", title="t", rationale="r",
                    steps=[Step(action="snapshot_state", note="n")], oracles=[], mutates_state=mutates,
                    expected_money_delta=delta or {})


async def setup():
    fb = FakeBrowser()
    fb.logged_in = True
    sm = StateManager(fb, fb.sitemap(), "run-123")
    return fb, sm


async def test_snapshot_reads_api_and_caches_lists():
    fb, sm = await setup()
    snap = await sm.snapshot()
    assert snap.balances == {"acc-1": "100.00", "acc-2": "50.00"}
    assert snap.tx_ids == ["tx-1", "tx-2"]
    assert len(sm.accounts) == 2 and len(sm.transactions) == 2
    assert all(("GET", e.url, e.t_ms) in sm.own_event_keys for e in fb.log)


def test_balance_deltas_decimal():
    t = datetime.now(timezone.utc)
    a = Snapshot(taken_at=t, balances={"x": "0.10", "y": "0.20"}, tx_ids=[])
    b = Snapshot(taken_at=t, balances={"x": "0.30", "y": "0.20"}, tx_ids=[])
    assert balance_deltas(a, b) == {"x": Decimal("0.20")}


async def test_ledger_compensation_and_foreign_activity():
    fb, sm = await setup()
    s = scen(delta={"src": "-{{amt}}", "dst": "{{amt}}"})
    ctx = sm.begin(s, {"src": "acc-1", "dst": "acc-2", "amt": "2.50"})
    run = ScenarioRun(scenario_id=s.id, attempt=1)
    run.state_before = await sm.snapshot()
    fb.clear_network_log()
    fb.values["transfer.amount"] = "2.50"
    await fb.ui_transfer("acc-1", "acc-2", memo=f"{ctx.run_tag} test")
    # someone else's activity appears at the same time
    fb.transactions.insert(0, {"id": "tx-foreign", "type": "deposit", "status": "completed", "amount": "1.00",
                               "occurred_at": "2026-01-01T00:00:00Z", "account_deltas": []})
    run.network = fb.network_log()
    run.state_after = await sm.snapshot()

    assert sm.foreign_tx_ids(run) == ["tx-foreign"]
    entries = sm.record_ledger(run)
    assert len(entries) == 1 and entries[0].intended
    assert entries[0].deltas == {"acc-1": "-2.50", "acc-2": "2.50"} and entries[0].memo_tag == ctx.run_tag

    comps = await sm.compensate(run)
    assert len(comps) == 1 and comps[0].ok, comps
    assert comps[0].deltas == {"acc-2": "-2.50", "acc-1": "2.50"}
    bal = {a["id"]: Decimal(a["balance"]) for a in fb.accounts}
    assert bal == {"acc-1": Decimal("100.00"), "acc-2": Decimal("50.00")}
    comp_post = [e for e in fb.log if e.method == "POST"][-1]
    assert comp_post.req_body["source_account_id"] == "acc-2"
    assert comp_post.req_body["amount"] == "2.50"
    assert ctx.run_tag in comp_post.req_body["memo"] and "compensation" in comp_post.req_body["memo"]
    assert await sm.compensate(run) == []  # idempotent


async def test_unintended_money_and_unknown_shape():
    fb, sm = await setup()
    s = scen(sid="NEG", delta={})
    sm.begin(s, {})
    run = ScenarioRun(scenario_id=s.id, attempt=1)
    run.state_before = await sm.snapshot()
    fb.accounts[0]["balance"] = "90.00"  # money vanished without a transaction (and no POST seen)
    fb.accounts[1]["balance"] = "60.00"
    run.state_after = await sm.snapshot()
    entries = sm.record_ledger(run)
    assert entries[0].tx_id is None and not entries[0].intended
    comps = await sm.compensate(run)
    assert len(comps) == 1 and not comps[0].ok and "unknown" in comps[0].reason


async def test_unbalanced_delta_reported():
    fb, sm = await setup()
    fb.break_balance_on_transfer = True
    s = scen(sid="BUG", delta={"src": "-1.00", "dst": "1.00"})
    sm.begin(s, {"src": "acc-1", "dst": "acc-2"})
    run = ScenarioRun(scenario_id=s.id, attempt=1)
    run.state_before = await sm.snapshot()
    fb.clear_network_log()
    fb.values["transfer.amount"] = "1.00"
    await fb.ui_transfer("acc-1", "acc-2")
    run.network = fb.network_log()
    run.state_after = await sm.snapshot()
    comps = await sm.compensate(run)
    assert any(not c.ok for c in comps)


def test_template_learning_by_name_and_body_build():
    ev = NetworkEvent(method="POST", url="http://api/x/move", status=200, t_ms=1,
                      req_body={"to": "B", "from": "A", "value": 5, "note": "hi", "idempotency_key": "k"},
                      resp_body={"id": "t9"})
    tpl = learn_transfer_template([ev], {"A", "B"})
    assert tpl and tpl["src_key"] == "from" and tpl["dst_key"] == "to" and tpl["path"] == "/x/move"
    body = build_transfer_body(tpl, "B", "A", Decimal("5"), "tag compensation")
    assert body["from"] == "B" and body["to"] == "A" and body["value"] == 5 and body["note"] == "tag compensation"
    assert body["idempotency_key"] != "k"


def test_redaction():
    assert redact({"a": {"Set-Cookie": "s=1", "x": "Bearer abc.def"}}) == {
        "a": {"Set-Cookie": "[REDACTED]", "x": "Bearer [REDACTED]"}}
    ev = NetworkEvent(method="GET", url="http://h/p?token=abc&q=1", status=200, t_ms=0)
    assert "abc" not in redact_event(ev)["url"]
