# QA Audit Report: http://localhost:5173

| | |
|---|---|
| Run | `qa-20261018-1612-bd83` |
| Target | http://localhost:5173 |
| PRD sha256 | `5bf675015f276b04` |
| Started | 2026-10-18T21:12:48.859937+00:00 |
| Finished | 2026-10-18T21:17:18.916808+00:00 (270 s) |
| Generated | 2026-10-18T16:17:21-05:00 |

## Executive summary

QA audit completed with 23 passed requirements, 1 failed requirement, and 25 unverified requirements. One major defect was identified: the transaction date filtering endpoint returns an empty result set when querying with matching start and end dates of 2025-09-03, despite a transaction existing on that date in the system. The expected behavior is that transactions on boundary dates should be included when start and end dates are identical. All other verified requirements passed without critical or minor defects. The audit consumed 270 seconds of wall time at a cost of 0.03 USD.

## Scorecard

| Status | Requirements |
|---|---:|
| passed | 23 |
| failed | 1 |
| unverified | 25 |
| **total** | **49** |

Pass rate on verified requirements: 96%. Verified (passed or failed): 49%.
Defects: 1 (0 critical, 1 major, 0 minor).
Estimated cost: $0.0334 ($0.0007 per requirement).

### By area

| Area | Passed | Failed | Unverified |
|---|---:|---:|---:|
| access | 5 | 0 | 1 |
| dashboard | 4 | 0 | 2 |
| history | 2 | 1 | 4 |
| profile | 2 | 0 | 0 |
| scope | 6 | 0 | 0 |
| seed | 2 | 0 | 1 |
| transfers | 0 | 0 | 14 |
| ui | 2 | 0 | 3 |

### Scenarios by polarity

