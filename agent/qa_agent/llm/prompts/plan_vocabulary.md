# DSL vocabulary

## Step actions

`goto` (value = route, may contain `{{param}}`), `click` (target), `fill` (target, value), `select` (target,
value = option value or visible label, usually an account id param), `submit` (target = form control or submit button),
`reload`, `back`, `logout`, `enter_demo`, `set_viewport` (value = `"<width>x<height>"`), `wait_for` (target),
`snapshot_state` (added automatically around state-changing scenarios; do not write it), `api_get` (value = API path),
`api_post` (value = API path; avoid unless the requirement is about the API itself).

Preconditions: `logged_in` (default), `logged_out`, `fresh_context`.

## ParamBinding sources and query

Built-ins always available in `{{...}}`: `run_tag` (unique per run+scenario, put it in every memo),
`unique_amount` (small unique decimal string per scenario, e.g. "1.07").

| source | query | resolves to |
|---|---|---|
| `account` | `{"select": "type=<t>" \| "index=<n>" \| "not=<param>", "field": "id"\|"name"\|"masked_number"\|"type"}` | from live snapshot accounts |
| `balance` | `{"of": "<account param>", "plus": "<decimal>"}` (plus optional, may be negative) | decimal string, 2 dp |
| `transaction` | `{"pick": "earliest"\|"latest"\|"any", "where": {"type"?, "status"?, "account"?: "<param>"}, "field": "id"\|"local_date"\|"type"\|"status"\|"amount"}` | `local_date` is `YYYY-MM-DD` in the site's timezone |
| `date` | `{"from": "<param>", "offset_days": <int>}` | `YYYY-MM-DD` |
| `literal` | `{"value": "<string>"}` | as is |
| `derived` | `{"expr": "unique_amount" \| "run_tag"}` | built-ins |

Params are resolved in declaration order; a param may reference earlier params. An unresolvable param makes
the scenario unverified, so only reference things the SiteMap entities show exist (types, statuses, accounts).

## Oracles (type: args -> passes when)

- `url_matches`: `pattern` -> regex matches path+query.
- `element_visible` / `element_absent`: `target` -> visible / not visible.
- `text_matches` / `text_not_matches`: `pattern`, `target?`, `ignore_case?` -> regex on target text (else main region text).
- `money_format`: `target`, `pattern?` -> every text contains a 2-decimal currency amount.
- `masked_identifier`: `{}` -> no full account id/number appears in visible text.
- `balance_delta`: `deltas: {<account param>: "<signed decimal or {{param}}>"}` -> after-before equals expected per account.
- `no_state_change`: `{}` -> balances equal and no new transactions.
- `new_tx_count`: `n` -> exactly n new transactions.
- `tx_present_in_histories`: `accounts: [<account params>]` -> the new transaction is listed in each account's history.
- `rows_satisfy_filters`: `filters: {account?, type?, status?, start_date?, end_date?}` (values may be `{{param}}`) -> every displayed row satisfies every filter (dates inclusive).
- `rows_equal_api_set`: `filters` -> displayed row set equals the set computed from the API with those filters.
- `row_count_equals`: `n` -> displayed row count equals n.
- `empty_state_shown`: `{}` -> zero rows, a status/empty message shown, no error alert.
- `ui_matches_api`: `{}` -> displayed balances equal API balances.
- `api_status`: `method`, `path_glob`, `status` or `status_in` -> a matching request returned that status.
- `api_error_code_present`: `method?`, `path_glob` -> a matching response carries an error object/code.
- `no_success_ui`: `{}` -> no success alert/status is shown.
- `button_disabled_during`: `target`, `request_glob`, `delay_ms?` -> control disabled/busy while the request is pending and at most one request sent.
- `no_horizontal_overflow`: `widths: [int]` -> no horizontal scrolling at each width.
- `llm_judge`: `question`, `inputs: ["visible_message", "screenshot"]` -> a model judges the question; use sparingly.

Severity: `critical` for money/data integrity and security, `major` for functional, `minor` for cosmetic/format.
