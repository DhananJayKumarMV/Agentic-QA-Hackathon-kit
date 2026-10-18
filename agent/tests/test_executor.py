import asyncio
from decimal import Decimal

from qa_agent.agents.executor import run_scenario
from qa_agent.contracts import Scenario
from qa_agent.state.snapshot import StateManager

from .fakes import FakeBrowser


def make_transfer_browser():
    fb = FakeBrowser()
    fb.add_element("transfer.source", options=[("acc-1", "Primary"), ("acc-2", "Reserve")])
    fb.add_element("transfer.destination", options=[("acc-1", "Primary"), ("acc-2", "Reserve")])
    fb.add_element("transfer.amount")
    fb.add_element("transfer.memo")

    async def submit(b):
        ev = await b.ui_request("POST", "/api/transfers", {
            "source_account_id": b.values["transfer.source"],
            "destination_account_id": b.values["transfer.destination"],
            "amount": b.values["transfer.amount"], "memo": b.values.get("transfer.memo")})
        b.alerts = [{"role": "status", "live": "", "classes": "notice notice--success", "title": "Transfer complete",
                     "text": "Transfer complete"}] if ev.status < 300 else \
            [{"role": "alert", "live": "", "classes": "notice notice--error", "title": "", "text": "Rejected"}]

    fb.add_element("transfer.submit", on_click=submit)
    return fb


TRANSFER = {
    "id": "SCN-T-1", "requirement_ids": ["REQ-1"], "polarity": "positive", "title": "transfer",
    "rationale": "r", "preconditions": ["logged_in"], "mutates_state": True, "phase": "positive_mutation",
    "params": [
        {"name": "dst", "source": "account", "query": {"select": "not=src", "field": "id"}},
        {"name": "src", "source": "account", "query": {"select": "type=checking", "field": "id"}},
        {"name": "amt", "source": "derived", "query": {"expr": "unique_amount"}},
    ],
    "steps": [
        {"action": "goto", "value": "/transfer", "note": "open"},
        {"action": "snapshot_state", "note": "before"},
        {"action": "select", "target": {"key": "transfer.source"}, "value": "{{src}}", "note": "src"},
        {"action": "select", "target": {"key": "transfer.destination"}, "value": "Reserve", "note": "dst by label"},
        {"action": "fill", "target": {"key": "transfer.amount"}, "value": "{{amt}}", "note": "amount"},
        {"action": "fill", "target": {"key": "transfer.memo"}, "value": "{{run_tag}} ok", "note": "memo"},
        {"action": "click", "target": {"key": "transfer.submit"}, "note": "submit"},
        {"action": "snapshot_state", "note": "after"},
    ],
    "oracles": [
        {"type": "balance_delta", "args": {"deltas": {"src": "-{{amt}}", "dst": "{{amt}}"}}},
        {"type": "tx_present_in_histories", "args": {"accounts": ["src", "dst"]}},
        {"type": "no_success_ui", "args": {}},
        {"type": "url_matches", "args": {"pattern": "^/transfer"}},
        {"type": "api_status", "args": {"method": "POST", "path_glob": "*/transfers", "status": 201}},
        {"type": "new_tx_count", "args": {"n": 1}},
    ],
    "expected_money_delta": {"src": "-{{amt}}", "dst": "{{amt}}"},
}


async def test_happy_path_transfer_then_compensate(tmp_path):
    fb = make_transfer_browser()
    sm_ = fb.sitemap()
    state = StateManager(fb, sm_, "run-abc")
    run = await run_scenario(Scenario(**TRANSFER), fb, sm_, state, attempt=1, evidence_dir=tmp_path)

    assert run.aborted_reason is None, run.aborted_reason
    assert all(s.ok for s in run.steps) and len(run.steps) == 8
    assert fb.logged_in  # session established through sitemap.auth.entry
    p = run.resolved_params
    assert p["src"] == "acc-1" and p["dst"] == "acc-2" and Decimal(p["amt"]) > 1
    amt = Decimal(p["amt"])
    before, after = run.state_before.balances, run.state_after.balances
    assert Decimal(after["acc-1"]) - Decimal(before["acc-1"]) == -amt

    probes = run.observations["probes"]
    assert set(probes) == {"0", "1", "2", "3", "4", "5"}
    assert probes["0"] == {"deltas": {"acc-1": f"-{p['amt']}", "acc-2": p["amt"]}}
    assert probes["1"]["present"] == {"acc-1": True, "acc-2": True}
    assert probes["2"]["alerts"][0]["title"] == "Transfer complete"
    assert probes["3"]["url"] == "/transfer"
    assert probes["4"]["events"][0]["status"] == 201
    assert len(probes["5"]["new_tx_ids"]) == 1 and probes["5"]["foreign_tx_ids"] == []
    # run.network only holds the UI's own call, not the agent's snapshot GETs
    assert [e.method for e in run.network] == ["POST"]
    assert not run.foreign_activity
    assert run.observations["ledger"][0]["intended"] is True
    assert (tmp_path / "SCN-T-1" / "a1_network.json").exists()

    comps = await state.compensate(run)
    assert comps and all(c.ok for c in comps)
    assert {a["id"]: a["balance"] for a in fb.accounts} == {"acc-1": "100.00", "acc-2": "50.00"}


