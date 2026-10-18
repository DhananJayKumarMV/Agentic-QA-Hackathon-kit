import json
from decimal import Decimal
from pathlib import Path

import pytest

from qa_agent.contracts import NetworkEvent, Oracle, Scenario, ScenarioRun, SiteMap, Snapshot, Step, StepResult
from qa_agent.dsl.oracles import check_oracle, parse_money, tx_accounts, tx_matches_filters

FIX = Path(__file__).parent / "fixtures" / "oracles"
ALL_TX = json.loads((FIX / "transactions.json").read_text())["transactions"]


def sitemap(tz=None):
    return SiteMap(base_url="http://app.test", entities={
        "timezone": tz,
        "accounts": [{"id": "acc-a", "name": "Checking A"}, {"id": "acc-b", "name": "Savings B"}],
    })


def judge(otype, args=None, probe=None, *, params=None, before=None, after=None, foreign=False,
          network=None, tz=None, steps=None, severity="major", expected_delta=None, title="t"):
    o = Oracle(type=otype, args=args or {}, severity=severity)
    sc = Scenario(id="SCN-X-1", requirement_ids=["REQ-X-001"], polarity="positive", title=title, rationale="r",
                  steps=[Step(action="goto", value="/", note="Open home")], oracles=[o],
                  expected_money_delta=expected_delta or {})
    obs = {"probes": {"0": probe}} if probe is not None else {}
    snap = lambda d: Snapshot(taken_at="2026-01-01T00:00:00Z", balances=d[0], tx_ids=d[1]) if d else None  # noqa: E731
    run = ScenarioRun(scenario_id=sc.id, attempt=1, resolved_params=params or {}, observations=obs,
                      network=network or [], state_before=snap(before), state_after=snap(after),
                      foreign_activity=foreign, steps=steps or [])
    return check_oracle(o, run, sc, sitemap(tz), 0)


# ---------------------------------------------------------------- money parsing

@pytest.mark.parametrize("text,expected", [
    ("$1,234.56", "1234.56"),
    ("-$5.00", "-5.00"),
    ("$-5.00", "-5.00"),
    ("−$5.00", "-5.00"),
    ("−5.00", "-5.00"),
    ("1234.56 USD", "1234.56"),
    ("USD 1,234.56", "1234.56"),
    ("($12.30)", "-12.30"),
    ("Balance: $0.07", "0.07"),
    ("+$3.10", "3.10"),
    ("$1,000,000.00", "1000000.00"),
    ("12", "12"),
    (Decimal("4.5"), "4.5"),
    (7, "7"),
])
def test_parse_money(text, expected):
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize("text", [None, "", "n/a", "abc"])
def test_parse_money_none(text):
    assert parse_money(text) is None


# ---------------------------------------------------------------- probe handling

def test_missing_probe_is_error():
    r = judge("url_matches", {"pattern": "^/dashboard"}, None)
    assert r.status == "error"


def test_probe_error_is_error_not_fail():
    r = judge("element_visible", {"target": {"key": "x.y"}}, {"error": "timeout waiting for locator"})
    assert r.status == "error" and "timeout" in r.observed


def test_unresolved_param_is_error():
    r = judge("row_count_equals", {"n": "{{missing}}"}, {"count": 3})
    assert r.status == "error"


def test_probe_evidence_passthrough():
    ev = {"kind": "screenshot", "path": "a.png", "caption": "after"}
    r = judge("element_visible", {"target": {"key": "a.b"}}, {"count": 1, "visible": True, "evidence": [ev]})
    assert r.status == "pass" and r.evidence[0].path == "a.png"


# ---------------------------------------------------------------- UI oracles

def test_url_matches():
    assert judge("url_matches", {"pattern": r"^/dashboard"}, {"url": "http://x/dashboard?x=1"}).status == "pass"
    r = judge("url_matches", {"pattern": r"^/dashboard"}, {"url": "http://x/login"})
    assert r.status == "fail" and "/login" in r.observed


