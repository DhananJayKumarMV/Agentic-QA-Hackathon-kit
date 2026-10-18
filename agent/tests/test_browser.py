"""Browser layer tests against a local static fixture served by a tiny http.server."""
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from qa_agent.browser import a11y, waits
from qa_agent.browser.locators import ResolveError
from qa_agent.browser.session import launch
from qa_agent.contracts import ElementRef, PageModel, SiteMap, Target
from qa_agent.llm.client import FakeLLM

STATIC = Path(__file__).parent / "fixtures" / "static"
RECORDED = Path(__file__).parent / "fixtures" / "recorded"


class _Handler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/slow.json"):
            time.sleep(0.6)
        return super().do_GET()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("localhost", 0), partial(_Handler, directory=str(STATIC)))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://localhost:{httpd.server_address[1]}"
    httpd.shutdown()


def _sitemap(base: str) -> SiteMap:
    els = {
        "page.amount": ElementRef(key="page.amount", role="textbox", name="Amount (USD)", label="Amount (USD)"),
        "page.source": ElementRef(key="page.source", label="From account"),
        "page.submit": ElementRef(key="page.submit", role="button", name="Send money"),
        "page.card": ElementRef(key="page.card", test_id="item-card", multiple=True),
        "page.by_css": ElementRef(key="page.by_css", css="button.go"),
        "page.stale": ElementRef(key="page.stale", role="button", name="Transmit funds"),
        "header.logout": ElementRef(key="header.logout", test_id="nope", role="button", name="Sign out"),
    }
    return SiteMap(base_url=base, pages={"/app.html": PageModel(route="/app.html", title="t", purpose="p", elements=els)})


# ---------------------------------------------------------------- pure helpers

def test_loading_regex_requires_ellipsis():
    assert waits.is_loading_text("Loading…")
    assert waits.is_loading_text("Loading transactions...")
    assert waits.is_loading_text("Checking your demo session…")
    assert waits.is_loading_text("Submitting transfer…")
    assert not waits.is_loading_text("Checking")
    assert not waits.is_loading_text("Everyday Checking\nAccount number ending •••• 12")
    assert not waits.is_loading_text("Checking\n...")


def test_compact_snapshot_trims_rows():
    raw = (RECORDED / "aria_transactions.yaml").read_text()
    out = a11y.compact(raw, max_chars=4000)
    assert "more rows" in out
    assert len(out) <= 4000
    assert 'combobox "Type"' in out


# ---------------------------------------------------------------- live browser on static fixture

async def test_settle_waits_for_loading_busy_and_network(server, tmp_path):
    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=None)
    try:
        await b.page.goto(server + "/app.html", wait_until="domcontentloaded")
        t0 = time.monotonic()
        await b.settle(timeout_ms=8000)
        elapsed = time.monotonic() - t0
        assert not b.last_settle["timed_out"], b.last_settle
        # "Checking" (account type) on the page must not block settle; "Loading…" must.
        text = await b.evaluate("() => document.body.innerText")
        assert "Checking" in text and "Loading…" not in text
        assert await b.evaluate("() => document.getElementById('late').innerText") == "Loaded content"
        assert await b.evaluate("() => document.getElementById('fetched').innerText") == "fetched ok"
        assert elapsed < 6
        log = b.network_log()
        assert any("slow.json" in e.url and e.status == 200 and e.resp_body["message"] == "fetched ok" for e in log)
        assert b.api_base == server
        ev = await b.api_request("GET", "/slow.json")
        assert ev.status == 200 and ev.resp_body["items"][0]["balance"] == "1.00"
    finally:
        await b.close()


async def test_static_page_with_only_checking_word_settles_fast(tmp_path):
    b = await launch("http://localhost", headed=False, evidence_dir=tmp_path, llm=None)
    try:
        await b.page.set_content("<main><h1>Accounts</h1><p>Checking</p><p>Savings</p></main>")
        t0 = time.monotonic()
        await b.settle(timeout_ms=3000)
        assert not b.last_settle["timed_out"]
        assert time.monotonic() - t0 < 1.5
    finally:
        await b.close()


async def test_locator_chain(server, tmp_path):
    sm = _sitemap(server)
    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=None, sitemap=sm)
    try:
        await b.goto("/app.html")
        el, desc, healed = await b.resolve(Target(key="page.amount"))
        assert desc.startswith("role=textbox") and not healed
        await el.fill("12.50")
        src, desc, _ = await b.resolve(Target(key="page.source"))
        assert desc.startswith("label=")
        await src.select("a2")  # by value
        assert await b.evaluate("() => document.getElementById('src').value") == "a2"
        await src.select("Primary (1)")  # by label
        assert await b.evaluate("() => document.getElementById('src').value") == "a1"
        await src.select("reserve")  # contains, case-insensitive
        assert await b.evaluate("() => document.getElementById('src').value") == "a2"

        cards, desc, _ = await b.resolve(Target(key="page.card"))
        assert desc == "test_id=item-card" and await cards.count() == 2
        second, _, _ = await b.resolve(Target(key="page.card", nth=1))
        assert "Reserve" in await second.text()
        filt, _, _ = await b.resolve(Target(key="page.card", text_contains="Primary"))
        assert await filt.count() == 1

        css, desc, _ = await b.resolve(Target(key="page.by_css"))
        assert desc == "css=button.go"
        # test_id misses, falls through to role+name
        lo, desc, _ = await b.resolve(Target(key="header.logout"))
        assert desc.startswith("role=button")
        # key not in sitemap: generic guess from the key words
        nav, desc, _ = await b.resolve(Target(key="nav.items"))
        assert "guess" in desc and await nav.count() == 1

        sub, _, _ = await b.resolve(Target(key="page.submit"))
        assert not await sub.is_disabled()
        await sub.click()
        await b.settle()
        assert await b.evaluate("() => document.getElementById('result').innerText") == "Sent 12.50"

        with pytest.raises(ResolveError):
            await b.resolve(Target(key="page.stale"), wait_ms=200)

        shot = await b.screenshot("with highlight", highlight=Target(key="page.submit"))
        assert Path(shot.path).exists() and "highlight" in shot.caption
        snap = await b.aria_snapshot()
        assert 'button "Send money"' in snap
    finally:
        await b.close()


