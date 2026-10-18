"""Explorer tests: offline helpers on recorded traffic, plus a live crawl of the local target."""
import json
import os
import urllib.request
from pathlib import Path

import pytest

from qa_agent.agents.explorer import (
    detect_timezone,
    endpoints_from_network,
    explore,
    extract_entities,
    template_path,
)
from qa_agent.contracts import NetworkEvent, SiteMap
from qa_agent.llm.client import FakeLLM

RECORDED = Path(__file__).parent / "fixtures" / "recorded"
LIVE_URL = os.environ.get("QA_LIVE_URL", "http://localhost:5173")


def _har_events() -> list[NetworkEvent]:
    har = json.loads((RECORDED / "api.har").read_text())
    out = []
    for i, e in enumerate(har["log"]["entries"]):
        r, resp = e["request"], e["response"]
        if resp["status"] <= 0:
            continue
        text = resp.get("content", {}).get("text")
        try:
            body = json.loads(text) if text else None
        except ValueError:
            body = text
        out.append(NetworkEvent(method=r["method"], url=r["url"], status=resp["status"], resp_body=body, t_ms=i))
    return out


def test_template_path():
    assert template_path("/accounts/00000000-0000-0000-0000-000000000101") == "/accounts/{id}"
    assert template_path("/orders/12345/items") == "/orders/{id}/items"
    assert template_path("/transactions") == "/transactions"
    assert template_path("/") == "/"


def test_entities_from_recorded_traffic():
    events = _har_events()
    base = "http://localhost:8000"
    ent = extract_entities(events, base)
    assert ent["accounts_endpoint"] == "/api/accounts"
    assert len(ent["accounts"]) >= 2
    acc = ent["accounts"][0]
    assert set(acc) == {"id", "name", "type", "masked_number", "balance"}
    assert acc["balance"].replace(".", "").isdigit()
    assert ent["transactions_endpoint"] == "/api/transactions"
    assert "account_id" in ent["transactions_query_params"]
    assert ent["transactions_sample_count"] > 0
    assert ent["date_span"][0] <= ent["date_span"][1]
    assert ent["currency"]
    eps = {(e.method, e.path_template) for e in endpoints_from_network(events, base)}
    assert ("GET", "/api/accounts/{id}") in eps and ("POST", "/api/demo/session") in eps


def test_timezone_detection():
    assert detect_timezone(["Dates use Pacific Time."], []) == "America/Los_Angeles"
    assert detect_timezone(["All times in Eastern Time"], []) == "America/New_York"
    assert detect_timezone(["Times are shown in UTC"], []) == "UTC"
    assert detect_timezone(["Shown in Europe/Paris time"], []) == "Europe/Paris"
    assert detect_timezone([], ["Jan 3, 2025, 9:00 AM PST and Jul 2, 2025, 9:40 AM PDT"]) == "America/Los_Angeles"
    assert detect_timezone(["Checking account"], ["no times here"]) is None


def _live_up() -> bool:
    try:
        urllib.request.urlopen(LIVE_URL, timeout=2)
        return True
    except Exception:
        return False


@pytest.mark.live
@pytest.mark.skipif(not _live_up(), reason=f"{LIVE_URL} unreachable")
async def test_explore_live(tmp_path):
    from qa_agent.browser.session import launch

    # One page gets canned LLM labels; every other page falls back to the heuristic labeler.
    llm = FakeLLM({
        ("explorer", "label:/profile"): {
            "page": "profile", "title": "Profile", "purpose": "Shows the demo customer's details",
            "renames": [{"from_key": "profile.profile_name", "to_key": "profile.name"}], "forms": {},
        },
    })
    b = await launch(LIVE_URL, headed=False, evidence_dir=tmp_path, llm=llm)
    try:
        sm = await explore(b, llm)
        assert not b.blocked_requests  # crawl never tried to mutate (filter forms are GET-only)
    finally:
        await b.close()

    assert isinstance(sm, SiteMap)
    assert sm.api_base and sm.api_base.startswith("http")
    ent = sm.entities
    assert ent["accounts_endpoint"].endswith("accounts")
    assert ent["transactions_endpoint"].endswith("transactions")
    assert ent["accounts"] and all(a["balance"] for a in ent["accounts"])
    assert ent["timezone"] == "America/Los_Angeles"
    assert len(ent["transactions_query_params"]) >= 3
    assert sm.auth["entry"]["name"] and sm.auth["post_login_route"] != sm.auth["landing_route"]
    assert sm.auth["logout"]["key"] == "header.logout"
    assert sm.auth["protected_routes"]
    assert any("{id}" in r for r in sm.pages)  # one detail page
    assert "profile.name" in sm.pages["/profile"].elements and sm.pages["/profile"].title == "Profile"
    keys = {k for p in sm.pages.values() for k in p.elements}
    assert {"transfer.amount", "transfer.submit", "transfer.source", "transfer.destination"} <= keys
    assert any(k.startswith("nav.") for k in keys)
    labelled = [c["purpose"] for c in llm.calls if c["agent"] == "explorer"]
    assert any(p.startswith("label:") for p in labelled)