async def test_abort_on_missing_element(tmp_path):
    fb = make_transfer_browser()
    sm_ = fb.sitemap()
    del fb.elements["transfer.submit"]
    run = await run_scenario(Scenario(**TRANSFER), fb, sm_, StateManager(fb, sm_, "r"), attempt=2,
                             evidence_dir=tmp_path)
    assert run.aborted_reason and "step 6" in run.aborted_reason
    assert run.steps[-1].ok is False and run.steps[-1].evidence[0].kind == "screenshot"
    assert "probes" not in run.observations
    assert run.state_after is not None  # still snapshotted for compensation


async def test_unresolvable_param_aborts(tmp_path):
    fb = make_transfer_browser()
    sm_ = fb.sitemap()
    data = dict(TRANSFER, params=[{"name": "src", "source": "account", "query": {"select": "type=brokerage"}}])
    run = await run_scenario(Scenario(**data), fb, sm_, StateManager(fb, sm_, "r"), attempt=1,
                             evidence_dir=tmp_path)
    assert run.aborted_reason.startswith("unresolvable param src")
    assert run.steps == []

    data = dict(TRANSFER, params=[])
    run = await run_scenario(Scenario(**data), fb, sm_, StateManager(fb, sm_, "r"), attempt=1,
                             evidence_dir=tmp_path)
    assert run.aborted_reason.startswith("unresolvable param")


async def test_logged_out_precondition(tmp_path):
    fb = make_transfer_browser()
    sm_ = fb.sitemap()
    s = Scenario(id="S-OUT", requirement_ids=["R"], polarity="negative", title="t", rationale="r",
                 preconditions=["logged_out"], steps=[{"action": "goto", "value": "/dashboard", "note": "n"}],
                 oracles=[{"type": "url_matches", "args": {"pattern": "^/$"}}])
    run = await run_scenario(s, fb, sm_, StateManager(fb, sm_, "r"), attempt=1, evidence_dir=tmp_path)
    assert run.aborted_reason is None and not fb.logged_in
    assert run.observations["probes"]["0"]["url"] == "/dashboard"


