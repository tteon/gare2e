# Frozen gate-10 artifacts

Content-free inputs the e2e binds into every receipt by digest. Schema slices
(labels, properties, relationship types, scope rule) and paired schedules of
question ids only — no questions, answers, prompts, or model output.

| file | role |
|---|---|
| `grbench-discovery-serving.json` | GRBench discovery cache: B0 / C- / S1 schema responses per qid with serving-token counts validated against vLLM 0.27.1 for `Qwen/Qwen3.6-27B@6a9e13bd` |
| `gtsqa-discovery-serving.json` | GTSQA discovery cache, same contract |
| `grbench-single-tool-schedule.json` | frozen paired schedule (arms B0/C-/S1, 10 cases); the GRBench multi lane reuses it |
| `gtsqa-single-tool-schedule.json` | frozen paired schedule (B0/C-/S1/S2a/S2b/S3/S4) |
| `gtsqa-multi-tool-schedule.json` | frozen paired schedule (B0/S4) |

Regenerating any of these changes the experiment identity (`cache_digest`,
`cohort_digest`); do so only deliberately with
`build_agentic_os_discovery_cache.py` (needs the live vLLM tokenizer) and
`build_agentic_os_cohort_schedule.py`.
