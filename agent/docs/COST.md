# Cost

How much a run costs, where the money goes, and what we changed to bring it down.
All numbers are measured with the real API (Claude Sonnet 5.5 at $2 in / $10 out / $0.20 cache read /
$2.50 cache write per million tokens; Claude Haiku 4.5 at $1 / $5 / $0.10 / $1.25).

- **Cold** means the on-disk artifact cache (`.qa_cache/`) is empty, so every LLM stage calls the API.
  The prompt cache still works inside the run. For example, the planner's later calls read the system
  prompt that its first call wrote.
- **Warm** means a rerun against the same PRD, site and prompt versions. Requirements, page labels
  and plans come from the artifact cache, so those stages cost $0.

## Per-stage cost

| Stage | Model / effort | Calls | Phase 2 (cold) | **Final (cold, measured)** | Warm rerun |
|---|---|---:|---:|---:|---:|
| Spec Analyst (extract) | Sonnet 5.5, low | 1 | $0.102 | **$0.083** | $0 (artifact cache) |
| Explorer (page labels, entry pick) | Haiku 4.5 | 6 | about $0.02 | **$0.015** | $0 (artifact cache) |
| Test Planner | Sonnet 5.5, medium | 4 | $0.463 | **$0.281** | $0 (artifact cache) |
| Executor self-heal | Haiku 4.5 | 14 | $0.024 | **$0.017** | about $0.017 |
| Verifier judge + triage | Haiku 4.5 judge; Sonnet 5.5 low triage | 9 | $0.187 | **$0.058** | about $0.06 |
| Reporter summary | Haiku 4.5 | 1 | $0.002 | **$0.002** | $0.002 |
| **Total** | | **35** | **about $0.80** | **$0.455** | **about $0.08-0.10** |

The "Final" column is the integrated cold run `qa-20261018-1541-25ad`: database reset, artifact cache off
(`--no-cache`), all Phase 3 changes merged, 63 scenarios, 9 min 39 s wall time. Prompt caching still
applies within the run (the planner read 48k cached tokens). Heal, judge and triage depend on how many
scenarios fail or need healing, so they vary from run to run. The Phase 2 column is the first
measured run before cost tuning.

Projected cost per run:
- **Cold first run: about $0.40-0.46** (measured $0.455; the planner is about 60% of it).
- **Warm rerun against the same PRD and site: about $0.08-0.10.** Requirements, page labels and the
  plan come from the artifact cache, and triage results are cached per failure.

## Planner detail (the dominant cost)

Before:
- 8 calls, one per requirement area.
- The 23,387-token system prompt was written to the cache once and read by the other 7 calls.
- Output was 36.9k tokens, which made up 80% of the stage cost.

After:
- 4 calls.
- The 16,516-token system prompt is written once and read by the other 3 calls.
- Output is 21.2k tokens.

Most output tokens are scenario JSON. Counting tokens on the drafts showed thinking at medium effort is a
small share, so the main lever was the shape of the JSON, not effort.

| Variant | Cold cost | Scenarios | Positive / negative / edge | Requirements with a negative or edge scenario | Requirement-polarity pairs | Uncovered |
|---|---:|---:|---|---:|---:|---:|
| Before: plan-v2, 8 area calls | $0.463 | 77 | 34 / 19 / 24 | 35 of 48 | 78 | 0 |
| plan-v3 schema, 4 calls | $0.279 | 61 | 23 / 17 / 21 | 34 of 48 | 76 | 0 |
| plan-v3, 6 calls (at most 8 requirements per call) | about $0.30 | 65 | 27 / 20 / 18 | 33 of 48 | 73 | 0 |
| plan-v3, **effort low** (rejected) | about $0.23 | 51 | 23 / 13 / 15 | **28** of 48 | 67 | 0 |
| **Final: plan-v3, compact requirements text, spec-v3 input** | **$0.265** | 62 | 25 / 21 / 16 | 38 of 50 | 67 | 0 |

For transfers, the final plan keeps every rejection and boundary scenario that the old plan had:
- same account;
- blank, zero, negative, non-numeric and over-precision amounts, one scenario each;
- one cent over the balance;
- exactly the full balance;
- double submit;
- persistence after reload.

The scenario count fell mainly because one scenario now covers several related requirements, not because
edge classes were lost.