async def test_read_only_probe_shapes(tmp_path):
    fb = make_transfer_browser()
    fb.add_element("history.row", texts=["r1"])
    fb.add_element("dashboard.balance", texts=["$100.00", "$50.00"])

    async def click_slow(b):
        b.elements["transfer.submit"]["disabled"] = True

        async def finish():
            await b.ui_request("POST", "/api/transfers", {"source_account_id": "acc-1",
                                                          "destination_account_id": "acc-2", "amount": "1.00"})
            b.elements["transfer.submit"]["disabled"] = False
        asyncio.get_running_loop().create_task(finish())

    fb.add_element("transfer.submit", on_click=click_slow, text="Send")
    fb.rows = [{"id": "tx-1", "datetime": "2025-09-03T03:30:00Z", "cells": {"Type": "Deposit"}, "text": "x"}]
    fb.alerts = [{"role": "status", "live": "polite", "classes": "", "title": "", "text": "Showing 1 transaction"}]
    fb.main_text = "Welcome **** 0001"
    fb.overflow_at = {375: 420}
    sm_ = fb.sitemap(timezone="America/Los_Angeles")
    oracles = [
        {"type": "rows_satisfy_filters", "args": {"filters": {"account": "{{src}}", "type": "deposit"}}},
        {"type": "rows_equal_api_set", "args": {"filters": {}}},
        {"type": "row_count_equals", "args": {"n": 1}},
        {"type": "empty_state_shown", "args": {}},
        {"type": "ui_matches_api", "args": {}},
        {"type": "masked_identifier", "args": {}},
        {"type": "element_absent", "args": {"target": {"key": "nope.thing"}}},
        {"type": "element_visible", "args": {"target": {"key": "dashboard.balance"}}},
        {"type": "text_matches", "args": {"pattern": "Welcome"}},
        {"type": "money_format", "args": {"target": {"key": "dashboard.balance"}}},
        {"type": "no_horizontal_overflow", "args": {"widths": [375, 1280]}},
        {"type": "button_disabled_during", "args": {"target": {"key": "transfer.submit"},
                                                    "request_glob": "*/api/transfers", "delay_ms": 300}},
        {"type": "llm_judge", "args": {"question": "q", "inputs": ["visible_message", "screenshot"]}},
        {"type": "element_visible", "args": {"target": {"key": "missing.key"}}},
        {"type": "new_tx_count", "args": {"n": 1}},  # judged after the button probe's submission
    ]
    s = Scenario(id="S-RO", requirement_ids=["R"], polarity="positive", title="t", rationale="r",
                 params=[{"name": "src", "source": "account", "query": {"select": "index=0"}}],
                 steps=[{"action": "goto", "value": "/history", "note": "n"},
                        {"action": "set_viewport", "value": "1280x800", "note": "n"},
                        {"action": "wait_for", "target": {"key": "history.row"}, "note": "n"}],
                 oracles=oracles)
    run = await run_scenario(s, fb, sm_, StateManager(fb, sm_, "r"), attempt=1, evidence_dir=tmp_path)
    assert run.aborted_reason is None, run.aborted_reason
    p = run.observations["probes"]
    assert p["0"]["rows"][0]["account_ids"] == ["acc-1"] and p["0"]["filters"]["account"] == "acc-1"
    assert p["0"]["timezone"] == "America/Los_Angeles" and len(p["0"]["all_tx"]) == 2
    assert p["1"]["displayed_ids"] == ["tx-1"]
    assert p["2"]["count"] == 1
    assert p["3"] == {"row_count": 1, "status_texts": ["Showing 1 transaction"], "alert_texts": []}
    assert p["4"]["ui_balances"] == {"acc-1": "$100.00", "acc-2": "$50.00"} and len(p["4"]["api_accounts"]) == 2
    assert p["5"]["full_ids"] == ["acc-1", "acc-2"] and "0001" in p["5"]["visible_text"]
    assert p["6"]["count"] == 0 and p["6"]["visible"] is False
    assert p["7"]["visible"] is True and p["7"]["count"] == 2
    assert p["8"] == {"text": "Welcome **** 0001"}
    assert p["9"] == {"texts": ["$100.00", "$50.00"]}
    assert p["10"]["by_width"]["375"] == {"scroll_width": 420, "client_width": 375}
    assert fb.viewport == (1280, 800)
    assert p["11"]["disabled_while_pending"] is True and p["11"]["matching_requests"] == 1
    assert p["12"]["screenshot"].endswith(".png") and "Showing" in p["12"]["visible_message"]
    assert "error" in p["13"]
    assert len(p["14"]["new_tx_ids"]) == 1 and run.network[-1].method == "POST"


async def test_button_probe_observes_the_scenarios_own_submit(tmp_path):
    """The pending-state check runs during the step that clicks the control, not after it."""
    fb = make_transfer_browser()

    async def submit_then_reset(b):
        b.elements["transfer.submit"]["disabled"] = True
        amount = b.values.get("transfer.amount", "")

        async def finish():
            if amount:  # an emptied form sends nothing (client-side validation)
                await b.ui_request("POST", "/api/transfers", {
                    "source_account_id": b.values["transfer.source"],
                    "destination_account_id": b.values["transfer.destination"], "amount": amount})
            b.values["transfer.amount"] = ""  # form reset after submit
            b.elements["transfer.submit"]["disabled"] = False
        asyncio.get_running_loop().create_task(finish())

    fb.add_element("transfer.submit", on_click=submit_then_reset, text="Send")
    sm_ = fb.sitemap()
    data = dict(TRANSFER, oracles=[
        {"type": "button_disabled_during",
         "args": {"target": {"key": "transfer.submit"}, "request_glob": "**/api/transfers*", "delay_ms": 300}},
        {"type": "new_tx_count", "args": {"n": 1}},
    ])
    run = await run_scenario(Scenario(**data), fb, sm_, StateManager(fb, sm_, "r"), attempt=1, evidence_dir=tmp_path)
    assert run.aborted_reason is None and all(s.ok for s in run.steps)
    p = run.observations["probes"]["0"]
    assert p["observed_during_step"] == 6
    assert p["matching_requests"] == 1 and p["disabled_while_pending"] is True
    assert len(run.state_after.tx_ids) == len(run.state_before.tx_ids) + 1


