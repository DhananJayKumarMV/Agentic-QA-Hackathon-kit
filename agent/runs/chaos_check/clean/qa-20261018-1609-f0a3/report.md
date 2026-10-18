# QA Audit Report: http://localhost:5173

| | |
|---|---|
| Run | `qa-20261018-1609-f0a3` |
| Target | http://localhost:5173 |
| PRD sha256 | `5bf675015f276b04` |
| Started | 2026-10-18T21:09:44.509657+00:00 |
| Finished | 2026-10-18T21:12:45.613354+00:00 (181 s) |
| Generated | 2026-10-18T16:12:47-05:00 |

## Executive summary

The QA audit identified 49 total requirements across history filtering functionality, with 27 passed and 20 unverified. Two major defects were found with no critical or minor issues. Both defects involve date range filtering: one shows that pending transactions on the end date boundary are being incorrectly omitted from results, and the other reveals that when start and end dates are identical, matching transactions fail to appear despite inclusive boundary requirements. The audit consumed 0.32 USD and completed in 181 seconds.

## Scorecard

| Status | Requirements |
|---|---:|
| passed | 27 |
| failed | 2 |
| unverified | 20 |
| **total** | **49** |

Pass rate on verified requirements: 93%. Verified (passed or failed): 59%.
Defects: 2 (0 critical, 2 major, 0 minor).
Estimated cost: $0.3222 ($0.0066 per requirement).

### By area

| Area | Passed | Failed | Unverified |
|---|---:|---:|---:|
| access | 5 | 0 | 1 |
| dashboard | 6 | 0 | 0 |
| history | 3 | 2 | 2 |
| profile | 2 | 0 | 0 |
| scope | 6 | 0 | 0 |
| seed | 3 | 0 | 0 |
| transfers | 0 | 0 | 14 |
| ui | 2 | 0 | 3 |

### Scenarios by polarity

| Polarity | Passed | Failed | Unverified |
|---|---:|---:|---:|
| positive | 21 | 0 | 2 |
| negative | 9 | 0 | 0 |
| edge | 6 | 3 | 0 |

## Coverage matrix

Each cell is the worst status among scenarios of that polarity ("-" = no scenario).

