# strata-v1 — difficulty-stratified cohort (lab request 007)

Ten easy / ten medium / ten hard cases per lane, sampled with seed 0 from the
structural difficulty labels produced by
`scripts/benchmarks/label_agentic_os_difficulty.py` (rubric in its docstring;
distributions in `difficulty-distribution.md`).

| file | role |
|---|---|
| `grbench-cases.jsonl`, `gtsqa-cases.jsonl` | qid + stratum, input to the cohort builder |
| `*-cohort.json`, `*-schedule.json` | frozen cohorts and paired schedules (grbench-single B0/C-/S1; gtsqa-single B0/C-/S1/S2a/S2b/S3/S4; gtsqa-multi B0/S4); entries carry `stratum` |
| `*-tool-schema.json` | discovery tool schemas (digests equal the gate-10 contracts) |
| `*-discovery-staging.json` | discovery caches with **whitespace** token counts — staging only; re-tokenise on the appliance with `build_agentic_os_discovery_cache.py --base-url http://127.0.0.1:8000` before any main-stage run, and use the resulting `*-discovery-serving.json` |

Run a stratified gate with the coordinator's `--only-stratum easy|medium|hard`
and read per-stratum results from the analyzer's `stratum-summary.md`
(`analyze_agentic_os_attempt_windows.py --schedule <schedule.json>`).