async def test_heal_uses_llm_and_writes_back(server, tmp_path):
    sm = _sitemap(server)
    llm = FakeLLM({("executor_heal", "heal"): {"found": True, "role": "button", "name": "Send money"}})
    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=llm, sitemap=sm)
    try:
        await b.goto("/app.html")
        el, desc, healed = await b.resolve(Target(key="page.stale"), wait_ms=200)
        assert healed and desc.startswith("healed role=button")
        ref = sm.pages["/app.html"].elements["page.stale"]
        assert ref.name == "Send money" and ref.healed_from and "Transmit funds" in ref.healed_from
        assert llm.calls[0]["agent"] == "executor_heal"
        # second resolve reuses the healed ref without another call
        _, _, healed2 = await b.resolve(Target(key="page.stale"), wait_ms=200)
        assert not healed2 and len(llm.calls) == 1
    finally:
        await b.close()


async def test_delay_requests_and_new_context(server, tmp_path):
    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=None)
    try:
        await b.delay_requests("GET", "**/slow.json*", 800)
        t0 = time.monotonic()
        await b.goto("/app.html")
        assert time.monotonic() - t0 >= 1.2  # 600ms server + 800ms delay
        await b.clear_delays()
        await b.new_context(fresh=True)
        await b.goto("/app.html")
        assert b.trace_paths and b.trace_paths[0].exists()
    finally:
        await b.close()


async def test_mutation_guard_blocks_post(server, tmp_path):
    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=None)
    try:
        await b.goto("/app.html")
        await b.set_mutation_guard(True)
        status = await b.evaluate("() => fetch('slow.json', {method: 'POST'}).then(r => r.status).catch(() => 'blocked')")
        assert status == "blocked" and b.blocked_requests
        await b.set_mutation_guard(False)
    finally:
        await b.close()


# ---------------------------------------------------------------- balance capture (ui_matches_api probe)

_ACCTS = [{"id": "a-101", "labels": ["Everyday Checking", "•••• 1042"]},
          {"id": "a-102", "labels": ["Rainy Day Savings", "•••• 7781"]}]


async def test_ui_balances_cards_and_detail_page_ignore_row_amounts(server, tmp_path):
    from qa_agent.dsl.probes import JS_UI_BALANCES

    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=None)
    try:
        await b.page.goto(server + "/app.html", wait_until="domcontentloaded")
        await b.page.set_content("""<main>
          <a class="account-card"><h3>Everyday Checking</h3><p>•••• 1042</p><p>Available balance</p>
            <p data-testid="account-balance">$4,369.75<span>USD</span></p></a>
          <a class="account-card"><h3>Rainy Day Savings</h3><p>•••• 7781</p><p>Available balance</p>
            <p data-testid="account-balance">$8,275.00<span>USD</span></p></a></main>""")
        out = await b.evaluate(JS_UI_BALANCES, {"accounts": _ACCTS, "selectors": []})
        assert {k: v["text"] for k, v in out["balances"].items()} == {"a-101": "$4,369.75USD", "a-102": "$8,275.00USD"}

        # Detail page: one balance, plus a history table whose rows name the other account.
        await b.page.set_content("""<main><div class="page-header"><h1>Everyday Checking</h1>
            <div class="balance-panel"><p class="balance-panel__value">$4,369.75 USD</p></div></div>
          <table><tbody>
            <tr><td>Rainy Day Savings (•••• 7781)</td><td class="amount">$175.00</td></tr>
            <tr><td>Everyday Checking (•••• 1042)</td><td>$250.00</td></tr></tbody></table></main>""")
        out = await b.evaluate(JS_UI_BALANCES, {"accounts": _ACCTS, "selectors": []})
        assert {k: v["text"] for k, v in out["balances"].items()} == {"a-101": "$4,369.75 USD"}

        # No account label near the balance: the account id in the URL decides.
        await b.page.set_content("""<main><h1>Details</h1><p data-testid="account-balance">$8,275.00</p>
          <ul><li>Everyday Checking</li><li>Rainy Day Savings</li></ul></main>""")
        await b.page.evaluate("() => history.pushState({}, '', '/accounts/a-102/')")
        out = await b.evaluate(JS_UI_BALANCES, {"accounts": _ACCTS, "selectors": []})
        assert {k: v["text"] for k, v in out["balances"].items()} == {"a-102": "$8,275.00"}
    finally:
        await b.close()


async def test_settle_restarts_window_after_client_side_url_change(server, tmp_path):
    b = await launch(server, headed=False, evidence_dir=tmp_path, llm=None)
    try:
        await b.page.goto(server + "/app.html", wait_until="domcontentloaded")
        await b.settle(timeout_ms=8000)
        # Router-style: the URL changes 200ms after the click, the data fetch starts 220ms later.
        await b.page.evaluate("""() => { window.__done = false; setTimeout(() => {
            history.pushState({}, '', '?filter=1');
            setTimeout(() => fetch('/slow.json').then(() => { window.__done = true; }), 220);
          }, 200); }""")
        await b.settle(timeout_ms=8000)
        assert b.last_settle.get("url_changed") is True
        assert await b.evaluate("() => window.__done") is True
    finally:
        await b.close()