def test_url_matches_hash_route():
    assert judge("url_matches", {"pattern": r"/dashboard"}, {"url": "http://x/#/dashboard"}).status == "pass"


def test_element_visible_absent():
    assert judge("element_visible", {"target": {"key": "a.b"}}, {"count": 1, "visible": True}).status == "pass"
    assert judge("element_visible", {"target": {"key": "a.b"}}, {"count": 0, "visible": False}).status == "fail"
    assert judge("element_absent", {"target": {"key": "a.b"}}, {"count": 0, "visible": False}).status == "pass"
    assert judge("element_absent", {"target": {"key": "a.b"}}, {"count": 1, "visible": True}).status == "fail"


def test_text_matches_with_param_escaped():
    r = judge("text_matches", {"pattern": "memo {{tag}}"}, {"text": "memo run.1+x done"}, params={"tag": "run.1+x"})
    assert r.status == "pass"
    r = judge("text_matches", {"pattern": "memo {{tag}}"}, {"text": "memo runA1+x"}, params={"tag": "run.1+x"})
    assert r.status == "fail"


def test_text_ignore_case_and_not_matches():
    assert judge("text_matches", {"pattern": "error", "ignore_case": True}, {"text": "ERROR!"}).status == "pass"
    assert judge("text_matches", {"pattern": "error"}, {"text": "ERROR!"}).status == "fail"
    assert judge("text_not_matches", {"pattern": "NaN|undefined"}, {"text": "Total $5.00"}).status == "pass"
    assert judge("text_not_matches", {"pattern": "NaN|undefined"}, {"text": "Total $NaN"}).status == "fail"


def test_money_format():
    tgt = {"target": {"key": "dashboard.balance"}}
    assert judge("money_format", tgt, {"texts": ["$1,234.56", "-$5.00", "−$2.00"]}).status == "pass"
    r = judge("money_format", tgt, {"texts": ["$1,234.56", "$12.5", "$7"]})
    assert r.status == "fail" and "2 of 3" in r.observed
    assert judge("money_format", tgt, {"texts": []}).status == "error"


def test_masked_identifier():
    probe = {"visible_text": "Checking **** 9876", "url": "/a/acc-123456", "full_ids": ["acc-123456", "5555119876"]}
    assert judge("masked_identifier", {}, probe).status == "pass"
    probe["visible_text"] = "Checking 5555 1198 76"
    assert judge("masked_identifier", {}, probe).status == "fail"
    probe["visible_text"] = "Account acc-123456"
    assert judge("masked_identifier", {}, probe).status == "fail"


def test_row_count_and_empty_state():
    assert judge("row_count_equals", {"n": "{{n}}"}, {"count": 2}, params={"n": "2"}).status == "pass"
    assert judge("row_count_equals", {"n": 2}, {"count": 3}).status == "fail"
    ok = {"row_count": 0, "status_texts": ["No transactions match"], "alert_texts": []}
    assert judge("empty_state_shown", {}, ok).status == "pass"
    assert judge("empty_state_shown", {}, {**ok, "status_texts": []}).status == "fail"
    assert judge("empty_state_shown", {}, {**ok, "alert_texts": ["Failed to load"]}).status == "fail"
    assert judge("empty_state_shown", {}, {**ok, "row_count": 2}).status == "fail"


def test_no_success_ui():
    assert judge("no_success_ui", {}, {"alerts": [{"role": "alert", "classes": "error", "title": "",
                                                    "text": "Amount must be greater than zero"}]}).status == "pass"
    assert judge("no_success_ui", {}, {"alerts": [{"role": "status", "classes": ["toast", "toast-success"],
                                                    "title": "", "text": "Done"}]}).status == "fail"
    assert judge("no_success_ui", {}, {"alerts": [{"text": "Transfer completed"}]}).status == "fail"
    assert judge("no_success_ui", {}, {"alerts": [{"text": "Transfer could not be completed"}]}).status == "pass"
    assert judge("no_success_ui", {}, {"alerts": []}).status == "pass"


