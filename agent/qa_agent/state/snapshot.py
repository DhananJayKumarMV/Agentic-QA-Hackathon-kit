"""Live state snapshots of the target's accounts and transactions (CONVENTIONS §1, §5).

The StateManager is the only place that reads balances/transactions through the API. It
keeps the raw lists of the last snapshot for parameter resolution and probes, records the
mutation ledger, and reverses money that scenarios moved.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ..browser.port import BrowserPort
from ..contracts import Compensation, LedgerEntry, NetworkEvent, Scenario, ScenarioRun, SiteMap, Snapshot
from ..dsl.params import (
    ParamContext,
    account_balance,
    extract_list,
    fmt_money,
    make_run_tag,
    make_unique_amount,
    obj_id,
)


class SnapshotError(RuntimeError):
    pass


@dataclass
class ScenarioContext:
    """What the StateManager needs to know about a scenario to attribute and reverse its money."""

    scenario: Scenario
    run_tag: str
    params: dict[str, str] = field(default_factory=dict)


def event_key(ev: NetworkEvent) -> tuple[str, str, int]:
    return (ev.method.upper(), ev.url, ev.t_ms)


def path_of(url: str) -> str:
    """'http://h/api/x?a=1' -> '/api/x?a=1' (already-relative paths are returned unchanged)."""
    p = urlparse(url)
    if not p.scheme:
        return url
    return p.path + (f"?{p.query}" if p.query else "")


class StateManager:
    def __init__(self, browser: BrowserPort, sitemap: SiteMap, run_id: str) -> None:
        self.browser = browser
        self.sitemap = sitemap
        self.run_id = run_id
        self.ledger: list[LedgerEntry] = []
        self.compensations: list[Compensation] = []
        # Raw API lists from the most recent snapshot.
        self.accounts: list[dict] = []
        self.transactions: list[dict] = []
        self.last_snapshot: Snapshot | None = None
        self._last_snapshot_mono: float = 0.0
        # Events issued by the agent itself (snapshots, probes), excluded from run.network.
        self.own_event_keys: set[tuple[str, str, int]] = set()
        # Transaction ids the agent created on purpose (compensations), never "foreign".
        self.own_tx_ids: set[str] = set()
        self.contexts: dict[str, ScenarioContext] = {}
        # Learned transfer request shape: {"method", "path", "body", "src_key", "dst_key", "amount_key", ...}
        self.transfer_template: dict[str, Any] | None = None
        self._amounts: dict[str, str] = {}
        self._compensated: set[tuple[str, int]] = set()

    # ------------------------------------------------------------------ endpoints / api

    @property
    def timezone(self) -> str | None:
        return self.sitemap.entities.get("timezone")

    def _endpoint(self, kind: str) -> str | None:
        ep = self.sitemap.entities.get(f"{kind}_endpoint")
        if ep:
            return str(ep)
        for api in self.sitemap.api:
            path = api.path_template
            if api.method.upper() == "GET" and kind in path.lower() and "{" not in path and ":" not in path:
                return path
        return None

    @property
    def accounts_endpoint(self) -> str | None:
        return self._endpoint("accounts")

    @property
    def transactions_endpoint(self) -> str | None:
        return self._endpoint("transactions")

    async def api(self, method: str, path: str, body: Any = None) -> NetworkEvent:
        """Call the API through the browser session and remember the event as agent-issued."""
        ev = await self.browser.api_request(method, path, body)
        self.own_event_keys.add(event_key(ev))
        return ev

    async def fetch_accounts(self) -> list[dict]:
        ep = self.accounts_endpoint
        if not ep:
            raise SnapshotError("sitemap has no accounts_endpoint")
        ev = await self.api("GET", ep)
        if ev.status >= 400:
            raise SnapshotError(f"GET {ep} -> {ev.status}")
        return extract_list(ev.resp_body, prefer=("accounts",))

    async def fetch_transactions(self, query: dict[str, str] | None = None) -> list[dict]:
        ep = self.transactions_endpoint
        if not ep:
            raise SnapshotError("sitemap has no transactions_endpoint")
        path = ep
        if query:
            from urllib.parse import urlencode

            path += ("&" if "?" in path else "?") + urlencode(query)
        ev = await self.api("GET", path)
        if ev.status >= 400:
            raise SnapshotError(f"GET {path} -> {ev.status}")
        return extract_list(ev.resp_body, prefer=("transactions",))

    def account_query_param(self) -> str | None:
        names = list(self.sitemap.entities.get("transactions_query_params") or [])
        for api in self.sitemap.api:
            if "transaction" in api.path_template.lower():
                names.extend(api.query_params)
        for n in names:
            if "account" in n.lower():
                return n
        return None

    # ------------------------------------------------------------------ snapshots

    async def snapshot(self) -> Snapshot:
        accounts = await self.fetch_accounts()
        txs: list[dict] = []
        if self.transactions_endpoint:
            txs = await self.fetch_transactions()
        self.accounts, self.transactions = accounts, txs
        balances: dict[str, str] = {}
        for a in accounts:
            aid, bal = obj_id(a), account_balance(a)
            if aid is not None and bal is not None:
                balances[aid] = fmt_money(bal)
        snap = Snapshot(
            taken_at=datetime.now(timezone.utc),
            balances=balances,
            tx_ids=[i for i in (obj_id(t) for t in txs) if i is not None],
        )
        self.last_snapshot = snap
        self._last_snapshot_mono = time.monotonic()
        return snap

    async def ensure_fresh(self, max_age_s: float = 30.0) -> Snapshot:
        if self.last_snapshot is None or time.monotonic() - self._last_snapshot_mono > max_age_s:
            return await self.snapshot()
        return self.last_snapshot

    # ------------------------------------------------------------------ scenario context

    def run_tag(self, scenario_id: str) -> str:
        return make_run_tag(self.run_id, scenario_id)

    def begin(self, scenario: Scenario, params: dict[str, str] | None = None) -> ScenarioContext:
        ctx = self.contexts.get(scenario.id)
        if ctx is None:
            ctx = ScenarioContext(scenario=scenario, run_tag=self.run_tag(scenario.id))
            self.contexts[scenario.id] = ctx
        if params:
            ctx.params = dict(params)
        return ctx

    def builtins_for(self, scenario_id: str) -> dict[str, str]:
        ctx = self.contexts.get(scenario_id)
        tag = ctx.run_tag if ctx else self.run_tag(scenario_id)
        if scenario_id not in self._amounts:
            self._amounts[scenario_id] = make_unique_amount(self.run_id, len(self._amounts))
        return {"run_tag": tag, "unique_amount": self._amounts[scenario_id]}

    def param_context(self, scenario_id: str) -> ParamContext:
        return ParamContext(
            accounts=list(self.accounts),
            transactions=list(self.transactions),
            timezone=self.timezone,
            builtins=self.builtins_for(scenario_id),
        )

    # ------------------------------------------------------------------ ledger / compensation

    def record_ledger(self, run: ScenarioRun) -> list[LedgerEntry]:
        from .ledger import ledger_entries

        ctx = self.contexts.get(run.scenario_id)
        entries = ledger_entries(run, ctx, self.transactions)
        self.ledger.extend(entries)
        return entries

    def foreign_tx_ids(self, run: ScenarioRun) -> list[str]:
        from .ledger import foreign_tx_ids

        ctx = self.contexts.get(run.scenario_id)
        return foreign_tx_ids(run, ctx.run_tag if ctx else None, self.transactions, self.own_tx_ids)

    async def compensate(self, run: ScenarioRun) -> list[Compensation]:
        key = (run.scenario_id, run.attempt)
        if key in self._compensated:
            return []
        self._compensated.add(key)
        from .compensator import compensate_run

        comps = await compensate_run(self, run)
        self.compensations.extend(comps)
        return comps
