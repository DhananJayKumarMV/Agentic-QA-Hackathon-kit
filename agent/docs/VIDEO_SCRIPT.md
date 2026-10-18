# Walkthrough video script (about 4:45)

A 3-5 minute screen recording that shows the agent reading the spec, operating the site, and producing
the audit report. Timestamps are targets. Narration is written to be read at a normal pace (about 150
words a minute). Every command runs from `agent/`.

## Before you record

> **API credits.** `extract` and `plan` call Claude unless their results are already in `.qa_cache/`, and
> a `run` also makes small Haiku and Sonnet calls (selector repair, judging, triage, summary). If you
> have no API credits when recording, skip the live segments and narrate over the committed artifacts:
> `runs/sample/artifacts/requirements.json`, `sitemap.json`, `plan.json`, and `runs/sample/report.html`.
> They come from a real full run (`qa-20261018-1628-861e`).

1. **Start the target and reset its data.**
   ```bash
   make -C ../test-website/database up      # first time only
   make -C ../test-website/database reset   # restore the seed: run this right before recording
   ```
   Start the backend (`:8000`) and frontend (`http://localhost:5173`) as described in
   `../test-website/LOCAL_SETUP.md`.
2. **Pre-warm the artifact cache** so the live parts finish in seconds, not minutes. Run each stage once,
   off camera, with the same arguments you use on camera:
   ```bash
   mkdir -p /tmp/demo
   .venv/bin/qa-agent extract --prd ../test-website/PRD.md --out /tmp/demo/requirements.json
   .venv/bin/qa-agent explore --url http://localhost:5173 --out /tmp/demo/sitemap.json
   .venv/bin/qa-agent plan --requirements /tmp/demo/requirements.json --sitemap /tmp/demo/sitemap.json --out /tmp/demo/plan.json
   .venv/bin/qa-agent run --url http://localhost:5173 --prd ../test-website/PRD.md --only 'REQ-TRF-00[5-8]' --only 'REQ-HIS-00[25]'
   make -C ../test-website/database reset   # the warm-up run changed balances; reset again
   ```
   Extraction, page labels and plans then come from `.qa_cache/` at $0. Say so on camera; it is part of
   the cost story.
3. **Pre-record the chaos run** (segment 7). It takes a few minutes, so run it before the session and
   open its report on camera:
   ```bash
   .venv/bin/qa-agent run --url http://localhost:5173 --prd ../test-website/PRD.md \
     --only 'REQ-DSH-*' --only 'REQ-HIS-*' --chaos-latency-ms 800 --chaos-error-rate 0.15 --chaos-seed 7
   make -C ../test-website/database reset
   ```
4. **Pre-open tabs:** `README.md` (architecture diagram), `runs/sample/report.html`, the chaos run's
   `report.html`.
5. **Screen setup:** terminal font 16 pt or larger, browser at 1280 px wide, light theme. Install `jq`
   (`brew install jq`) for the JSON one-liners. Hide `.env` and never show the API key.
6. Do one silent rehearsal. Cut long waits in editing rather than talking over them.

---

## 0:00-0:15 The challenge

**Screen:** title card or the repo `README.md` at the top.

> "The challenge: give an AI agent a product spec and a live web app, and have it decide, requirement by
> requirement, whether the app does what the spec says, with evidence a human can check, at a known
> cost. This is qa-agent."

## 0:15-0:45 Architecture: six agents

**Screen:** the mermaid diagram in `README.md` (section *Architecture*) or `docs/ARCHITECTURE.md`.
Point at each box as you name it.

> "Six agents work in a pipeline. The Spec Analyst turns the PRD into atomic requirements. The Explorer
> crawls the live site at the same time, read-only. The Test Planner writes positive, negative and edge
> scenarios. The Executor runs them in a real Chromium browser and records state before and after.
> The Verifier judges them, and the Reporter writes the audit. One design rule matters most:
> deterministic oracles decide every verdict. The LLMs plan, label and triage. They never mark
> anything as passed."

## 0:45-1:10 Reading the spec: `qa-agent extract`

**Screen:** terminal.

```bash
.venv/bin/qa-agent extract --prd ../test-website/PRD.md --out /tmp/demo/requirements.json
jq '.requirements | length' /tmp/demo/requirements.json
jq '.requirements[] | select(.id == "REQ-TRF-007")' /tmp/demo/requirements.json
```

> "Extraction produces 49 requirements. Each one keeps a verbatim quote from the PRD. If the quote is
> not in the document, the requirement is dropped, so the agent cannot invent rules. Each one also gets
> a priority, a kind, and whether testing it changes data. This came from the cache, so it cost
> nothing."

## 1:10-1:40 Operating the site: `qa-agent explore --headed`

**Screen:** terminal, then the browser window the command opens.

```bash
.venv/bin/qa-agent explore --url http://localhost:5173 --out /tmp/demo/sitemap.json --headed
jq '{pages: (.pages | keys), api: .api_base, entities: (.entities | keys)}' /tmp/demo/sitemap.json
```

> "The Explorer enters the demo, visits every page, and records locators, API endpoints and live
> entities such as the accounts and the timezone. It never submits a form. A mutation guard blocks any
> write while exploring. Page labels come from Haiku, and they're cached too."