| Polarity | Passed | Failed | Unverified |
|---|---:|---:|---:|
| positive | 17 | 0 | 6 |
| negative | 9 | 0 | 0 |
| edge | 4 | 1 | 4 |

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
| REQ-DSH-003 | dashboard | P1 | unverified | - | passed | **unverified** |  |
| REQ-DSH-004 | dashboard | P0 | - | passed | passed | **passed** |  |
| REQ-DSH-005 | dashboard | P1 | passed | - | passed | **passed** |  |
| REQ-DSH-006 | dashboard | P0 | unverified | - | - | **unverified** |  |
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
| REQ-HIS-004 | history | P1 | unverified | passed | unverified | **unverified** |  |
| REQ-HIS-005 | history | P1 | - | passed | failed | **failed** | DEF-001 |
| REQ-HIS-006 | history | P1 | - | - | unverified | **unverified** |  |
| REQ-HIS-007 | history | P1 | passed | - | - | **passed** |  |
| REQ-PRO-001 | profile | P1 | passed | - | - | **passed** |  |
| REQ-PRO-002 | profile | P0 | - | passed | - | **passed** |  |
| REQ-GUI-001 | ui | P2 | passed | - | passed | **passed** |  |
| REQ-GUI-002 | ui | P2 | unverified | - | - | **unverified** |  |
| REQ-GUI-003 | ui | P1 | - | - | unverified | **unverified** |  |
| REQ-GUI-004 | ui | P1 | passed | - | - | **passed** |  |
| REQ-GUI-005 | ui | P1 | - | unverified | unverified | **unverified** |  |
| REQ-SED-001 | seed | P1 | passed | - | - | **passed** |  |
| REQ-SED-002 | seed | P1 | unverified | - | unverified | **unverified** |  |
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
| [DEF-001](#def-001) | major | REQ-HIS-005 | Start and end dates are inclusive: transactions on either boundary date that match other filters are included. | GET /api/transactions?start_date=2025-09-03&end_date=2025-09-03 returned an empty list and the UI showed 0 rows, although the unfiltered API has a 2025-09-03 deposit (...212). |

### DEF-001

**Requirement not met:** Start and end dates are inclusive: transactions on either boundary date that match other filters are included.

- Severity: **major** (requirement priority P1), confidence 0.95
- Requirement(s): REQ-HIS-005; scenario(s): SCN-HIS-005-E1, SCN-HIS-005-E2, SCN-HIS-004-N1

> The start and end dates are inclusive. A transaction occurring on either boundary date must be included when it matches the other filters.

**Expected:** With start and end date both 2025-09-03, the transaction dated 2025-09-03 (id ...212) is included because the dates are inclusive.

**Observed:** GET /api/transactions?start_date=2025-09-03&end_date=2025-09-03 returned an empty list and the UI showed 0 rows, although the unfiltered API has a 2025-09-03 deposit (...212).

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

**Evidence**

- ![SCN-HIS-005-E1: final screen (attempt 2)](evidence/SCN-HIS-005-E1__a2__final.png) [SCN-HIS-005-E1: final screen (attempt 2)](evidence/SCN-HIS-005-E1__a2__final.png)
- network: [SCN-HIS-005-E1: API calls recorded (attempt 2)](evidence/SCN-HIS-005-E1/a2_network.json)


## Unverified requirements

An unverified requirement is neither a pass nor a defect: the agent could not collect conclusive evidence and says so instead of guessing.

| Requirement | Statement | Why unverified | Scenarios run | Evidence |
|---|---|---|---|---|
| REQ-ACC-001 | The landing page shows a clearly labeled Enter Demo action that opens the demo dashboard without requiring a password or real identity information. | The agent could not find the UI element 'landing.entry' (role=button[name="Enter Demo"]) on the page, so the check was inconclusive rather than failed. The screenshot shows the page at that moment; a human should confirm whether the control is missing (a defect) or just labelled differently.<br>Technical reason: SCN-ACC-001-P1: element_visible could not verify: probe failed: ResolveError: could not resolve element 'landing.entry' (role=button[name="Enter Demo"]) | SCN-ACC-001-P1 (positive): unverified | [screenshot](evidence/SCN-ACC-001-P1__a2__final.png), [network](evidence/SCN-ACC-001-P1/a2_network.json) |
| REQ-DSH-003 | Balances are displayed with exactly two decimal places. | The agent could not find the UI element 'dashboard.account_balance' (test_id=account-balance) on the page, so the check was inconclusive rather than failed. The screenshot shows the page at that moment; a human should confirm whether the control is missing (a defect) or just labelled differently.<br>Technical reason: SCN-DSH-003-P1: aborted: step 1 (wait_for: Wait for balances to display) failed: StepFailed: timed out waiting for dashboard.account_balance: could not resolve element 'dashboard.account_balance' (test_id=account-balance) | SCN-DSH-003-P1 (positive): unverified<br>SCN-DSH-004-E1 (edge): passed | [screenshot](evidence/SCN-DSH-003-P1__a2__final.png), [network](evidence/SCN-DSH-003-P1/a2_network.json), [network](evidence/SCN-DSH-004-E1/a1_network.json) |
| REQ-DSH-006 | Balances on the dashboard and account detail view agree with each other and with the latest successful transactions. | The agent could not find the UI element 'dashboard.account_balance' (test_id=account-balance) on the page, so the check was inconclusive rather than failed. The screenshot shows the page at that moment; a human should confirm whether the control is missing (a defect) or just labelled differently.<br>Technical reason: SCN-DSH-003-P1: aborted: step 1 (wait_for: Wait for balances to display) failed: StepFailed: timed out waiting for dashboard.account_balance: could not resolve element 'dashboard.account_balance' (test_id=account-balance) | SCN-DSH-003-P1 (positive): unverified<br>SCN-DSH-005-P1 (positive): passed | [screenshot](evidence/SCN-DSH-003-P1__a2__final.png), [network](evidence/SCN-DSH-003-P1/a2_network.json), [network](evidence/SCN-DSH-005-P1/a2_network.json) |
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
| REQ-HIS-001 | The user can view a combined transaction history and an account-specific history. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-HIS-001-P1: inconclusive under chaos mode: 1 injected HTTP 503(s) during this attempt (GET http://localhost:8000/health); underlying result was 'failed'. rows_equal_api_set: expected 12 transaction(s) matching no filters (from the API, dates inclusive in America/Los_Angeles), each row showing its API amount; ob | SCN-HIS-001-P1 (positive): unverified<br>SCN-HIS-001-P2 (positive): passed<br>SCN-HIS-001-P3 (positive): passed | [screenshot](evidence/SCN-HIS-001-P1__a2__final.png), [network](evidence/SCN-HIS-001-P1/a2_network.json), [network](evidence/SCN-HIS-001-P2/a2_network.json), [network](evidence/SCN-HIS-001-P3/a1_network.json) |
| REQ-HIS-002 | Each history row shows date and time, description or memo, transaction type, amount, status, and relevant account. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-HIS-001-P1: inconclusive under chaos mode: 1 injected HTTP 503(s) during this attempt (GET http://localhost:8000/health); underlying result was 'failed'. rows_equal_api_set: expected 12 transaction(s) matching no filters (from the API, dates inclusive in America/Los_Angeles), each row showing its API amount; ob | SCN-HIS-001-P1 (positive): unverified | [screenshot](evidence/SCN-HIS-001-P1__a2__final.png), [network](evidence/SCN-HIS-001-P1/a2_network.json) |
| REQ-HIS-004 | History can be filtered by account, transaction type, status and date range, and every displayed row satisfies all selected filters. | The agent could not find the UI element 'transactions.filter_account' (role=combobox[name="Account"] \| label="Account" \| css=#filter-account) on the page, so the check was inconclusive rather than failed. The screenshot shows the page at that moment; a human should confirm whether the control is missing (a defect) or just labelled differently.<br>Technical reason: SCN-HIS-004-P1: aborted: step 1 (select: Select the checking account in the Account filter) failed: ResolveError: could not resolve element 'transactions.filter_account' (role=combobox[name="Account"] \| label="Account" \| css=#filter-account); SCN-HIS-004-P3: aborted: step 1 (select: Select Pending in the Status filter) failed: ResolveError: could not resolve element 'transactions.filter_status' (role=combobox[name="Status"] \| label="Status" \| css=#filter-status); SCN-HIS-004-E1: aborted: step 1 (select: Select the checking account) failed: ValueError: no option matching 'Everyday Checking'; options: ['All accounts'] | SCN-HIS-004-P1 (positive): unverified<br>SCN-HIS-004-P2 (positive): passed<br>SCN-HIS-004-P3 (positive): unverified<br>SCN-HIS-004-E1 (edge): unverified<br>SCN-HIS-004-N1 (negative): passed<br>SCN-HIS-007-P1 (positive): passed | [screenshot](evidence/SCN-HIS-004-P1__a2__final.png), [network](evidence/SCN-HIS-004-P1/a2_network.json), [network](evidence/SCN-HIS-004-P2/a2_network.json), [network](evidence/SCN-HIS-004-P3/a2_network.json), [network](evidence/SCN-HIS-004-E1/a2_network.json), [network](evidence/SCN-HIS-004-N1/a2_network.json), [network](evidence/SCN-HIS-007-P1/a1_network.json) |
| REQ-HIS-006 | An empty filter result shows a clear empty state, not an error or unrelated transactions. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-HIS-006-E1: inconclusive under chaos mode: 2 injected HTTP 503(s) during this attempt (GET http://localhost:8000/api/accounts, GET http://localhost:8000/api/transactions?start_date=2024-12-04&end_date=2024-12-14); underlying result was 'failed'. empty_state_shown: expected no rows, an empty-state message, and n | SCN-HIS-006-E1 (edge): unverified | [screenshot](evidence/SCN-HIS-006-E1__a2__final.png), [network](evidence/SCN-HIS-006-E1/a2_network.json) |
| REQ-GUI-002 | Interactive controls have readable labels and communicate loading, success, validation and failure states. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-GUI-002-P1: not executed | SCN-GUI-002-P1 (positive): not run | - |
| REQ-GUI-003 | A submitted action does not silently disappear: the UI shows a loading state or prevents duplicate submission while waiting. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-GUI-003-E1: not executed; SCN-GUI-003-E2: not executed | SCN-GUI-003-E1 (edge): not run<br>SCN-GUI-003-E2 (edge): not run | - |
| REQ-GUI-005 | After a recoverable error the user can retry or return to the dashboard. | The checks were inconclusive; see the technical reason.<br>Technical reason: SCN-GUI-005-N1: not executed; SCN-GUI-005-E1: not executed | SCN-GUI-005-N1 (negative): not run<br>SCN-GUI-005-E1 (edge): not run | - |
| REQ-SED-002 | Seed data includes enough historical transactions across multiple dates to exercise account, type, status and date filters. | The agent could not find the UI element 'transactions.filter_status' (role=combobox[name="Status"] \| label="Status" \| css=#filter-status) on the page, so the check was inconclusive rather than failed. The screenshot shows the page at that moment; a human should confirm whether the control is missing (a defect) or just labelled differently.<br>Technical reason: SCN-SED-002-P1: inconclusive under chaos mode: 3 injected HTTP 503(s) during this attempt (GET http://localhost:8000/api/accounts, GET http://localhost:8000/api/transactions, GET http://localhost:8000/api/transactions); underlying result was 'failed'. rows_equal_api_set: expected 12 transaction(s) matching no filte; SCN-SED-002-E1: aborted: step 1 (select: Select Pending status) failed: ResolveError: could not resolve element 'transactions.filter_status' (role=combobox[name="Status"] \| label="Status" \| css=#filter-status) | SCN-SED-002-P1 (positive): unverified<br>SCN-SED-002-E1 (edge): unverified | [screenshot](evidence/SCN-SED-002-P1__a2__final.png), [network](evidence/SCN-SED-002-P1/a2_network.json), [network](evidence/SCN-SED-002-E1/a2_network.json) |

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
| REQ-DSH-003 | unverified | Balances are displayed with exactly two decimal places. | SCN-DSH-003-P1: aborted: step 1 (wait_for: Wait for balances to display) failed: StepFailed: timed out waiting for dashboard.account_balance: could not resolve element 'dashboard.account_balance' (test_id=account-balance) |
| REQ-DSH-004 | passed | The UI never exposes a full account identifier. | all 2 scenario(s) passed: SCN-DSH-004-N1, SCN-DSH-004-E1 |
| REQ-DSH-005 | passed | Selecting an account opens an account detail view showing its current balance and related transaction history. | all 2 scenario(s) passed: SCN-DSH-005-P1, SCN-DSH-005-E1 |
| REQ-DSH-006 | unverified | Balances on the dashboard and account detail view agree with each other and with the latest successful transactions. | SCN-DSH-003-P1: aborted: step 1 (wait_for: Wait for balances to display) failed: StepFailed: timed out waiting for dashboard.account_balance: could not resolve element 'dashboard.account_balance' (test_id=account-balance) |
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
| REQ-HIS-001 | unverified | The user can view a combined transaction history and an account-specific history. | SCN-HIS-001-P1: inconclusive under chaos mode: 1 injected HTTP 503(s) during this attempt (GET http://localhost:8000/health); underlying result was 'failed'. rows_equal_api_set: expected 12 transaction(s) matching no filters (from the API, dates inclusive in America/Los_Angeles), each row showing its API amount; ob |
| REQ-HIS-002 | unverified | Each history row shows date and time, description or memo, transaction type, amount, status, and relevant account. | SCN-HIS-001-P1: inconclusive under chaos mode: 1 injected HTTP 503(s) during this attempt (GET http://localhost:8000/health); underlying result was 'failed'. rows_equal_api_set: expected 12 transaction(s) matching no filters (from the API, dates inclusive in America/Los_Angeles), each row showing its API amount; ob |
| REQ-HIS-003 | passed | Amount direction is clear for deposits, withdrawals and transfers. | all 1 scenario(s) passed: SCN-HIS-003-P1 |
| REQ-HIS-004 | unverified | History can be filtered by account, transaction type, status and date range, and every displayed row satisfies all selected filters. | SCN-HIS-004-P1: aborted: step 1 (select: Select the checking account in the Account filter) failed: ResolveError: could not resolve element 'transactions.filter_account' (role=combobox[name="Account"] \| label="Account" \| css=#filter-account); SCN-HIS-004-P3: aborted: step 1 (select: Select Pending in the Status filter) failed: ResolveError: could not resolve element 'transactions.filter_status' (role=combobox[name="Status"] \| label="Status" \| css=#filter-status); SCN-HIS-004-E1: aborted: step 1 (select: Select the checking account) failed: ValueError: no option matching 'Everyday Checking'; options: ['All accounts'] |
| REQ-HIS-005 | failed | Start and end dates are inclusive: transactions on either boundary date that match other filters are included. | SCN-HIS-005-E1 (edge) failed: rows_equal_api_set |
| REQ-HIS-006 | unverified | An empty filter result shows a clear empty state, not an error or unrelated transactions. | SCN-HIS-006-E1: inconclusive under chaos mode: 2 injected HTTP 503(s) during this attempt (GET http://localhost:8000/api/accounts, GET http://localhost:8000/api/transactions?start_date=2024-12-04&end_date=2024-12-14); underlying result was 'failed'. empty_state_shown: expected no rows, an empty-state message, and n |
| REQ-HIS-007 | passed | Clearing filters restores the unfiltered history. | all 1 scenario(s) passed: SCN-HIS-007-P1 |
| REQ-PRO-001 | passed | The profile view displays only synthetic demo-customer details such as display name and a masked email address. | all 1 scenario(s) passed: SCN-PRO-001-P1 |
| REQ-PRO-002 | passed | The profile view does not reveal passwords, authentication tokens, full account identifiers, or another user's data. | all 2 scenario(s) passed: SCN-DSH-004-N1, SCN-PRO-002-N1 |
| REQ-GUI-001 | passed | The app works in a current desktop browser at 1280x800 and remains usable at narrower widths. | all 2 scenario(s) passed: SCN-GUI-001-P1, SCN-GUI-001-E1 |
| REQ-GUI-002 | unverified | Interactive controls have readable labels and communicate loading, success, validation and failure states. | SCN-GUI-002-P1: not executed |
| REQ-GUI-003 | unverified | A submitted action does not silently disappear: the UI shows a loading state or prevents duplicate submission while waiting. | SCN-GUI-003-E1: not executed; SCN-GUI-003-E2: not executed |
| REQ-GUI-004 | passed | Navigation links and buttons lead to the labeled destination or perform the labeled action. | all 3 scenario(s) passed: SCN-GUI-004-P1, SCN-GUI-004-P2, SCN-GUI-004-P3 |
| REQ-GUI-005 | unverified | After a recoverable error the user can retry or return to the dashboard. | SCN-GUI-005-N1: not executed; SCN-GUI-005-E1: not executed |
| REQ-SED-001 | passed | The demo begins with one customer, one checking account and one savings account. | all 1 scenario(s) passed: SCN-SED-001-P1 |
| REQ-SED-002 | unverified | Seed data includes enough historical transactions across multiple dates to exercise account, type, status and date filters. | SCN-SED-002-P1: inconclusive under chaos mode: 3 injected HTTP 503(s) during this attempt (GET http://localhost:8000/api/accounts, GET http://localhost:8000/api/transactions, GET http://localhost:8000/api/transactions); underlying result was 'failed'. rows_equal_api_set: expected 12 transaction(s) matching no filte; SCN-SED-002-E1: aborted: step 1 (select: Select Pending status) failed: ResolveError: could not resolve element 'transactions.filter_status' (role=combobox[name="Status"] \| label="Status" \| css=#filter-status) |
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

Total estimated model cost: **$0.0334** of a $1.0000 budget (3.3%).

### Per agent

| Agent | Model(s) | Calls | Input tok | Output tok | Cache read | Cache write | USD | Share | Artifact-cache hits |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| executor_heal | claude-haiku-4-5 | 57 | 18,964 | 628 | 0 | 0 | $0.0221 | 66% | 37 |
| explorer | claude-haiku-4-5 | 6 | 0 | 0 | 0 | 0 | $0.0000 | 0% | 6 |
| reporter | claude-haiku-4-5 | 1 | 286 | 128 | 0 | 0 | $0.0009 | 3% | 0 |
| spec_analyst | claude-sonnet-5-5 | 1 | 0 | 0 | 0 | 0 | $0.0000 | 0% | 1 |
| test_planner | claude-sonnet-5-5 | 4 | 0 | 0 | 0 | 0 | $0.0000 | 0% | 4 |
| verifier | claude-haiku-4-5, claude-sonnet-5-5 | 3 | 4,052 | 493 | 1,582 | 0 | $0.0103 | 31% | 0 |
| **total** | | **72** | **23,302** | **1,249** | **1,582** | **0** | **$0.0334** | **100%** | **48** |

### Per model

| Model | Calls | Input tok | Output tok | Cache read | Cache write | USD | Artifact-cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|
| claude-haiku-4-5 | 66 | 21,410 | 929 | 0 | 0 | $0.0261 | 43 |
| claude-sonnet-5-5 | 6 | 1,892 | 320 | 1,582 | 0 | $0.0073 | 5 |

**Cold vs warm:** this was a warm run: 48 of 72 model calls were served from the on-disk artifact cache at no cost. A cold run (empty cache) is estimated at **$0.0895** (each cache hit priced at the average of live calls for the same agent and purpose).
Prompt-cache reads made up 6% of input tokens.

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
| chaos | latency_ms: 1500, error_rate: 0.15, seed: 7 |
| parallel | 3 |
| scenario_timeout_s | 90.0 |

## Resilience

Chaos mode was **on**: latency_ms 1500, error_rate 0.15, seed 7. Injected faults: delayed 614, passed_through 514, failed 100.
34 read-only scenarios ran in 3 parallel browser contexts.

| After scenario | What the agent saw and did |
|---|---|
| SCN-ACC-002-P1 | page left in an error state ("We couldn't load your accounts DemoBank ran into a problem on its side. Please ") → recovery: error state ("We couldn't load your accounts DemoBank ran into a problem on its side. Please try again. Try again"); clicked retry once |
| SCN-SED-002-E1 | page left in an error state ("We couldn't verify your session DemoBank ran into a problem on its side. Please") → recovery: error state ("We couldn't verify your session DemoBank ran into a problem on its side. Please try again. Try again Back to start"); clicked retry once → error state persisted; opened a new browser context |
| SCN-HIS-001-P3 | page left in an error state ('Account choices could not be loaded: DemoBank ran into a problem on its side. Pl') → error state persisted; opened a new browser context |
| SCN-SED-002-P1 | page left in an error state ('Account choices could not be loaded: DemoBank ran into a problem on its side. Pl') → recovery: error state ("Account choices could not be loaded: DemoBank ran into a problem on its side. Please try again. We couldn't load transac"); clicked retry once → error state persisted; opened a new browser context |
| SCN-HIS-004-P1 | page left in an error state ("We couldn't verify your session DemoBank ran into a problem on its side. Please") → recovery: error state ("We couldn't verify your session DemoBank ran into a problem on its side. Please try again. Try again Back to start"); clicked retry once → error state persisted; opened a new browser context |
| SCN-HIS-004-P3 | page left in an error state ("We couldn't verify your session DemoBank ran into a problem on its side. Please") → recovery: error state ("We couldn't verify your session DemoBank ran into a problem on its side. Please try again. Try again Back to start"); clicked retry once → error state persisted; opened a new browser context |
| SCN-HIS-005-E2 | page left in an error state ('Account choices could not be loaded: DemoBank ran into a problem on its side. Pl') → error state persisted; opened a new browser context |
| SCN-HIS-004-E1 | page left in an error state ('Account choices could not be loaded: DemoBank ran into a problem on its side. Pl') → error state persisted; opened a new browser context |
| SCN-HIS-004-N1 | page left in an error state ("We couldn't load transactions Start date must not be after end date. Try again") → recovery: error state ("We couldn't load transactions Start date must not be after end date. Try again Back to dashboard"); clicked retry once → error state persisted; opened a new browser context |
| SCN-HIS-006-E1 | page left in an error state ("We couldn't load transactions DemoBank ran into a problem on its side. Please t") → recovery: error state ("We couldn't load transactions DemoBank ran into a problem on its side. Please try again. Try again Back to dashboard"); clicked retry once |
| SCN-DSH-004-N1 | page left in an error state ("We couldn't load your profile DemoBank ran into a problem on its side. Please t") → recovery: error state ("We couldn't load your profile DemoBank ran into a problem on its side. Please try again. Try again Back to dashboard"); clicked retry once |
| SCN-DSH-003-P1 | page left in an error state ("We couldn't load your accounts DemoBank ran into a problem on its side. Please ") → recovery: error state ("We couldn't load your accounts DemoBank ran into a problem on its side. Please try again. Try again"); clicked retry once → error state persisted; opened a new browser context |
| SCN-PRO-002-N1 | page left in an error state ("We couldn't load your profile DemoBank ran into a problem on its side. Please t") → recovery: error state ("We couldn't load your profile DemoBank ran into a problem on its side. Please try again. Try again Back to dashboard"); clicked retry once |
| SCN-SCP-003-N1 | page left in an error state ("We couldn't load your accounts DemoBank ran into a problem on its side. Please ") → recovery: error state ("We couldn't load your accounts DemoBank ran into a problem on its side. Please try again. Try again Back to dashboard"); clicked retry once |

## Limitations

- --read-only: 19 data-changing scenarios were not run; their requirements are reported as unverified.
- Self-test chaos mode: up to 1500 ms random latency on API requests, 15% of GET API requests answered with HTTP 503 (seed 7); mutating requests are never failed. Faults were injected only into the page's API traffic during scenario execution (not exploration, snapshots or compensation); failures observed in an attempt that received an injected error are reported as unverified, not as defects.
- The agent repaired the browser state 14 time(s) between scenarios (see environment.resilience).