def test_button_disabled_during():
    a = {"target": {"key": "transfer.submit"}, "request_glob": "/api/*"}
    assert judge("button_disabled_during", a, {"disabled_while_pending": True, "busy_text": "", "matching_requests": 1}).status == "pass"
    assert judge("button_disabled_during", a, {"disabled_while_pending": False, "busy_text": "Sending…", "matching_requests": 1}).status == "pass"
    assert judge("button_disabled_during", a, {"disabled_while_pending": True, "busy_text": "", "matching_requests": 2}).status == "fail"
    assert judge("button_disabled_during", a, {"disabled_while_pending": False, "busy_text": "", "matching_requests": 1}).status == "fail"
    assert judge("button_disabled_during", a, {"disabled_while_pending": False, "busy_text": "", "matching_requests": 0}).status == "error"


def test_no_horizontal_overflow():
    a = {"widths": [375, 1280]}
    ok = {"by_width": {"375": {"scroll_width": 376, "client_width": 375}, "1280": {"scroll_width": 1280, "client_width": 1280}}}
    assert judge("no_horizontal_overflow", a, ok).status == "pass"
    bad = {"by_width": {"375": {"scroll_width": 520, "client_width": 375}, "1280": {"scroll_width": 1280, "client_width": 1280}}}
    r = judge("no_horizontal_overflow", a, bad)
    assert r.status == "fail" and "375px" in r.observed
    assert judge("no_horizontal_overflow", a, {"by_width": {"375": {"scroll_width": 375, "client_width": 375}}}).status == "error"


def test_llm_judge_pending():
    r = judge("llm_judge", {"question": "Does the message explain why?"}, {"visible_message": "x"})
    assert r.status == "skipped" and r.observed == "pending LLM judge"


# ---------------------------------------------------------------- snapshot oracles

B = ({"acc-a": "100.00", "acc-b": "50.00"}, ["t1"])


def test_balance_delta_pass_and_fail():
    args = {"deltas": {"src": "-{{amt}}", "dst": "{{amt}}"}}
    params = {"src": "acc-a", "dst": "acc-b", "amt": "1.07"}
    after = ({"acc-a": "98.93", "acc-b": "51.07"}, ["t1", "t2"])
    r = judge("balance_delta", args, None, params=params, before=B, after=after)
    assert r.status == "pass" and r.layer == "api"
    wrong = ({"acc-a": "97.86", "acc-b": "51.07"}, ["t1", "t2"])
    r = judge("balance_delta", args, None, params=params, before=B, after=wrong)
    assert r.status == "fail"
    assert "Checking A balance changes by -1.07" in r.expected
    assert "changed by -2.14" in r.observed


def test_balance_delta_foreign_activity_is_error():
    args = {"deltas": {"src": "-1.00"}}
    wrong = ({"acc-a": "90.00", "acc-b": "50.00"}, ["t1", "t2", "t3"])
    r = judge("balance_delta", args, None, params={"src": "acc-a"}, before=B, after=wrong, foreign=True)
    assert r.status == "error" and "foreign" in r.observed


def test_balance_delta_falls_back_to_expected_money_delta():
    after = ({"acc-a": "95.00", "acc-b": "50.00"}, ["t1"])
    r = judge("balance_delta", {}, None, params={"src": "acc-a"}, before=B, after=after, expected_delta={"src": "-5"})
    assert r.status == "pass"


def test_balance_delta_missing_snapshot_is_error():
    assert judge("balance_delta", {"deltas": {"src": "-1"}}, None, params={"src": "acc-a"}, before=B).status == "error"


def test_no_state_change():
    assert judge("no_state_change", {}, None, before=B, after=B).status == "pass"
    after = ({"acc-a": "100", "acc-b": "50.0"}, ["t1"])  # numerically equal
    assert judge("no_state_change", {}, None, before=B, after=after).status == "pass"
    moved = ({"acc-a": "99.00", "acc-b": "50.00"}, ["t1", "t9"])
    r = judge("no_state_change", {}, None, before=B, after=moved)
    assert r.status == "fail" and "100.00 -> 99.00" in r.observed and "t9" in r.observed
    assert judge("no_state_change", {}, None, before=B, after=moved, foreign=True).status == "error"


