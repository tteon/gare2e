# strata-v2 — widened difficulty-stratified cohort (lab request 013, bead seocho-sudv)

Thirty easy / thirty medium / thirty hard cases per lane (90 per lane, 360 per
B0 pass over the four lanes), sampled with seed 0 from the structural difficulty
labels v1.1 (`label_agentic_os_difficulty.py`; `*-difficulty.jsonl` here are the
full label files). Every strata-v1 case that kept its v1.1 stratum is included,
so v1 ⊂ v2 (GRBench easy kept 8 of 10: qids 101 and 109 moved to medium under
v1.1); the remaining slots were drawn uniformly from the rest of the stratum.

| file | role |
|---|---|
| `*-cases.jsonl` | qid + stratum, input to the cohort builder |
| `*-cohort.json`, `*-schedule.json` | frozen cohorts and paired schedules, one repetition, warm cache phase (grbench-single B0/C-/S1; gtsqa-single B0/C-/S1/S2a/S2b/S3/S4; gtsqa-multi B0/S4). The e2e main matrix runs `--only-arm B0` |
| `*-tool-schema.json` | discovery tool schemas (identical to strata-v1 / gate-10) |
| `*-discovery-staging.json` | discovery caches with **whitespace** token counts, built offline from the strata-v1 `*-discovery-records-all.jsonl` (no new LLM calls) — re-tokenise on the appliance with `build_agentic_os_discovery_cache.py --base-url http://127.0.0.1:8000` (queue step `cache-serving` in `../../lab-queue.strata-v2-session-E.tsv`) |

Cohort digests: grbench 877a26d1…, gtsqa 9f0fc540…. Leave `SEOCHO_MAIN_STRATUM`
unset to run all three strata in one pass; `--only-stratum` takes a list, not a
comma-joined string.