| Requirement | Area | Pri | Positive | Negative | Edge | Result | Defect |
|---|---|---|---|---|---|---|---|
| REQ-ACC-001 | access | P1 | unverified | - | - | **unverified** |  |
| REQ-ACC-002 | access | P1 | passed | - | - | **passed** |  |
| REQ-ACC-003 | access | P1 | passed | passed | - | **passed** |  |
| REQ-ACC-004 | access | P0 | - | passed | - | **passed** |  |
| REQ-ACC-005 | access | P1 | passed | - | - | **passed** |  |
| REQ-ACC-006 | access | P0 | - | passed | - | **passed** |  |
| REQ-DSH-001 | dashboard | P1 | passed | - | - | **passed** |  |
| REQ-DSH-002 | dashboard | P1 | passed | - | - | **passed** |  |
| REQ-DSH-003 | dashboard | P1 | passed | - | passed | **passed** |  |
| REQ-DSH-004 | dashboard | P0 | - | passed | passed | **passed** |  |
| REQ-DSH-005 | dashboard | P1 | passed | - | passed | **passed** |  |
| REQ-DSH-006 | dashboard | P0 | passed | - | - | **passed** |  |
| REQ-TRF-001 | transfers | P0 | unverified | - | - | **unverified** |  |
| REQ-TRF-002 | transfers | P1 | unverified | - | - | **unverified** |  |
| REQ-TRF-003 | transfers | P0 | - | unverified | - | **unverified** |  |
| REQ-TRF-004 | transfers | P0 | - | unverified | - | **unverified** |  |
| REQ-TRF-005 | transfers | P0 | - | unverified | unverified | **unverified** |  |
| REQ-TRF-006 | transfers | P0 | - | unverified | - | **unverified** |  |
| REQ-TRF-007 | transfers | P0 | - | unverified | unverified | **unverified** |  |
| REQ-TRF-008 | transfers | P0 | - | unverified | - | **unverified** |  |
| REQ-TRF-009 | transfers | P0 | unverified | - | unverified | **unverified** |  |
| REQ-TRF-010 | transfers | P0 | unverified | - | unverified | **unverified** |  |
| REQ-TRF-011 | transfers | P1 | unverified | - | - | **unverified** |  |
| REQ-TRF-012 | transfers | P0 | unverified | - | - | **unverified** |  |
| REQ-TRF-013 | transfers | P0 | unverified | unverified | - | **unverified** |  |
| REQ-TRF-014 | transfers | P0 | unverified | - | - | **unverified** |  |
| REQ-HIS-001 | history | P1 | unverified | - | - | **unverified** |  |
| REQ-HIS-002 | history | P1 | unverified | - | - | **unverified** |  |
| REQ-HIS-003 | history | P2 | passed | - | - | **passed** |  |
| REQ-HIS-004 | history | P1 | passed | passed | failed | **failed** | DEF-001 |
| REQ-HIS-005 | history | P1 | - | passed | failed | **failed** | DEF-002 |
| REQ-HIS-006 | history | P1 | - | - | passed | **passed** |  |
| REQ-HIS-007 | history | P1 | passed | - | - | **passed** |  |
| REQ-PRO-001 | profile | P1 | passed | - | - | **passed** |  |
| REQ-PRO-002 | profile | P0 | - | passed | - | **passed** |  |
| REQ-GUI-001 | ui | P2 | passed | - | passed | **passed** |  |
| REQ-GUI-002 | ui | P2 | unverified | - | - | **unverified** |  |
| REQ-GUI-003 | ui | P1 | - | - | unverified | **unverified** |  |
| REQ-GUI-004 | ui | P1 | passed | - | - | **passed** |  |
| REQ-GUI-005 | ui | P1 | - | unverified | unverified | **unverified** |  |
| REQ-SED-001 | seed | P1 | passed | - | - | **passed** |  |
| REQ-SED-002 | seed | P1 | passed | - | passed | **passed** |  |
| REQ-SED-003 | seed | P2 | passed | - | - | **passed** |  |
| REQ-SCP-001 | scope | P1 | - | - | passed | **passed** |  |
| REQ-SCP-002 | scope | P2 | - | passed | - | **passed** |  |
| REQ-SCP-003 | scope | P2 | - | passed | - | **passed** |  |
| REQ-SCP-004 | scope | P2 | - | passed | - | **passed** |  |
| REQ-SCP-005 | scope | P1 | - | passed | passed | **passed** |  |
| REQ-SCP-006 | scope | P2 | - | passed | - | **passed** |  |

## Defects