def test_new_tx_count():
    after = ({"acc-a": "1"}, ["t1", "t2"])
    assert judge("new_tx_count", {"n": 1}, None, before=B, after=after).status == "pass"
    assert judge("new_tx_count", {"n": 0}, None, before=B, after=after).status == "fail"
    assert judge("new_tx_count", {"n": 0}, None, before=B, after=after, foreign=True).status == "error"
    # fewer than expected is still a real failure even with foreign activity
    assert judge("new_tx_count", {"n": 2}, None, before=B, after=after, foreign=True).status == "fail"


def test_tx_present_in_histories():
    a = {"accounts": ["src", "dst"]}
    p = {"src": "acc-a", "dst": "acc-b"}
    assert judge("tx_present_in_histories", a, {"tx_id": "t2", "present": {"acc-a": True, "acc-b": True}}, params=p).status == "pass"
    r = judge("tx_present_in_histories", a, {"tx_id": "t2", "present": {"acc-a": True, "acc-b": False}}, params=p)
    assert r.status == "fail" and "Savings B" in r.observed
    assert judge("tx_present_in_histories", a, {"tx_id": None, "present": {}}, params=p).status == "error"


# ---------------------------------------------------------------- filters / data integrity

def test_tx_accounts_variants():
    ids = {t["id"]: tx_accounts(t)[0] for t in ALL_TX}
    assert ids["tx-1"] == {"acc-a"}
    assert ids["tx-2"] == {"acc-a", "acc-b"}
    assert ids["tx-3"] == {"acc-b"}
    assert ids["tx-4"] == {"acc-b"}


def test_filters_type_status_variants():
    from datetime import timezone
    ok, _ = tx_matches_filters(ALL_TX[2], {"type": "Withdrawal", "status": "PENDING"}, timezone.utc)
    assert ok
    ok, why = tx_matches_filters(ALL_TX[2], {"type": "deposit", "status": "all"}, timezone.utc)
    assert not ok and "type" in why[0]


def test_rows_equal_api_set_inclusive_local_day():
    # tx-2 at 06:30Z on Jul 3 is Jul 2 23:30 in Los Angeles -> included on a single-day Jul 2 filter.
    f = {"filters": {"start_date": "2025-07-02", "end_date": "2025-07-02"}}
    probe = {"displayed_ids": ["tx-1", "tx-2"], "all_tx": {"transactions": ALL_TX}}
    assert judge("rows_equal_api_set", f, probe, tz="America/Los_Angeles").status == "pass"
    # In UTC the same UI rows include an extra row.
    r = judge("rows_equal_api_set", f, probe, tz=None)
    assert r.status == "fail" and "unexpected" in r.observed and "tx-2" in r.observed


def test_rows_equal_api_set_missing_row_on_end_boundary():
    # End date is inclusive: tx-4 (Jul 4 16:59 local) must be shown for end_date=2025-07-04.
    f = {"filters": {"start_date": "{{d0}}", "end_date": "{{d1}}"}}
    probe = {"displayed_ids": ["tx-1", "tx-2"], "all_tx": ALL_TX}
    r = judge("rows_equal_api_set", f, probe, params={"d0": "2025-07-02", "d1": "2025-07-04"}, tz="America/Los_Angeles")
    assert r.status == "fail" and "missing 1" in r.observed and "tx-4" in r.observed


def test_rows_equal_api_set_account_filter():
    f = {"filters": {"account": "{{acct}}"}}
    probe = {"displayed_ids": ["tx-2", "tx-3", "tx-4"], "all_tx": ALL_TX}
    assert judge("rows_equal_api_set", f, probe, params={"acct": "acc-b"}).status == "pass"
    probe = {"displayed_ids": ["tx-3", "tx-4", "tx-1"], "all_tx": ALL_TX}
    r = judge("rows_equal_api_set", f, probe, params={"acct": "acc-b"})
    assert r.status == "fail" and "missing 1" in r.observed and "1 unexpected" in r.observed


