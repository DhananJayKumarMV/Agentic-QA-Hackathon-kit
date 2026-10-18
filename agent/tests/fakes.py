"""FakeBrowser: an in-memory BrowserPort for tests (scripted DOM-ish elements + a tiny fake bank API).

Usage:
    fb = FakeBrowser()
    fb.add_element("transfer.amount", fillable=True)
    fb.add_element("transfer.submit", on_click=lambda b: b.ui_transfer(...))
    ev = await fb.api_request("GET", "/api/accounts")

Page-side scripts are dispatched by their ``/*qa:<name>*/`` tag to ``fb.js[name]`` (callables
taking ``arg``), so probes can be tested without a real DOM. Other workstreams may reuse this.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from qa_agent.contracts import ElementRef, Evidence, NetworkEvent, PageModel, SiteMap, Target


class FakeElement:
    def __init__(self, browser: "FakeBrowser", key: str, spec: dict[str, Any]) -> None:
        self.b, self.key, self.spec = browser, key, spec

    async def _call(self, name: str, *args: Any) -> None:
        fn = self.spec.get(name)
        if fn is None:
            return
        r = fn(self.b, *args)
        if inspect.isawaitable(r):
            await r

    async def click(self) -> None:
        if self.spec.get("disabled"):
            await asyncio.sleep(5)  # like Playwright waiting for an enabled element
            raise TimeoutError("element is disabled")
        self.b.clicks.append(self.key)
        await self._call("on_click")

    async def fill(self, value: str) -> None:
        self.spec["value"] = value
        self.b.values[self.key] = value
        await self._call("on_fill", value)

    async def select(self, value: str) -> None:
        options = self.spec.get("options")
        if options is not None:
            # value first, then visible label
            match = next((v for v, label in options if v == value), None) or \
                next((v for v, label in options if label == value), None)
            if match is None:
                raise ValueError(f"no option {value!r}")
            value = match
        self.spec["value"] = value
        self.b.values[self.key] = value
        await self._call("on_select", value)

    async def text(self) -> str:
        t = self.spec.get("text", "")
        return t(self.b) if callable(t) else t

    async def all_texts(self) -> list[str]:
        t = self.spec.get("texts")
        if t is None:
            return [await self.text()]
        return t(self.b) if callable(t) else list(t)

    async def attribute(self, name: str) -> str | None:
        return (self.spec.get("attrs") or {}).get(name)

    async def is_visible(self) -> bool:
        return bool(self.spec.get("visible", True))

    async def is_disabled(self) -> bool:
        return bool(self.spec.get("disabled", False))

    async def count(self) -> int:
        return int(self.spec.get("count", len(self.spec["texts"]) if self.spec.get("texts") is not None else 1))


class FakeBrowser:
    """Scripted BrowserPort. API paths: /api/accounts, /api/transactions, /api/transfers."""

    def __init__(self, base_url: str = "http://app.test", *, accounts: list[dict] | None = None,
                 transactions: list[dict] | None = None, evidence_dir: Path | None = None) -> None:
        self.base_url = base_url
        self.api_base = "http://api.test"
        self.path = "/"
        self.history: list[str] = []
        self.viewport = (1280, 800)
        self.logged_in = False
        self.elements: dict[str, dict[str, Any]] = {}
        self.values: dict[str, str] = {}
        self.clicks: list[str] = []
        self.log: list[NetworkEvent] = []
        self.delays: list[tuple[str, str, int]] = []
        self.screens: list[str] = []
        self.evidence_dir = evidence_dir or Path("/tmp/qa-fake-evidence")
        self.t0 = time.monotonic()
        self._tick = 0
        self.accounts: list[dict] = accounts if accounts is not None else [
            {"id": "acc-1", "name": "Primary", "type": "checking", "masked_number": "**** 0001",
             "balance": "100.00"},
            {"id": "acc-2", "name": "Reserve", "type": "savings", "masked_number": "**** 0002",
             "balance": "50.00"},
        ]
        self.transactions: list[dict] = transactions if transactions is not None else [
            {"id": "tx-1", "type": "deposit", "status": "completed", "amount": "10.00", "memo": "seed a",
             "occurred_at": "2025-09-03T03:30:00Z", "source_account": None,
             "destination_account": {"id": "acc-1"},
             "account_deltas": [{"account_id": "acc-1", "amount_delta": "10.00"}]},
            {"id": "tx-2", "type": "withdrawal", "status": "pending", "amount": "5.00", "memo": "seed b",
             "occurred_at": "2025-08-01T18:00:00Z", "source_account": {"id": "acc-2"},
             "destination_account": None,
             "account_deltas": [{"account_id": "acc-2", "amount_delta": "0.00"}]},
        ]
        self.rows: list[dict] | Callable[["FakeBrowser"], list[dict]] = []
        self.alerts: list[dict] = []
        self.main_text = ""
        self.overflow_at: dict[int, int] = {}  # width -> scroll width
        self.fail_transfers = False  # make POST /api/transfers return 422
        self.break_balance_on_transfer = False  # debit only, no credit
        self.js: dict[str, Callable[[Any], Any]] = {
            "main_text": lambda arg: self.main_text,
            "body_text": lambda arg: self.main_text,
            "rows": lambda arg: self.rows(self) if callable(self.rows) else list(self.rows),
            "alerts": lambda arg: list(self.alerts),
            "viewport": lambda arg: {"width": self.viewport[0], "height": self.viewport[1]},
            "overflow": lambda arg: {"scroll_width": self.overflow_at.get(self.viewport[0], self.viewport[0]),
                                     "client_width": self.viewport[0]},
            "ui_balances": self._ui_balances,
            "submit": lambda arg: False,
        }
        # Auth entry element on the landing page.
        self.add_element("landing.enter", on_click=lambda b: setattr(b, "logged_in", True))
        self.add_element("header.logout", on_click=lambda b: setattr(b, "logged_in", False))

    # ---------------------------------------------------------------- scripting helpers

    def add_element(self, key: str, **spec: Any) -> dict[str, Any]:
        self.elements[key] = spec
        return spec

    def sitemap(self, **entities: Any) -> SiteMap:
        pages: dict[str, PageModel] = {}
        for key in self.elements:
            page = key.split(".")[0]
            route = f"/{page}"
            pages.setdefault(route, PageModel(route=route, title=page, purpose=page))
            pages[route].elements[key] = ElementRef(key=key, multiple=key.endswith(".row"))
        ents = {"accounts_endpoint": "/api/accounts", "transactions_endpoint": "/api/transactions",
                "transactions_query_params": ["account_id", "type", "status", "start_date", "end_date"],
                "timezone": None}
        ents.update(entities)
        return SiteMap(base_url=self.base_url, api_base=self.api_base, pages=pages,
                       auth={"entry": {"key": "landing.enter"}, "logout": {"key": "header.logout"},
                             "landing_route": "/", "protected_routes": ["/dashboard"]},
                       entities=ents)

    def _ui_balances(self, arg: Any) -> dict:
        out = {}
        for a in self.accounts:
            out[a["id"]] = {"text": f"${Decimal(a['balance']):,.2f}", "card_text": a["name"]}
        return out

    def _t(self) -> int:
        self._tick += 1
        return int((time.monotonic() - self.t0) * 1000) + self._tick

    # ---------------------------------------------------------------- fake bank API

    def _handle(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        u = urlparse(path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self.logged_in:
            return 401, {"error": {"code": "unauthenticated", "message": "no session"}}
        if method == "GET" and u.path == "/api/accounts":
            return 200, {"accounts": [dict(a) for a in self.accounts]}
        if method == "GET" and u.path == "/api/transactions":
            txs = list(self.transactions)
            if "account_id" in q:
                txs = [t for t in txs if any(d["account_id"] == q["account_id"] for d in t["account_deltas"])]
            return 200, {"transactions": [dict(t) for t in txs]}
        if method == "POST" and u.path == "/api/transfers":
            if self.fail_transfers:
                return 422, {"error": {"code": "invalid_amount", "message": "bad amount"}}
            src, dst = body["source_account_id"], body["destination_account_id"]
            amt = Decimal(str(body["amount"]))
            if amt <= 0 or src == dst:
                return 422, {"error": {"code": "invalid", "message": "invalid transfer"}}
            accts = {a["id"]: a for a in self.accounts}
            accts[src]["balance"] = str(Decimal(accts[src]["balance"]) - amt)
            deltas = [{"account_id": src, "amount_delta": str(-amt)}]
            if not self.break_balance_on_transfer:
                accts[dst]["balance"] = str(Decimal(accts[dst]["balance"]) + amt)
                deltas.append({"account_id": dst, "amount_delta": str(amt)})
            tx = {"id": f"tx-{uuid.uuid4().hex[:8]}", "type": "transfer", "status": "completed",
                  "amount": str(amt), "memo": body.get("memo"), "occurred_at": "2026-01-01T12:00:00Z",
                  "source_account": {"id": src}, "destination_account": {"id": dst}, "account_deltas": deltas}
            self.transactions.insert(0, tx)
            return 201, {"transaction": tx, "accounts": [dict(a) for a in self.accounts]}
        return 404, {"error": {"code": "not_found", "message": path}}

    def _event(self, method: str, path: str, body: Any) -> NetworkEvent:
        status, resp = self._handle(method, path, body)
        return NetworkEvent(method=method, url=self.api_base + path, status=status, req_body=body,
                            resp_body=resp, t_ms=self._t())

    async def ui_request(self, method: str, path: str, body: Any = None) -> NetworkEvent:
        """What the page's own fetch() would do: hits the API and is logged."""
        delay = next((d for m, g, d in self.delays if m == method and g in path and d > 0), 0)
        if delay:
            await asyncio.sleep(delay / 1000)
        ev = self._event(method, path, body)
        self.log.append(ev)
        return ev

    async def ui_transfer(self, src: str, dst: str, memo: str = "") -> NetworkEvent:
        return await self.ui_request("POST", "/api/transfers", {
            "source_account_id": src, "destination_account_id": dst,
            "amount": self.values.get("transfer.amount", "0"), "memo": self.values.get("transfer.memo", memo)})

    # ---------------------------------------------------------------- BrowserPort

    async def goto(self, path: str) -> None:
        self.history.append(self.path)
        self.path = path

    async def reload(self) -> None:
        return None

    async def back(self) -> None:
        if self.history:
            self.path = self.history.pop()

    async def set_viewport(self, width: int, height: int) -> None:
        self.viewport = (width, height)

    async def current_url(self) -> str:
        return self.base_url + self.path

    async def resolve(self, target: Target):
        spec = self.elements.get(target.key)
        if spec is None:
            raise LookupError(f"no element for {target.key}")
        return FakeElement(self, target.key, spec), f"fake[{target.key}]", bool(spec.get("healed"))

    async def settle(self, timeout_ms: int = 10_000) -> None:
        await asyncio.sleep(0)

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        m = re.search(r"/\*qa:(\w+)\*/", js)
        if not m or m.group(1) not in self.js:
            raise NotImplementedError(f"fake cannot evaluate: {js[:60]}")
        return self.js[m.group(1)](arg)

    async def api_request(self, method: str, path: str, body: Any = None) -> NetworkEvent:
        ev = self._event(method, path, body)
        self.log.append(ev)  # worst case: the port logs its own calls too
        return ev

    def network_log(self) -> list[NetworkEvent]:
        return list(self.log)

    def clear_network_log(self) -> None:
        self.log.clear()

    async def delay_requests(self, method: str, url_glob: str, delay_ms: int) -> None:
        self.delays.append((method, url_glob.strip("*"), delay_ms))

    async def screenshot(self, name: str, highlight: Target | None = None) -> Evidence:
        self.screens.append(name)
        return Evidence(kind="screenshot", path=str(self.evidence_dir / f"{name}.png"), caption=name)

    async def aria_snapshot(self, max_chars: int = 12_000) -> str:
        return "- main"

    async def new_context(self, fresh: bool = False) -> None:
        if fresh:
            self.logged_in = False
        self.path = "about:blank"

    async def close(self) -> None:
        return None
