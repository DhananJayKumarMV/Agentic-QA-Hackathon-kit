# Cross-agent conventions (frozen with contracts)

These conventions bind the Planner (writes them), the Executor (resolves and probes them)
and the Verifier (judges them). Nothing here may name DemoBank-specific values: everything
is discovered from the PRD and the live site.

## 1. Python entry points

| Module | Signature |
|---|---|
| `agents/spec_analyst.py` | `async def extract_requirements(prd_text: str, llm: LLM) -> RequirementSet` |
| `browser/session.py` | `async def launch(base_url: str, *, headed: bool, evidence_dir: Path, llm: LLM \| None, sitemap: SiteMap \| None = None) -> PlaywrightBrowser` (implements `BrowserPort`) |
| `agents/explorer.py` | `async def explore(browser: BrowserPort, llm: LLM) -> SiteMap` |
| `agents/test_planner.py` | `async def plan_tests(reqs: RequirementSet, sitemap: SiteMap, llm: LLM) -> TestPlan` |
| `state/snapshot.py` | `class StateManager: __init__(browser, sitemap, run_id)`; `async snapshot() -> Snapshot`; `async compensate(run: ScenarioRun) -> list[Compensation]`; `ledger: list[LedgerEntry]`; `compensations: list[Compensation]` |
| `agents/executor.py` | `async def run_scenario(scenario: Scenario, browser: BrowserPort, sitemap: SiteMap, state: StateManager, *, attempt: int, evidence_dir: Path) -> ScenarioRun` |
| `agents/verifier.py` | `def evaluate(scenario, run, sitemap) -> ScenarioVerdict` (deterministic); `async def judge_pending(verdicts, runs, llm) -> None` (fills llm_judge oracles); `async def triage_failures(verdicts, reqs, llm) -> None`; `def rollup(reqs, plan, verdicts) -> list[RequirementVerdict]`; `def needs_retry(verdict, run) -> bool` |
| `agents/reporter.py` | `def build_report(...) -> AuditReport`; `def render(report: AuditReport, out_dir: Path, formats=("json","md","html")) -> dict[str, Path]`; `async def render_pdf(html_path: Path) -> Path` |

`LLM` is the Protocol in `llm/client.py`. Use `config.REASONING_MODEL` / `config.FAST_MODEL`, never literal model strings.

## 2. SiteMap conventions (Explorer fills, everyone reads)

`SiteMap.entities` keys:
- `accounts`: `[{id, name, type, masked_number, balance}]` (from whichever API response holds a list of objects with a balance-like field)
- `accounts_endpoint`: API path returning that list, e.g. `"/api/..."`
- `transactions_endpoint`: API path returning the transaction list; `transactions_query_params`: list of names seen in the UI's own requests
- `transactions_sample_count`, `tx_types`, `tx_statuses`, `date_span`: `[min_iso, max_iso]`
- `timezone`: IANA name if the UI states one (e.g. a hint saying "Pacific Time" -> `"America/Los_Angeles"`), else `null`
- `currency`: as displayed/returned

`SiteMap.auth`: `{entry: ElementRef-like dict, logout: ElementRef-like dict, landing_route, protected_routes: [...], post_login_route}`

Element keys are `"<page>.<thing>"`, lowercase, e.g. `transfer.amount`, `transfer.submit`, `history.filter_type`, `history.row`, `dashboard.account_card`, `nav.transactions`, `header.logout`, `header.customer`. Lists use `multiple=True`.

## 3. ParamBinding query vocabulary

Built-ins always available in `{{...}}`: `run_tag` (unique per run+scenario, put it in every memo), `unique_amount` (small unique decimal string per scenario, e.g. "1.07").

| source | query | resolves to |
|---|---|---|
| `account` | `{"select": "type=<t>" \| "index=<n>" \| "not=<param>", "field": "id"\|"name"\|"masked_number"\|"type"}` | from live `snapshot` accounts |
| `balance` | `{"of": "<account param>", "plus": "<decimal>"}` (plus optional, may be negative) | decimal string, 2 dp |
| `transaction` | `{"pick": "earliest"\|"latest"\|"any", "where": {"type"?, "status"?, "account"?: "<param>"}, "field": "id"\|"local_date"\|"type"\|"status"\|"amount"}` | `local_date` = date in `entities.timezone` (UTC if null), `YYYY-MM-DD` |
| `date` | `{"from": "<param>", "offset_days": <int>}` | `YYYY-MM-DD` |
| `literal` | `{"value": "<string>"}` | as is |
| `derived` | `{"expr": "unique_amount" \| "run_tag"}` | built-ins |