## Levers and their measured effect

1. **Smaller draft schema (planner, plan-v3).** The model no longer writes fields the system can derive:
   - `phase` and `mutates_state` are worked out from the oracles and submit steps.
   - `expected_money_delta` is copied from the `balance_delta` oracle.
   - `snapshot_state` steps are inserted automatically.
   - `preconditions` defaults to logged-in.
   - `rationale` is built from the requirement statement.
   - Oracle `severity` comes from requirement priority (P0 critical, P1 major, P2 minor) unless the
     model overrides it.
   - Targets are short strings (`key`, `key[n]`, `key~text`) instead of objects full of nulls.

   Output fell by about 40% per requirement. The contract `Scenario` is unchanged, so reports read
   exactly as before.

   A fully typed oracle-args schema would have saved about 1.5k more tokens, but the API rejected it
   ("compiled grammar is too large"). Args therefore stay as JSON strings.
2. **Batching areas.** Areas are packed into calls of at most 14 requirements (`BATCH_REQS`), with
   the smallest batch first. That gives 4 calls instead of 8, which halves the per-call overhead (cache
   reads and preamble). With 6 calls, quality was the same and cost was about $0.02 higher.
3. **Compact system prompt.**
   - The SiteMap drops null, false and empty fields and the keys it repeats.
   - Requirements are rendered as text lines instead of JSON.
   - The prompt went from 23.4k to 16.5k tokens: about $0.017 less on the cache write and $0.001 less
     on each cache read.
4. **Prompt caching verified.** `ClaudeLLM` logs `cache_read` and `cache_write` for every call. The
   first planner call runs alone and writes the cache. All later calls read the full prefix
   (`cache_write=0`). No silent invalidators were found: the JSON is sorted and contains no
   timestamps. Changing effort did not invalidate the cache.
5. **Planner effort: kept at medium.** Low effort saves about 30% but cut negative and edge coverage
   from 34 to 28 requirements and positive-mutation scenarios from 8 to 5. That is a loss in
   defect-finding, so we rejected it.
6. **Extraction: Sonnet at low effort, terse hints (spec-v3).**
   - Low effort cost the same as medium and covered as much of the PRD, because about 95% of the
     output is requirement JSON, not thinking.
   - Capping `verification_hints` at 1-3 short hints cut the output from 9.1k to 7.8k tokens. It also
     shrank the planner's prompt.
   - Haiku 4.5 cost $0.070 but produced **105** requirements instead of about 52. It also dropped a
     quote and covered fewer PRD sentences (51 vs 53-55 of 59). Doubling the requirements would roughly
     double the planner's cost, so we rejected Haiku for this stage.
7. **Cost accounting.**
   - `usage.output_tokens` already includes adaptive-thinking tokens, so `price()` gives the exact bill.
   - Every artifact-cache entry now stores the original call's cost in a `.meta.json` sidecar.
   - `cost.json` `by_agent` and `by_model` buckets carry `cold_usd`: what the run would have cost
     without the artifact cache. `cold_unknown_hits` counts cached entries written before sidecars
     existed.

## Levers applied after this phase

- **Verifier triage: $0.18 -> about $0.06 per run.** The first call runs alone to write the prompt cache,
  effort is `low`, identical failures (same requirement and symptom) share one call, and results are
  artifact-cached so warm reruns are free. Triage may still only downgrade a failure, never upgrade it.

## Further levers (not yet applied)

- **Executor heal:** cache heal results by (element key, DOM hash); heals repeat across scenarios.
- **Planner:** the remaining dominant cost. Fewer, broader scenarios would cut it further, but the
  measured trade-off (see above) lost edge-case coverage, so it was not applied.

## Reproducing the measurements

```bash
cd agent
.venv/bin/qa-agent extract --prd ../test-website/PRD.md --out runs/_stages/req.json
.venv/bin/qa-agent plan --requirements runs/_stages/req.json --sitemap runs/_stages/sitemap.json --out runs/_stages/plan.json
```

For a cold measurement, bump `PROMPT_VERSION` or remove `.qa_cache/{spec_analyst,test_planner}`.
Enable `INFO` logging for `qa_agent.llm.client` to see each call's token counts, cache read/write
and cost.
