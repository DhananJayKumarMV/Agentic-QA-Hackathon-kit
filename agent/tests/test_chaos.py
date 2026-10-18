"""Chaos mode: latency on API requests, 503 only on GET, deterministic per seed."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from qa_agent.browser.chaos import Chaos, ChaosConfig
from qa_agent.browser.session import launch

PAGE = b"""<!doctype html><main><h1>Chaos fixture</h1></main>
<script>
window.call = async (method, path) => {
  const t = performance.now();
  try {
    const r = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
                                 body: method === 'GET' ? undefined : '{}'});
    return {status: r.status, ms: performance.now() - t};
  } catch (e) { return {status: 0, ms: performance.now() - t}; }
};
</script>"""


class _Handler(BaseHTTPRequestHandler):
    hits: list[str] = []

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _Handler.hits.append(f"GET {self.path}")
        if self.path.startswith("/api/"):
            self._send(200, json.dumps({"ok": True}).encode(), "application/json")
        else:
            self._send(200, PAGE, "text/html")

    def do_POST(self):  # noqa: N802
        _Handler.hits.append(f"{self.command} {self.path}")
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._send(201, b'{"created": true}', "application/json")

    do_DELETE = do_POST

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("localhost", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://localhost:{httpd.server_address[1]}"
    httpd.shutdown()


def test_decisions_are_seeded_and_never_fail_mutations():
    cfg = ChaosConfig(latency_ms=1000, error_rate=0.5, seed=7)
    urls = [f"http://x/api/items?page={i % 5}" for i in range(60)]
    a = [Chaos(cfg).decide("GET", u) for u in urls[:1]]
    one, two = Chaos(cfg), Chaos(cfg)
    seq1 = [one.decide("GET", u) for u in urls]
    seq2 = [two.decide("GET", u) for u in urls]
    assert seq1 == seq2 and a[0] == seq1[0]
    assert any(d.fail for d in seq1) and not all(d.fail for d in seq1)
    assert all(0 <= d.delay_ms <= 1000 for d in seq1)
    other = Chaos(ChaosConfig(latency_ms=1000, error_rate=0.5, seed=8))
    assert [other.decide("GET", u) for u in urls] != seq1
    always = Chaos(ChaosConfig(error_rate=1.0, seed=1))
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert not always.decide(m, "http://x/api/transfers").fail
    assert always.decide("GET", "http://x/api/accounts").fail
    assert not ChaosConfig().enabled and ChaosConfig(latency_ms=1).enabled


async def test_503_only_on_get_api_requests(server, tmp_path):
    b = await launch(server, evidence_dir=tmp_path, chaos=ChaosConfig(error_rate=1.0, seed=3))
    try:
        await b.goto("/")  # documents are never faulted
        assert "Chaos fixture" in await b.evaluate("() => document.body.innerText")
        _Handler.hits.clear()
        get = await b.evaluate("() => window.call('GET', '/api/accounts')")
        post = await b.evaluate("() => window.call('POST', '/api/transfers')")
        delete = await b.evaluate("() => window.call('DELETE', '/api/transfers/1')")
        assert get["status"] == 503
        assert post["status"] == 201 and delete["status"] == 201
        assert "POST /api/transfers" in _Handler.hits and "DELETE /api/transfers/1" in _Handler.hits
        assert "GET /api/accounts" not in _Handler.hits  # the faulted GET never reached the server
        assert b.chaos.was_injected("GET", f"{server}/api/accounts")
        # The agent's own API client bypasses page routing: snapshots are never faulted.
        ev = await b.api_request("GET", f"{server}/api/accounts")
        assert ev.status == 200
    finally:
        await b.close()


async def test_latency_applied_and_toggleable(server, tmp_path):
    cfg = ChaosConfig(latency_ms=800, seed=11)
    b = await launch(server, evidence_dir=tmp_path)
    try:
        await b.goto("/")
        await b.set_chaos(cfg)
        url = f"{server}/api/slow-check"
        expected = Chaos(cfg).decide("GET", url).delay_ms  # same seed -> same first decision
        res = await b.evaluate("() => window.call('GET', '/api/slow-check')")
        assert res["status"] == 200
        assert res["ms"] >= expected - 30
        assert b.chaos.stats["delayed"] >= (1 if expected else 0)
        # New contexts keep chaos installed.
        await b.new_context()
        await b.goto("/")
        await b.set_chaos(ChaosConfig(error_rate=1.0))
        assert (await b.evaluate("() => window.call('GET', '/api/x')"))["status"] == 503
        await b.set_chaos(None)
        t = time.monotonic()
        assert (await b.evaluate("() => window.call('GET', '/api/x')"))["status"] == 200
        assert time.monotonic() - t < 0.5
    finally:
        await b.close()


async def test_chaos_coexists_with_delay_and_guard(server, tmp_path):
    b = await launch(server, evidence_dir=tmp_path, chaos=ChaosConfig(latency_ms=50, seed=1))
    try:
        await b.goto("/")
        await b.set_mutation_guard(True, allow=[])
        await b.delay_requests("POST", "/api/*", 300)
        post = await b.evaluate("() => window.call('POST', '/api/transfers')")
        assert post["status"] == 0  # guard still aborts mutations first
        await b.set_mutation_guard(False)
        post = await b.evaluate("() => window.call('POST', '/api/transfers')")
        assert post["status"] == 201 and post["ms"] >= 280  # delay_requests still holds the request
    finally:
        await b.close()


async def test_fork_and_crash_recovery(server, tmp_path):
    b = await launch(server, evidence_dir=tmp_path, chaos=ChaosConfig(error_rate=1.0))
    try:
        await b.goto("/")
        w = await b.fork("w1")
        await w.goto("/")
        assert (await w.evaluate("() => window.call('GET', '/api/x')"))["status"] == 503  # chaos carried over
        await w.close()
        assert await b.is_healthy()
        await b.page.close()  # simulate a crashed tab
        assert not await b.is_healthy()
        obs = await b.ensure_alive()
        assert obs and await b.is_healthy()
        await b.goto("/")
        await b._browser.close()  # simulate a dead browser process
        obs = await b.ensure_alive()
        assert any("relaunched" in o for o in obs) and b.relaunches == 1
        await b.goto("/")
        assert "Chaos fixture" in await b.evaluate("() => document.body.innerText")
    finally:
        await b.close()