def test_rows_equal_api_set_bad_probe():
    assert judge("rows_equal_api_set", {"filters": {}}, {"displayed_ids": ["a"]}).status == "error"
    assert judge("rows_equal_api_set", {"filters": {"color": "red"}}, {"displayed_ids": [], "all_tx": ALL_TX}).status == "error"


def test_rows_satisfy_filters():
    f = {"filters": {"type": "deposit"}}
    rows = [{"id": "tx-1", "datetime": "", "cells": {}, "account_ids": []}]
    assert judge("rows_satisfy_filters", f, {"rows": rows, "all_tx": ALL_TX}).status == "pass"
    rows.append({"id": "tx-2", "datetime": "", "cells": {}, "account_ids": []})
    r = judge("rows_satisfy_filters", f, {"rows": rows, "all_tx": ALL_TX})
    assert r.status == "fail" and "tx-2" in r.observed


def test_rows_satisfy_filters_date_boundary_local():
    f = {"filters": {"start_date": "2025-07-03", "end_date": "2025-07-05"}}
    rows = [{"id": "tx-2", "cells": {}}]
    # Jul 2 local in LA -> violates start bound; in UTC it is Jul 3 -> fine.
    assert judge("rows_satisfy_filters", f, {"rows": rows, "all_tx": ALL_TX}, tz="America/Los_Angeles").status == "fail"
    assert judge("rows_satisfy_filters", f, {"rows": rows, "all_tx": ALL_TX}).status == "pass"


def test_rows_satisfy_filters_unknown_row_uses_cells_or_errors():
    f = {"filters": {"type": "deposit"}}
    rows = [{"id": "zzz", "datetime": "2025-07-01T00:00:00Z", "cells": {"Type": "Withdrawal"}}]
    assert judge("rows_satisfy_filters", f, {"rows": rows, "all_tx": ALL_TX}).status == "fail"
    f = {"filters": {"account": "acc-a"}}
    rows = [{"id": "zzz", "cells": {}}]
    assert judge("rows_satisfy_filters", f, {"rows": rows, "all_tx": ALL_TX}).status == "error"


def test_ui_matches_api():
    api = [{"id": "acc-a", "name": "A", "balance": "1234.50"}, {"id": "acc-b", "available_balance": "-5.00"}]
    ok = {"ui_balances": {"acc-a": "$1,234.50", "acc-b": "−$5.00"}, "api_accounts": api}
    assert judge("ui_matches_api", {}, ok).status == "pass"
    bad = {"ui_balances": {"acc-a": "$1,234.05", "acc-b": "-$5.00"}, "api_accounts": api}
    r = judge("ui_matches_api", {}, bad)
    assert r.status == "fail" and "1,234.05" in r.observed
    assert judge("ui_matches_api", {}, {"ui_balances": {}, "api_accounts": api}).status == "error"


# ---------------------------------------------------------------- network oracles

def ev(method, url, status, body=None):
    return NetworkEvent(method=method, url=url, status=status, resp_body=body, t_ms=1)


def test_api_status():
    net = [ev("GET", "http://h/api/accounts", 200), ev("POST", "http://h/api/transfers", 422)]
    a = {"method": "POST", "path_glob": "/api/transfers*", "status_in": [400, 422]}
    assert judge("api_status", a, None, network=net).status == "pass"
    a = {"method": "POST", "path_glob": "/api/transfers*", "status": 201}
    r = judge("api_status", a, None, network=net)
    assert r.status == "fail" and "422" in r.observed


def test_api_status_no_request_is_error():
    net = [ev("POST", "http://h/api/transfers", -1)]
    a = {"method": "POST", "path_glob": "/api/transfers", "status": 400}
    assert judge("api_status", a, None, network=net).status == "error"