Unresolvable binding -> scenario `aborted_reason="unresolvable param <name>"` -> verdict `unverified`.

Step `select` values: try option value first, then visible label.

## 4. Oracle args and probe outputs

The Executor runs a **probe** for each oracle after the steps finish and stores the raw facts in
`run.observations["probes"][<oracle index>]`. The Verifier only reads probes, snapshots and `run.network`.
`target` args are `Target` dicts. Regexes are Python `re` syntax.

| type | args | probe output | passes when |
|---|---|---|---|
| `url_matches` | `pattern` | `{url}` | `re.search(pattern, path+query)` |
| `element_visible` / `element_absent` | `target` | `{count, visible}` | visible / not visible |
| `text_matches` / `text_not_matches` | `pattern`, `target?`, `ignore_case?` | `{text}` (target text, else `main` text) | match / no match |
| `money_format` | `target`, `pattern?` | `{texts: [...]}` | every text contains a 2-decimal currency amount (default `\$?-?[\d,]+\.\d{2}\b`) |
| `masked_identifier` | `{}` | `{visible_text, url, full_ids: [...]}` (full ids/numbers from API account objects) | no full id appears in `visible_text` |
| `balance_delta` | `deltas: {<account param>: "<signed decimal or {{param}}>"}` | uses `state_before/after` | after-before == expected per account (Decimal) |
| `no_state_change` | `{}` | uses snapshots | balances equal and no new tx ids (ignoring foreign activity) |
| `new_tx_count` | `n` | uses snapshots | exactly `n` new tx ids |
| `tx_present_in_histories` | `accounts: [<account params>]` | `{tx_id, present: {account_id: bool}}` (tx_id = id from the scenario's successful POST response, else the single new snapshot tx) | all true |
| `rows_satisfy_filters` | `filters: {account?, type?, status?, start_date?, end_date?}` (values may be `{{param}}`) | `{rows: [{id, datetime, cells: {label: text}, account_ids: [...]}]}` plus `all_tx` (API, unfiltered) | every displayed row, looked up in `all_tx` by id, satisfies every filter; dates inclusive in `entities.timezone` |
| `rows_equal_api_set` | `filters` | `{displayed_ids: [...], all_tx: [...]}` | set(displayed) == set computed from `all_tx` by applying the filters (inclusive dates) |
| `row_count_equals` | `n` | `{count}` | equal |
| `empty_state_shown` | `{}` | `{row_count, status_texts: [...], alert_texts: [...]}` | 0 rows, a status text present, no error alert |
| `ui_matches_api` | `{}` | `{ui_balances: {account_id: text}, api_accounts: [...]}` | parsed money equals API balance per account |
| `api_status` | `method`, `path_glob`, `status` or `status_in` | uses `run.network` | some matching event has the status |
| `api_error_code_present` | `method?`, `path_glob` | uses `run.network` | a matching response body has `error.code` (or equivalent error object) |
| `no_success_ui` | `{}` | `{alerts: [{role, classes, title, text}]}` | no alert/status whose classes contain "success" or whose title/text says it completed/succeeded |
| `button_disabled_during` | `target`, `request_glob`, `delay_ms?` | `{disabled_while_pending, busy_text, matching_requests}` (Executor delays the request, clicks twice, samples state) | disabled (or busy) while pending and `matching_requests <= 1` |
| `no_horizontal_overflow` | `widths: [int]` | `{by_width: {w: {scroll_width, client_width}}}` | scroll <= client + 1 at each width |
| `llm_judge` | `question`, `inputs: ["visible_message","screenshot"]` | `{visible_message, screenshot}` | Haiku verdict pass with confidence >= 0.7, else unverified |

Probe failure (element not found, timeout) -> oracle status `error` -> scenario `unverified`, never `failed`.

## 5. Phases and state

Order: `read_only` (parallel contexts allowed) -> `negative_mutation` -> `positive_mutation` -> `session_destructive` (each serial). Mutating scenarios begin and end with `snapshot_state`. After every mutating scenario `StateManager.compensate()` reverses any money that moved (intended or not) and records it.