| ID | Severity | Requirement(s) | Requirement not met | What the agent observed |
|---|---|---|---|---|
| [DEF-001](#def-001) | major | REQ-HIS-004 | History can be filtered by account, transaction type, status and date range, and every displayed row satisfies all selected filters. | Filtered results showed 7 rows and omitted transaction ...0211 (withdrawal, pending, 125.00, 2025-08-08T18:25:00Z), which the API's full record set places in range. |
| [DEF-002](#def-002) | major | REQ-HIS-005 | Start and end dates are inclusive: transactions on either boundary date that match other filters are included. | GET /api/transactions?start_date=2025-09-03&end_date=2025-09-03 returned an empty list and the UI showed 0 rows, although transaction ...212 (deposit, failed, occurred_at 2025-09-03T14:50:00Z) exists. |

### DEF-001

**Requirement not met:** History can be filtered by account, transaction type, status and date range, and every displayed row satisfies all selected filters.

- Severity: **major** (requirement priority P1), confidence 0.95
- Requirement(s): REQ-HIS-004; scenario(s): SCN-HIS-004-P1, SCN-HIS-004-P2, SCN-HIS-004-P3, SCN-HIS-004-E1, SCN-HIS-004-N1, SCN-HIS-007-P1

> The user can filter history by account, transaction type, status, and date range. When filters are applied, every displayed row must satisfy all selected filters.

**Expected:** With account=checking and date range 2025-01-03 to 2025-08-08 (inclusive), all matching transactions are shown, including pending withdrawal 211 dated 2025-08-08.

**Observed:** Filtered results showed 7 rows and omitted transaction ...0211 (withdrawal, pending, 125.00, 2025-08-08T18:25:00Z), which the API's full record set places in range.

**Steps to reproduce**

1. Precondition: logged in
2. Open the Transactions page: http://localhost:5173/transactions
3. Select the checking account (value: "Everyday Checking")
4. Enter the earliest date as start date (value: "2025-01-03")
5. Enter the latest date as end date (value: "2025-08-08")
6. Click Apply filters

**Failed checks**

| Scenario | Check | Layer | Expected | Observed |
|---|---|---|---|---|
| SCN-HIS-004-E1 | rows_equal_api_set | both | 8 transaction(s) matching account=Everyday Checking, start_date=2025-01-03, end_date=2025-08-08 (from the API, dates inclusive in America/Los_Angeles) | UI shows 7 row(s); missing 1 expected: 00000000-0000-0000-0000-000000000211 (withdrawal, pending, 2025-08-08) |

**Evidence**

- ![SCN-HIS-004-E1: final screen (attempt 1)](evidence/SCN-HIS-004-E1__a1__final.png) [SCN-HIS-004-E1: final screen (attempt 1)](evidence/SCN-HIS-004-E1__a1__final.png)
- network: [SCN-HIS-004-E1: API calls recorded (attempt 1)](evidence/SCN-HIS-004-E1/a1_network.json)

### DEF-002

**Requirement not met:** Start and end dates are inclusive: transactions on either boundary date that match other filters are included.

- Severity: **major** (requirement priority P1), confidence 0.95
- Requirement(s): REQ-HIS-005; scenario(s): SCN-HIS-005-E1, SCN-HIS-005-E2, SCN-HIS-004-N1

> The start and end dates are inclusive. A transaction occurring on either boundary date must be included when it matches the other filters.

**Expected:**

- `SCN-HIS-005-E1`: With start and end date both 2025-09-03, the transaction dated 2025-09-03 (id ...212) must be included, since the start and end dates are inclusive.
- `SCN-HIS-005-E2`: With start and end date both 2025-09-03, the transaction dated 2025-09-03 (id ...212) must be included, since the start and end dates are inclusive.

**Observed:**

- `SCN-HIS-005-E1`: GET /api/transactions?start_date=2025-09-03&end_date=2025-09-03 returned an empty list and the UI showed 0 rows, although transaction ...212 (deposit, failed, occurred_at 2025-09-03T14:50:00Z) exists.
- `SCN-HIS-005-E2`: GET /api/transactions?start_date=2025-09-03&end_date=2025-09-03 returned an empty list and the UI showed 0 rows, although transaction ...212 (deposit, failed, occurred_at 2025-09-03T14:50:00Z) exists.

**Steps to reproduce**

1. Precondition: logged in
2. Open the Transactions page: http://localhost:5173/transactions
3. Enter a date with a known transaction as start date (value: "2025-09-03")
4. Enter the same date as end date (value: "2025-09-03")
5. Click Apply filters

**Failed checks**

| Scenario | Check | Layer | Expected | Observed |
|---|---|---|---|---|
| SCN-HIS-005-E1 | rows_equal_api_set | both | 1 transaction(s) matching start_date=2025-09-03, end_date=2025-09-03 (from the API, dates inclusive in America/Los_Angeles) | UI shows 0 row(s); missing 1 expected: 00000000-0000-0000-0000-000000000212 (deposit, failed, 2025-09-03) |
| SCN-HIS-005-E2 | rows_equal_api_set | both | 12 transaction(s) matching start_date=2025-01-03, end_date=2025-09-03 (from the API, dates inclusive in America/Los_Angeles) | UI shows 11 row(s); missing 1 expected: 00000000-0000-0000-0000-000000000212 (deposit, failed, 2025-09-03) |

**Evidence**

- ![SCN-HIS-005-E1: final screen (attempt 1)](evidence/SCN-HIS-005-E1__a1__final.png) [SCN-HIS-005-E1: final screen (attempt 1)](evidence/SCN-HIS-005-E1__a1__final.png)
- network: [SCN-HIS-005-E1: API calls recorded (attempt 1)](evidence/SCN-HIS-005-E1/a1_network.json)
- ![SCN-HIS-005-E2: final screen (attempt 1)](evidence/SCN-HIS-005-E2__a1__final.png) [SCN-HIS-005-E2: final screen (attempt 1)](evidence/SCN-HIS-005-E2__a1__final.png)
- network: [SCN-HIS-005-E2: API calls recorded (attempt 1)](evidence/SCN-HIS-005-E2/a1_network.json)


## Unverified requirements

An unverified requirement is neither a pass nor a defect: the agent could not collect conclusive evidence and says so instead of guessing.

| Requirement | Statement | Why unverified | Scenarios run | Evidence |
|---|---|---|---|---|
| REQ-ACC-001 | The landing page shows a clearly labeled Enter Demo action that opens the demo dashboard without requiring a password or real identity information. | The agent could not find the UI element 'landing.entry' (role=button[name="Enter Demo"]) on the page, so the check was inconclusive rather than failed. The screenshot shows the page at that moment; a human should confirm whether the control is missing (a defect) or just labelled differently.<br>Technical reason: SCN-ACC-001-P1: element_visible could not verify: probe failed: ResolveError: could not resolve element 'landing.entry' (role=button[name="Enter Demo"]) | SCN-ACC-001-P1 (positive): unverified | [screenshot](evidence/SCN-ACC-001-P1__a1__final.png), [network](evidence/SCN-ACC-001-P1/a1_network.json) |
| REQ-TRF-001 | The user can transfer funds between the checking and savings accounts of the demo customer. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-001-P1: not executed; SCN-TRF-001-P2: not executed | SCN-TRF-001-P1 (positive): not run<br>SCN-TRF-001-P2 (positive): not run | - |
| REQ-TRF-002 | The transfer form includes source account, destination account, amount, and an optional memo. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-001-P1: not executed; SCN-TRF-002-P1: not executed | SCN-TRF-001-P1 (positive): not run<br>SCN-TRF-002-P1 (positive): not run | - |
| REQ-TRF-003 | A transfer with the same source and destination account is rejected with a clear explanation. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-003-N1: not executed | SCN-TRF-003-N1 (negative): not run | - |
| REQ-TRF-004 | A rejected same-account transfer does not alter balances or create a transaction. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-003-N1: not executed; SCN-TRF-007-N1: not executed | SCN-TRF-003-N1 (negative): not run<br>SCN-TRF-007-N1 (negative): not run | - |
| REQ-TRF-005 | Blank, zero, negative, non-numeric and over-precision (more than two decimals) amounts are each rejected with a clear validation message. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-005-N1: not executed; SCN-TRF-005-N2: not executed; SCN-TRF-005-N3: not executed; SCN-TRF-005-N4: not executed; SCN-TRF-005-N5: not executed; SCN-TRF-005-E1: not executed | SCN-TRF-005-N1 (negative): not run<br>SCN-TRF-005-N2 (negative): not run<br>SCN-TRF-005-N3 (negative): not run<br>SCN-TRF-005-N4 (negative): not run<br>SCN-TRF-005-N5 (negative): not run<br>SCN-TRF-005-E1 (edge): not run | - |
| REQ-TRF-006 | Rejected invalid amount input does not create a transaction or change balances. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-005-N1: not executed; SCN-TRF-005-N2: not executed; SCN-TRF-005-N3: not executed; SCN-TRF-005-N4: not executed; SCN-TRF-005-N5: not executed | SCN-TRF-005-N1 (negative): not run<br>SCN-TRF-005-N2 (negative): not run<br>SCN-TRF-005-N3 (negative): not run<br>SCN-TRF-005-N4 (negative): not run<br>SCN-TRF-005-N5 (negative): not run | - |
| REQ-TRF-007 | A transfer exceeding the source account's available balance is rejected. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-007-N1: not executed; SCN-TRF-007-E1: not executed | SCN-TRF-007-N1 (negative): not run<br>SCN-TRF-007-E1 (edge): not run | - |
| REQ-TRF-008 | An insufficient-funds transfer leaves both balances unchanged and no completed transfer appears in history. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-007-N1: not executed | SCN-TRF-007-N1 (negative): not run | - |
| REQ-TRF-009 | A valid transfer is atomic: the source decreases by the amount and the destination increases by exactly the same amount. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-001-P1: not executed; SCN-TRF-001-P2: not executed; SCN-TRF-007-E1: not executed; SCN-TRF-005-E1: not executed | SCN-TRF-001-P1 (positive): not run<br>SCN-TRF-001-P2 (positive): not run<br>SCN-TRF-007-E1 (edge): not run<br>SCN-TRF-005-E1 (edge): not run | - |
| REQ-TRF-010 | A successful transfer creates exactly one completed transfer record that appears in both the source and destination account histories. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-001-P1: not executed; SCN-TRF-001-P2: not executed; SCN-GUI-003-E2: not executed | SCN-TRF-001-P1 (positive): not run<br>SCN-TRF-001-P2 (positive): not run<br>SCN-GUI-003-E2 (edge): not run | - |
| REQ-TRF-011 | The transfer record shows amount, date and time, source, destination, status, and memo if supplied. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-001-P1: not executed | SCN-TRF-001-P1 (positive): not run | - |
| REQ-TRF-012 | The UI shows a success state only after the transfer has been committed. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-001-P1: not executed; SCN-TRF-014-P1: not executed; SCN-TRF-012-P1: not executed | SCN-TRF-001-P1 (positive): not run<br>SCN-TRF-014-P1 (positive): not run<br>SCN-TRF-012-P1 (positive): not run | - |
| REQ-TRF-013 | If a transfer is rejected or fails, the UI explains that it did not complete and does not show a success state. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-003-N1: not executed; SCN-TRF-005-N1: not executed; SCN-TRF-005-N2: not executed; SCN-TRF-005-N3: not executed; SCN-TRF-005-N4: not executed; SCN-TRF-005-N5: not executed; SCN-TRF-007-N1: not executed; SCN-TRF-012-P1: not executed | SCN-TRF-003-N1 (negative): not run<br>SCN-TRF-005-N1 (negative): not run<br>SCN-TRF-005-N2 (negative): not run<br>SCN-TRF-005-N3 (negative): not run<br>SCN-TRF-005-N4 (negative): not run<br>SCN-TRF-005-N5 (negative): not run<br>SCN-TRF-007-N1 (negative): not run<br>SCN-TRF-012-P1 (positive): not run | - |
| REQ-TRF-014 | After a successful transfer and a page refresh, the updated balances and the transaction record persist. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-TRF-014-P1: not executed | SCN-TRF-014-P1 (positive): not run | - |
| REQ-HIS-001 | The user can view a combined transaction history and an account-specific history. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-HIS-001-P1: triage: not confirmed; The PRD quote requires each row to show an amount, but it does not say what amount a failed or pending transaction must display. The API's account_deltas show 0.00 for the failed deposit, so 'No change: $0.00' is a plausible, deliberate way to show the net effect on the balanc | SCN-HIS-001-P1 (positive): unverified<br>SCN-HIS-001-P2 (positive): passed<br>SCN-HIS-001-P3 (positive): passed | [screenshot](evidence/SCN-HIS-001-P1__a1__final.png), [network](evidence/SCN-HIS-001-P1/a1_network.json), [network](evidence/SCN-HIS-001-P2/a1_network.json), [network](evidence/SCN-HIS-001-P3/a1_network.json) |
| REQ-HIS-002 | Each history row shows date and time, description or memo, transaction type, amount, status, and relevant account. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-HIS-001-P1: triage: not confirmed; The PRD quote requires each row to show an amount, but it does not say what amount a failed or pending transaction must display. The API's account_deltas show 0.00 for the failed deposit, so 'No change: $0.00' is a plausible, deliberate way to show the net effect on the balanc | SCN-HIS-001-P1 (positive): unverified | [screenshot](evidence/SCN-HIS-001-P1__a1__final.png), [network](evidence/SCN-HIS-001-P1/a1_network.json) |
| REQ-GUI-002 | Interactive controls have readable labels and communicate loading, success, validation and failure states. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-GUI-002-P1: not executed | SCN-GUI-002-P1 (positive): not run | - |
| REQ-GUI-003 | A submitted action does not silently disappear: the UI shows a loading state or prevents duplicate submission while waiting. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-GUI-003-E1: not executed; SCN-GUI-003-E2: not executed | SCN-GUI-003-E1 (edge): not run<br>SCN-GUI-003-E2 (edge): not run | - |
| REQ-GUI-005 | After a recoverable error the user can retry or return to the dashboard. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-GUI-005-N1: not executed; SCN-GUI-005-E1: not executed | SCN-GUI-005-N1 (negative): not run<br>SCN-GUI-005-E1 (edge): not run | - |

## All requirement results

| Requirement | Status | Statement | Reason |
|---|---|---|---|
| REQ-ACC-001 | unverified | The landing page shows a clearly labeled Enter Demo action that opens the demo dashboard without requiring a password or real identity information. | SCN-ACC-001-P1: element_visible could not verify: probe failed: ResolveError: could not resolve element 'landing.entry' (role=button[name="Enter Demo"]) |
| REQ-ACC-002 | passed | The header identifies the active demo customer and provides a Log out action. | all 1 scenario(s) passed: SCN-ACC-002-P1 |
| REQ-ACC-003 | passed | Logging out returns the user to the landing page. | all 2 scenario(s) passed: SCN-ACC-003-P1, SCN-ACC-003-N1 |
| REQ-ACC-004 | passed | After logout, direct navigation to protected dashboard views returns the user to the landing page or an access-required state and does not show protected content. | all 3 scenario(s) passed: SCN-ACC-003-N1, SCN-ACC-004-N1, SCN-ACC-004-N2 |
| REQ-ACC-005 | passed | Refreshing a page during a valid demo session preserves the session. | all 1 scenario(s) passed: SCN-ACC-005-P1 |
| REQ-ACC-006 | passed | A logged-out user does not regain access to a protected page by refreshing it. | all 1 scenario(s) passed: SCN-ACC-006-N1 |
| REQ-DSH-001 | passed | The dashboard shows the customer's checking and savings accounts. | all 1 scenario(s) passed: SCN-DSH-001-P1 |
| REQ-DSH-002 | passed | Each account card displays a readable name, a masked account identifier, account type, and current available balance in USD. | all 1 scenario(s) passed: SCN-DSH-001-P1 |
| REQ-DSH-003 | passed | Balances are displayed with exactly two decimal places. | all 2 scenario(s) passed: SCN-DSH-003-P1, SCN-DSH-004-E1 |
| REQ-DSH-004 | passed | The UI never exposes a full account identifier. | all 2 scenario(s) passed: SCN-DSH-004-N1, SCN-DSH-004-E1 |
| REQ-DSH-005 | passed | Selecting an account opens an account detail view showing its current balance and related transaction history. | all 2 scenario(s) passed: SCN-DSH-005-P1, SCN-DSH-005-E1 |
| REQ-DSH-006 | passed | Balances on the dashboard and account detail view agree with each other and with the latest successful transactions. | all 2 scenario(s) passed: SCN-DSH-003-P1, SCN-DSH-005-P1 |
| REQ-TRF-001 | unverified | The user can transfer funds between the checking and savings accounts of the demo customer. | SCN-TRF-001-P1: not executed; SCN-TRF-001-P2: not executed |
| REQ-TRF-002 | unverified | The transfer form includes source account, destination account, amount, and an optional memo. | SCN-TRF-001-P1: not executed; SCN-TRF-002-P1: not executed |
| REQ-TRF-003 | unverified | A transfer with the same source and destination account is rejected with a clear explanation. | SCN-TRF-003-N1: not executed |
| REQ-TRF-004 | unverified | A rejected same-account transfer does not alter balances or create a transaction. | SCN-TRF-003-N1: not executed; SCN-TRF-007-N1: not executed |
| REQ-TRF-005 | unverified | Blank, zero, negative, non-numeric and over-precision (more than two decimals) amounts are each rejected with a clear validation message. | SCN-TRF-005-N1: not executed; SCN-TRF-005-N2: not executed; SCN-TRF-005-N3: not executed; SCN-TRF-005-N4: not executed; SCN-TRF-005-N5: not executed; SCN-TRF-005-E1: not executed |
| REQ-TRF-006 | unverified | Rejected invalid amount input does not create a transaction or change balances. | SCN-TRF-005-N1: not executed; SCN-TRF-005-N2: not executed; SCN-TRF-005-N3: not executed; SCN-TRF-005-N4: not executed; SCN-TRF-005-N5: not executed |
| REQ-TRF-007 | unverified | A transfer exceeding the source account's available balance is rejected. | SCN-TRF-007-N1: not executed; SCN-TRF-007-E1: not executed |
| REQ-TRF-008 | unverified | An insufficient-funds transfer leaves both balances unchanged and no completed transfer appears in history. | SCN-TRF-007-N1: not executed |
| REQ-TRF-009 | unverified | A valid transfer is atomic: the source decreases by the amount and the destination increases by exactly the same amount. | SCN-TRF-001-P1: not executed; SCN-TRF-001-P2: not executed; SCN-TRF-007-E1: not executed; SCN-TRF-005-E1: not executed |
| REQ-TRF-010 | unverified | A successful transfer creates exactly one completed transfer record that appears in both the source and destination account histories. | SCN-TRF-001-P1: not executed; SCN-TRF-001-P2: not executed; SCN-GUI-003-E2: not executed |
| REQ-TRF-011 | unverified | The transfer record shows amount, date and time, source, destination, status, and memo if supplied. | SCN-TRF-001-P1: not executed |
| REQ-TRF-012 | unverified | The UI shows a success state only after the transfer has been committed. | SCN-TRF-001-P1: not executed; SCN-TRF-014-P1: not executed; SCN-TRF-012-P1: not executed |
| REQ-TRF-013 | unverified | If a transfer is rejected or fails, the UI explains that it did not complete and does not show a success state. | SCN-TRF-003-N1: not executed; SCN-TRF-005-N1: not executed; SCN-TRF-005-N2: not executed; SCN-TRF-005-N3: not executed; SCN-TRF-005-N4: not executed; SCN-TRF-005-N5: not executed; SCN-TRF-007-N1: not executed; SCN-TRF-012-P1: not executed |
| REQ-TRF-014 | unverified | After a successful transfer and a page refresh, the updated balances and the transaction record persist. | SCN-TRF-014-P1: not executed |
| REQ-HIS-001 | unverified | The user can view a combined transaction history and an account-specific history. | SCN-HIS-001-P1: triage: not confirmed; The PRD quote requires each row to show an amount, but it does not say what amount a failed or pending transaction must display. The API's account_deltas show 0.00 for the failed deposit, so 'No change: $0.00' is a plausible, deliberate way to show the net effect on the balanc |
| REQ-HIS-002 | unverified | Each history row shows date and time, description or memo, transaction type, amount, status, and relevant account. | SCN-HIS-001-P1: triage: not confirmed; The PRD quote requires each row to show an amount, but it does not say what amount a failed or pending transaction must display. The API's account_deltas show 0.00 for the failed deposit, so 'No change: $0.00' is a plausible, deliberate way to show the net effect on the balanc |
| REQ-HIS-003 | passed | Amount direction is clear for deposits, withdrawals and transfers. | all 1 scenario(s) passed: SCN-HIS-003-P1 |
| REQ-HIS-004 | failed | History can be filtered by account, transaction type, status and date range, and every displayed row satisfies all selected filters. | SCN-HIS-004-E1 (edge) failed: rows_equal_api_set |
| REQ-HIS-005 | failed | Start and end dates are inclusive: transactions on either boundary date that match other filters are included. | SCN-HIS-005-E1 (edge) failed: rows_equal_api_set; SCN-HIS-005-E2 (edge) failed: rows_equal_api_set |
| REQ-HIS-006 | passed | An empty filter result shows a clear empty state, not an error or unrelated transactions. | all 1 scenario(s) passed: SCN-HIS-006-E1 |
| REQ-HIS-007 | passed | Clearing filters restores the unfiltered history. | all 1 scenario(s) passed: SCN-HIS-007-P1 |
| REQ-PRO-001 | passed | The profile view displays only synthetic demo-customer details such as display name and a masked email address. | all 1 scenario(s) passed: SCN-PRO-001-P1 |
| REQ-PRO-002 | passed | The profile view does not reveal passwords, authentication tokens, full account identifiers, or another user's data. | all 2 scenario(s) passed: SCN-DSH-004-N1, SCN-PRO-002-N1 |
| REQ-GUI-001 | passed | The app works in a current desktop browser at 1280x800 and remains usable at narrower widths. | all 2 scenario(s) passed: SCN-GUI-001-P1, SCN-GUI-001-E1 |
| REQ-GUI-002 | unverified | Interactive controls have readable labels and communicate loading, success, validation and failure states. | SCN-GUI-002-P1: not executed |
| REQ-GUI-003 | unverified | A submitted action does not silently disappear: the UI shows a loading state or prevents duplicate submission while waiting. | SCN-GUI-003-E1: not executed; SCN-GUI-003-E2: not executed |
| REQ-GUI-004 | passed | Navigation links and buttons lead to the labeled destination or perform the labeled action. | all 3 scenario(s) passed: SCN-GUI-004-P1, SCN-GUI-004-P2, SCN-GUI-004-P3 |
| REQ-GUI-005 | unverified | After a recoverable error the user can retry or return to the dashboard. | SCN-GUI-005-N1: not executed; SCN-GUI-005-E1: not executed |
| REQ-SED-001 | passed | The demo begins with one customer, one checking account and one savings account. | all 1 scenario(s) passed: SCN-SED-001-P1 |
| REQ-SED-002 | passed | Seed data includes enough historical transactions across multiple dates to exercise account, type, status and date filters. | all 2 scenario(s) passed: SCN-SED-002-P1, SCN-SED-002-E1 |
| REQ-SED-003 | passed | Seed data includes at least one transfer between the checking and savings accounts. | all 1 scenario(s) passed: SCN-SED-003-P1 |
| REQ-SCP-001 | passed | The app never initiates real payments or connects to an external bank. | all 1 scenario(s) passed: SCN-SCP-001-E1 |
| REQ-SCP-002 | passed | The app offers no signup feature. | all 2 scenario(s) passed: SCN-SCP-002-N1, SCN-SCP-003-N1 |
| REQ-SCP-003 | passed | The app offers no external payment, bill-pay, loan or card features. | all 1 scenario(s) passed: SCN-SCP-003-N1 |
| REQ-SCP-004 | passed | The app offers no multi-user features, joint accounts or multiple customers. | all 1 scenario(s) passed: SCN-SCP-003-N1 |
| REQ-SCP-005 | passed | The app offers no real bank integrations or deposits from external sources. | all 2 scenario(s) passed: SCN-SCP-003-N1, SCN-SCP-001-E1 |
| REQ-SCP-006 | passed | The app offers no password reset, multi-factor authentication or production-grade identity verification. | all 2 scenario(s) passed: SCN-SCP-002-N1, SCN-SCP-003-N1 |

## State ledger and compensations

Every mutation the agent made is tagged and recorded; unintended ones (accepted by a bug) are reversed by a compensating transfer.

No state mutations were recorded.

No compensations were needed.

## Cost

Total estimated model cost: **$0.3222** of a $1.0000 budget (32.2%).

### Per agent

| Agent | Model(s) | Calls | Input tok | Output tok | Cache read | Cache write | USD | Share | Artifact-cache hits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| executor_heal | claude-haiku-4-5 | 20 | 8,600 | 348 | 0 | 0 | $0.0103 | 3% | 12 |
| explorer | claude-haiku-4-5 | 6 | 0 | 0 | 0 | 0 | $0.0000 | 0% | 6 |
| reporter | claude-haiku-4-5 | 1 | 615 | 112 | 0 | 0 | $0.0012 | 0% | 0 |
| spec_analyst | claude-sonnet-5-5 | 1 | 0 | 0 | 0 | 0 | $0.0000 | 0% | 1 |
| test_planner | claude-sonnet-5-5 | 4 | 843 | 23,160 | 48,315 | 16,105 | $0.2832 | 88% | 0 |
| verifier | claude-haiku-4-5, claude-sonnet-5-5 | 5 | 9,126 | 1,124 | 4,746 | 0 | $0.0274 | 9% | 0 |
| **total** | | **37** | **19,184** | **24,744** | **53,061** | **16,105** | **$0.3222** | **100%** | **19** |

### Per model

| Model | Calls | Input tok | Output tok | Cache read | Cache write | USD | Artifact-cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|
| claude-haiku-4-5 | 29 | 11,359 | 633 | 0 | 0 | $0.0145 | 18 |
| claude-sonnet-5-5 | 8 | 7,825 | 24,111 | 53,061 | 16,105 | $0.3076 | 1 |

**Cold vs warm:** this was a warm run: 19 of 37 model calls were served from the on-disk artifact cache at no cost. A cold run (empty cache) is estimated at **$0.4630** (each cache hit priced at the average of live calls for the same agent and purpose).
Prompt-cache reads made up 73% of input tokens.

## Environment

| Key | Value |
|---|---|
| target_url | http://localhost:5173 |
| api_base | http://localhost:8000 |
| timezone | America/Los_Angeles |
| models | reasoning: claude-sonnet-5-5, fast: claude-haiku-4-5 |
| python | 3.13.9 |
| platform | macOS-26.6.2-arm64-arm-64bit-Mach-O |
| read_only | yes |
| chaos | off |
| parallel | 3 |
| scenario_timeout_s | 90.0 |

## Resilience

Chaos mode was off for this run.
34 read-only scenarios ran in 3 parallel browser contexts.

| After scenario | What the agent saw and did |
|---|---|
| SCN-HIS-004-N1 | page left in an error state ("We couldn't load transactions Start date must not be after end date. Try again") → recovery: error state ("We couldn't load transactions Start date must not be after end date. Try again Back to dashboard"); clicked retry once → error state persisted; opened a new browser context |

## Limitations

- --read-only: 19 data-changing scenarios were not run; their requirements are reported as unverified.
- The agent repaired the browser state 1 time(s) between scenarios (see environment.resilience).