async def test_row_count_counts_target_or_page_items_and_judge_gets_region_text(tmp_path):
    fb = make_transfer_browser()
    fb.add_element("dashboard.account_card", texts=["A", "B"])
    fb.main_text = "Transactions\nShowing 2 transactions\nDeposit +$5.00\nWithdrawal -$3.00"
    fb.alerts = [{"role": "status", "live": "polite", "classes": "", "title": "", "text": "Showing 2 transactions"},
                 {"role": "alert", "live": "", "classes": "toast", "title": "", "text": "Saved elsewhere"}]
    sm_ = fb.sitemap()
    sm_.pages["/dashboard"].elements["dashboard.account_card"].multiple = True
    s = Scenario(id="S-RC", requirement_ids=["R"], polarity="positive", title="t", rationale="r",
                 steps=[{"action": "goto", "value": "/dashboard", "note": "n"}],
                 oracles=[{"type": "row_count_equals", "args": {"n": 2}},
                          {"type": "row_count_equals", "args": {"n": 2, "target": {"key": "dashboard.account_card"}}},
                          {"type": "llm_judge", "args": {"question": "q", "inputs": ["visible_message"]}}])
    run = await run_scenario(s, fb, sm_, StateManager(fb, sm_, "r"), attempt=1, evidence_dir=tmp_path)
    p = run.observations["probes"]
    assert p["0"]["count"] == 2 and p["0"]["key"] == "dashboard.account_card"
    assert p["1"]["count"] == 2
    msg = p["2"]["visible_message"]
    assert msg.startswith("Saved elsewhere") and "Deposit +$5.00" in msg and msg.count("Showing 2 transactions") == 1


async def test_button_probe_counts_the_clicks_request_when_glob_is_wrong(tmp_path):
    fb = make_transfer_browser()
    sm_ = fb.sitemap()
    data = dict(TRANSFER, oracles=[
        {"type": "button_disabled_during",
         "args": {"target": {"key": "transfer.submit"}, "request_glob": "**/api/nothing-like-this*", "delay_ms": 200}},
    ])
    run = await run_scenario(Scenario(**data), fb, sm_, StateManager(fb, sm_, "r"), attempt=1, evidence_dir=tmp_path)
    p = run.observations["probes"]["0"]
    assert p["matching_requests"] >= 1 and "request_glob matched none" in p["request_source"]
    assert "/api/transfers" in p["request_source"]


async def test_ui_matches_api_reads_balances_from_the_balances_page_when_current_page_has_none(tmp_path):
    fb = make_transfer_browser()
    fb.add_element("dashboard.account_balance", texts=["$100.00", "$50.00"])
    default = fb.js["ui_balances"]
    fb.js["ui_balances"] = lambda arg: default(arg) if fb.path == "/dashboard" else {"balances": {}, "ambiguous": []}
    sm_ = fb.sitemap()
    sm_.pages["/dashboard"].elements["dashboard.account_balance"].multiple = True
    s = Scenario(id="S-UI", requirement_ids=["R"], polarity="positive", title="t", rationale="r",
                 steps=[{"action": "goto", "value": "/history", "note": "n"}],
                 oracles=[{"type": "ui_matches_api", "args": {}}, {"type": "url_matches", "args": {"pattern": "x"}}])
    run = await run_scenario(s, fb, sm_, StateManager(fb, sm_, "r"), attempt=1, evidence_dir=tmp_path)
    p = run.observations["probes"]
    assert p["0"]["page"] == "/dashboard" and p["0"]["ui_balances"] == {"acc-1": "$100.00", "acc-2": "$50.00"}
    assert p["1"]["url"].endswith("/history") and fb.path == "/history"