## 1:40-2:00 Planning: `qa-agent plan`

```bash
.venv/bin/qa-agent plan --requirements /tmp/demo/requirements.json --sitemap /tmp/demo/sitemap.json --out /tmp/demo/plan.json
jq '[.scenarios[].polarity] | group_by(.) | map({(.[0]): length}) | add' /tmp/demo/plan.json
jq '.coverage | length' /tmp/demo/plan.json
```

> "The planner writes 59 scenarios: 27 positive, 20 negative and 12 edge cases. All 49 requirements are
> covered. Steps are written in a small DSL with oracles attached, so the Executor runs them without
> an LLM choosing each click. Haiku is called only to heal a locator that stopped matching."

(Numbers are from the sample run. Read yours off the screen if they differ.)

## 2:00-2:50 Testing the site: a headed `qa-agent run`

**Screen:** terminal on the left, the headed browser on the right.

```bash
.venv/bin/qa-agent run --url http://localhost:5173 --prd ../test-website/PRD.md --headed \
  --only 'REQ-TRF-00[5-8]' --only 'REQ-HIS-00[25]'
```

> "For the video I'm running only the transfer-validation and history requirements. A full run of all
> 49 takes about five to ten minutes. Watch the browser: it opens the transfer form, picks the checking
> account, and enters zero. Then a negative amount, then one cent more than the balance. Before and
> after each action the Executor snapshots balances and transactions through the API. A transfer that
> should have been rejected but went through is tagged and then reversed by a compensating transfer,
> so the demo data ends where it started."

While it runs, point at the console stages `1/6 ... 6/6`. When it finishes, read the final line:
requirement counts, defects and cost.

## 2:50-3:50 The report

**Screen:** open `runs/sample/report.html` (the full sample run). Use the section links at the top.

1. **Summary tiles (5 s).**
   > "Twenty-seven passed, eleven failed, eleven unverified. Eight defects, three critical, which trace
   > back to four underlying bugs. This rerun cost ten cents; a first run from scratch is about forty-five."
2. **A critical defect (25 s).** Click *Defects*, then **DEF-002**.
   > "DEF-002: transfers above the available balance are accepted. Here is the PRD quote. Expected: a
   > transfer of 4,369.76 from an account holding 4,369.75 is rejected. Observed: the API returned 201,
   > checking went to minus one cent, and the UI showed 'Transfer complete'. The steps to reproduce use
   > real account names and the page URL, so a developer can follow them by hand. And here's the
   > screenshot the agent took."
   Click the screenshot to enlarge it, then point at the *Failed checks* table and the *related: DEF-003*
   link.
3. **An unverified item (15 s).** Click *Unverified*, then the **REQ-ACC-001** row.
   > "Unverified is not a pass. Here the agent couldn't find the Enter Demo button, so it says the check
   > was inconclusive and shows what was on screen, rather than guessing either way."
4. **Cost (15 s).** Click *Cost*.
   > "Cost per agent and per model, with input, output and prompt-cache tokens. The planner is eighty
   > percent of the spend. Eighteen of thirty-six calls were served from the artifact cache, and a cold
   > run is estimated at about forty-seven cents."

## 3:50-4:10 Resilience under chaos

**Screen:** the pre-recorded chaos run's `report.html`, section *Resilience*. Then show the
*Resilience* section of `runs/sample/report.html`.

> "Chaos mode injects random latency and HTTP 503 errors into the app's own API calls. The agent
> retries, and a failure seen during an injected fault is downgraded to unverified, never reported as a
> bug. In our chaos runs, verdicts only ever moved from passed to unverified. Chaos never produced a
> false defect. Even without chaos, the sample run hit a persistent error page once. The agent clicked
> retry, then opened a fresh browser context, and recorded both steps."

## 4:10-4:35 Accuracy and honesty checks

**Screen:** terminal.

```bash
.venv/bin/python -m eval.score runs/sample
.venv/bin/pytest -q tests/test_no_eval_leak.py
```

> "We scored the sample run against a hand-verified answer key. All four labeled defects were found,
> with no false passes and no false failures. The answer key is gitignored, and this leak-guard test
> fails the build if the agent's code ever references it or hardcodes the demo's seed data."

## 4:35-4:45 Close

**Screen:** report summary tiles.

> "From a PRD and a URL to a requirement-by-requirement audit, with evidence, reproduction steps and
> a cost breakdown, for about thirty cents. Thanks for watching."

---

## Recording tips

- **Reset before every take:** `make -C ../test-website/database reset`. The negative-path scenarios
  move money when the app has bugs. Compensation restores the balances, but extra transactions remain
  in history.
- **Keep the cache warm.** Don't pass `--no-cache` on camera. If you change the PRD or prompts, warm
  the cache again, because cache keys include content hashes.
- **Keep live segments short.** If the headed run drags, cut to the console summary in editing. The full
  report you narrate is the committed `runs/sample/`, so the story never depends on a live run going
  well.
- **The numbers in this script come from `runs/sample`.** If you narrate a fresh run, read the counts
  and cost from its report instead.
- **Hide secrets.** Don't `cat .env`. Close terminal tabs that show the API key.
- **Re-render the sample report** after any template change: `.venv/bin/qa-agent report runs/sample`.