def test_api_error_code_present():
    a = {"method": "POST", "path_glob": "/api/transfers"}
    good = [ev("POST", "http://h/api/transfers", 400, {"error": {"code": "invalid_amount", "message": "x"}})]
    assert judge("api_error_code_present", a, None, network=good).status == "pass"
    bad = [ev("POST", "http://h/api/transfers", 422, {"detail": [{"msg": "bad"}]})]
    assert judge("api_error_code_present", a, None, network=bad).status == "fail"
    assert judge("api_error_code_present", a, None, network=[]).status == "error"


def test_fail_on_ui_layer_attaches_last_step_evidence():
    steps = [StepResult(step_idx=0, ok=True, duration_ms=1,
                        evidence=[{"kind": "screenshot", "path": "s0.png", "caption": "after step"}])]
    r = judge("element_visible", {"target": {"key": "a.b"}}, {"count": 0, "visible": False}, steps=steps)
    assert r.status == "fail" and r.evidence and r.evidence[0].path == "s0.png"


# ---------------------------------------------------------------- phase 3a accuracy fixes

def test_money_format_accepts_glued_currency_code_and_rejects_third_decimal():
    tgt = {"target": {"key": "dashboard.balance"}}
    assert judge("money_format", tgt, {"texts": ["$4,369.75USD", "$8,275.00 USD"]}).status == "pass"
    assert judge("money_format", tgt, {"texts": ["$4.369USD"]}).status == "fail"
    # A planner pattern ending in \b gets the same treatment.
    r = judge("money_format", {**tgt, "pattern": r"\$[\d,]+\.\d{2}\b"}, {"texts": ["$4,369.75USD"]})
    assert r.status == "pass"


def test_text_matches_spans_lines():
    probe = {"text": "From account\nEveryday Checking\nTo account\nRainy Day Savings"}
    assert judge("text_matches", {"pattern": "Checking.*Savings"}, probe).status == "pass"
    assert judge("text_not_matches", {"pattern": "Checking.*Savings"}, probe).status == "fail"
    assert judge("text_matches", {"pattern": "(?i"}, probe).status == "error"


def _amount_rows(amounts):
    return [{"id": tid, "cells": {"Description": "x", "Amount": amt}} for tid, amt in amounts]


def test_rows_equal_api_set_unfiltered_checks_row_amounts():
    api = [{"id": "t1", "type": "deposit", "status": "completed", "amount": "700.00", "occurred_at": "2025-01-01T00:00:00Z"},
           {"id": "t2", "type": "withdrawal", "status": "pending", "amount": "125.00", "occurred_at": "2025-01-02T00:00:00Z"}]
    good = {"displayed_ids": ["t1", "t2"], "all_tx": api,
            "rows": _amount_rows([("t1", "+$700.00"), ("t2", "Pending\n−$125.00")])}
    t = "History rows show all columns"
    assert judge("rows_equal_api_set", {"filters": {}}, good, title=t).status == "pass"
    bad = {**good, "rows": _amount_rows([("t1", "+$700.00"), ("t2", "No change:\n$0.00")])}
    r = judge("rows_equal_api_set", {"filters": {}}, bad, title=t)
    assert r.status == "fail" and "t2" in r.observed and "125.00" in r.observed
    # A scenario about something else (e.g. clearing filters) only gets a note.
    r = judge("rows_equal_api_set", {"filters": {}}, bad, title="Clear filters restores history")
    assert r.status == "pass" and "note" in r.observed
    # An explicit check_amounts flag (added by the planner's API cross-check) judges amounts
    # regardless of how the scenario is titled.
    r = judge("rows_equal_api_set", {"filters": {}, "check_amounts": True}, bad, title="Open history page")
    assert r.status == "fail" and "t2" in r.observed
    # With an active filter the oracle is about the filter: amounts are only noted.
    api_dep = [api[0], {**api[1], "type": "deposit"}]
    r = judge("rows_equal_api_set", {"filters": {"type": "deposit"}}, {**bad, "all_tx": api_dep})
    assert r.status == "pass" and "note" in r.observed
    # No amount column / no money in it: nothing judged.
    assert judge("rows_equal_api_set", {"filters": {}},
                 {**good, "rows": [{"id": "t1", "cells": {"Memo": "x"}}]}).status == "pass"
