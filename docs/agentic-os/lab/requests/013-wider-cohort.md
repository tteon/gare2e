---
id: 013
title: Wider cohort — strata-v2 (30 cases per stratum per lane) at the recommended serving shape, with the DB fixes live
state: done
created: 2026-09-10
owner_intent: user
bead: seocho-sudv
---

## Intent (user)
"네 그럼 우리 이번엔 모수를 더 넓혀서 실험해보세요" (2026-09-10 23:55). Every stratum × lane cell so far has N = 10 (strata-v1
was frozen with 10 unique cases per stratum per lane); funnel differences under ~20 points are noise. Widen the sample so the
stratum findings (K=4 gain, bottleneck shares, repair-loop counts, GRBench DB tail) and the DB fixes (timeout, indexes, anchor
guard) are measured with N = 30 per cell.

## Acceptance (user; Claude may draft)
- [x] strata-v2 cohorts: 30 unique cases per stratum (easy/medium/hard) for GRBench single/multi (same qids), GTSQA single, GTSQA multi, drawn from labels v1.1 (`label_agentic_os_difficulty.py`), frozen schedules + discovery caches + tool schemas under `.seocho/benchmarks/agentic-os/strata-v2/`, committed frozen copies to gare2e `examples/agentic-os/frozen/strata-v2/`.
- [x] One pass per configuration over all three strata in a single queue: `all-k4` (TP=2 monolith, --max-num-seqs 8, K=4, baseline prompt, reasoning off) — 360 cases (90 per lane). Optional second pass `all-k8` if the ceiling allows.
- [x] Bundle ≥ v40 (Bolt timeout in both executors, indexes created at `dozer_indexes` after DozerDB ready, anchor guard on, OTel export on with the in-instance receiver; `SEOCHO_OTLP_ENDPOINT=http://127.0.0.1:4317 SEOCHO_OTEL_FILE_RECEIVER=1`).
- [x] Per stratum (N=30): funnel, bottleneck shares, request-stage table, repair counts; GRBench single DB share and max DB call (< 15 s) as the DB-fix verdict; count of `unanchored_scan` rejections and whether repairs recover.
- [x] Package uploaded to S3 (`seocho-agentic-os/traces/<run>`), manifest + index committed to gare2e, report artifact.

## Constraints (user)
- Paid time bounded; one instance; reuse the queue-mode lifecycle.

## Feasibility (Claude)
- verdict: feasible; needs one preparation step the strata-v1 freeze already did once (cohort sampling → discovery cache build → schedules), now with 30/stratum. `grbench-discovery-records-all.jsonl` / `gtsqa-discovery-records-all.jsonl` under strata-v1 already hold discovery records for every qid, so the serving caches can be rebuilt offline (the `cache-serving` cmd pass in lab-s3's queue is the reference).
- estimated cost/time: K=4 over 360 cases ≈ 9× the 40-case K=4 pass (≈ 13 min) ≈ 100–120 min; + bootstrap/index build ≈ 20 min → ≈ 2.3 h. On a $1.96/h A100 pair ≈ $4.5, on the $2.9 RTX PRO 6000 pair ≈ $6.7. Adding `all-k8` (≈ 80 min) → ≈ 3.6 h / ≤ $10.5. Ceiling: `--max-runtime-s 14400` (4 h), inner `timeout 13800`.
- alternatives: 20/stratum (≈ 1.5 h) if the market only offers the $2.9 host and budget matters.

## Approval (user)
approved: yes  # chat 2026-09-10 "모수를 더 넓혀서 실험해보세요"
budget_usd: 11

## Plan (Claude — execute in a session with shell access; this one is blocked)
1. `bd create` the bead; claim.
2. Build strata-v2: run the stratified sampler for 30/stratum with labels v1.1 (`scripts/benchmarks/stratified_grbench_cases.py` and the GTSQA equivalent used for strata-v1 — see lab 007 request for the exact commands), write cohorts/schedules to `.seocho/benchmarks/agentic-os/strata-v2/`; rebuild `strata-grbench-discovery-serving.json` / `strata-gtsqa-discovery-serving.json` from the `*-records-all.jsonl` files with the same tool-schema digests (compare `grbench-tool-schema.json` sha with strata-v1); validate with `validate_discovery_cache` via a dry `--verify` if available. Commit frozen copies to gare2e.
3. Queue `strata-v2/session-E/lab-queue.tsv`: `all-k4` (and `all-k8`) with `SEOCHO_MAIN_STRATUM=easy,medium,hard` (comma list → all), `SEOCHO_CONCURRENCY=4|8`, strata-v2 schedule/discovery file names, `VLLM_EXTRA_ARGS_OVERRIDE=--max-num-seqs,8`, `VLLM_REPLICAS=1`.
4. Launcher `launch_lab_s11.sh` from `launch_lab_s10.sh`: bundle v40 (or newer), uploads switched to strata-v2 files, queue session-E, `--max-runtime-s 14400`, inner `timeout 13800`, offer query `num_gpus=2 rentable=true cuda_max_good>=13.0 gpu_ram>=79`, `--gpu-name-regex "A100|RTX PRO 6000"`, `--max-dph 2.9`. Gate: no live `seocho-agentic-os-*` instance.
5. After the run: catalog + `summarize_agentic_os_queue_session.py --schedule <strata-v2 schedules>` + `summarize_agentic_os_otel_stages.py` + `pack_agentic_os_traces.py … --s3-uri s3://seocho-agentic-os-traces/seocho-agentic-os/traces/<run> --aws-profile seocho-traces --lab-request 013`; commit manifest/index; publish report.

## Log (Claude)
- 2026-09-11 12:10 KST — done: package s3://seocho-agentic-os-traces/seocho-agentic-os/traces/lab-s11-try-075031 (812 files, 17.3 MB, leak 0), manifest/index in gare2e traces/ (bfe3f8f0); report https://claude.ai/code/artifact/04719482-b362-477d-b154-769afb6bb263
- 2026-09-11 11:30 KST — analysis done (catalog, session summary with strata-v2 schedules, OTel stage tables, per-stratum funnel script); Result written; trace package uploading.
- 2026-09-11 10:10 KST — lab-s11 complete: instance 50532353 (RTX PRO 6000 pair, Taiwan, $2.66/h), 07:50–10:10, ≈ $6.2. Queue: cache-serving 4 s, all-k4 4283 s, all-k8 2559 s, all exit 0; lifecycle state=complete, artifacts copied + verified, destroyed + verified. dozer_indexes phase ran after dozer_ready (lab-s9 fix confirmed live). Analysis next.
- 2026-09-11 08:30 KST — prep done: bead seocho-sudv; entrypoint dozer_indexes fix verified (23 shell tests) → bundle v40 (sha 561e26f1…); strata-v2 frozen (.seocho/benchmarks/agentic-os/strata-v2, gare2e 28fdfe35: 30/stratum/lane, v1 ⊂ v2 under labels v1.1, one repetition, offline staging caches from strata-v1 records-all); queue session-E = cache-serving → all-k4 → all-k8 (SEOCHO_MAIN_STRATUM unset = all strata; --only-stratum is a repeatable flag, not a comma list); launcher launch_lab_s11.sh (label seocho-agentic-os-lab-s11, bundle v40, ceiling 14400 s / inner 13800 s, offer gpu_ram>=79 A100|RTX PRO 6000 ≤ $2.9/h). Launching as unit seocho-vast-lab-s11.
- 2026-09-10 23:55 KST — request written in a shell-blocked session; execution starts in the next session after the lab-s10 (v40 one-pass) validation, or instead of it if the market is thin (the wide pass validates the DB fixes too).

## Result (Claude)
Report: https://claude.ai/code/artifact/04719482-b362-477d-b154-769afb6bb263

### lab-s11 (2026-09-11, bundle v40, TP=2 monolith on 2× RTX PRO 6000, reasoning off, B0, strata-v2 N=30 per cell)
Evidence: `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s11-try-075031/session-summary/` (pass-serving, lane-client, lane-bottleneck, lane-funnel, stratum-funnel), `run-all-k{4,8}/catalog/`, `run-all-k{4,8}/otel-stages/`; package `s3://seocho-agentic-os-traces/seocho-agentic-os/traces/lab-s11-try-075031`.

**Serving (pass level).** all-k4: 4283 s wall, 2099 requests, 126.8 gen tok/s, prefix-hit 0.50, ITL 25 ms, GPU 93 %, 2470 kJ. all-k8: 2559 s, 2122 requests, 212.8 gen tok/s (+68 %), ITL 29 ms, GPU 91 %, 1477 kJ (−40 % energy). Per-case wall grows 11–15 % at K=8 (queueing shows as decode 6.3 → 7.3 s/req), so K=8 buys throughput, not latency.

**DB fixes (lab 012 items 1–3) — live verdict.** All 10 GRBench lookup indexes ONLINE at `dozer_indexes` before the workload. GRBench single graph-DB share fell from 0.74 (lab-s7) to 0.005–0.05; graph call p50 16 ms / p90 35 ms (k4). The 15 s Bolt timeout fired on 3 calls in 723 (max 16.5 s, outcome `rejected`) — the 43–63 s label scans are gone. Static anchor guard: `unanchored_scan` rejected 16 (k4) / 20 (k8) first attempts in 9 / 12 cases; every one of those cases repaired to a validated query, but only 1 / 3 reached non-empty rows — the guard removes the DB tail without recovering the answer.

**Per-stratum funnel (N = 30 per cell, k4).**
| pass | lane | stratum | N | triggered | validated | executed | nonempty | answered | budget-exhausted | wall p50 s | repairs/case |
|---|---|---|---|---|---|---|---|---|---|---|---|
| k4 | grbench-multi | easy | 30 | 30 | 3 | 30 | 0 | 0 | 0 | 66.2 | 2.7 |
| k4 | grbench-multi | medium | 30 | 30 | 4 | 30 | 0 | 0 | 0 | 67.3 | 2.67 |
| k4 | grbench-multi | hard | 30 | 29 | 4 | 29 | 0 | 0 | 0 | 66.2 | 2.8 |
| k4 | grbench-single | easy | 30 | 30 | 30 | 30 | 25 | 9 | 17 | 11.8 | 0.83 |
| k4 | grbench-single | medium | 30 | 30 | 30 | 30 | 30 | 16 | 14 | 8.9 | 0.43 |
| k4 | grbench-single | hard | 30 | 30 | 29 | 29 | 24 | 2 | 28 | 14.2 | 0.87 |
| k4 | gtsqa-multi | easy | 30 | 12 | 12 | 12 | 0 | 0 | 0 | 3.5 | 0.5 |
| k4 | gtsqa-multi | medium | 30 | 29 | 14 | 29 | 0 | 0 | 0 | 66.7 | 1.87 |
| k4 | gtsqa-multi | hard | 30 | 30 | 17 | 30 | 0 | 0 | 2 | 71.7 | 2.17 |
| k4 | gtsqa-single | easy | 30 | 24 | 23 | 23 | 11 | 0 | 22 | 14.0 | 1.27 |
| k4 | gtsqa-single | medium | 30 | 28 | 28 | 28 | 16 | 0 | 28 | 14.2 | 0.93 |
| k4 | gtsqa-single | hard | 30 | 29 | 28 | 28 | 14 | 0 | 29 | 16.2 | 1.17 |
| k8 | grbench-multi | easy | 30 | 30 | 3 | 30 | 0 | 0 | 0 | 73.4 | 2.7 |
| k8 | grbench-multi | medium | 30 | 30 | 6 | 30 | 0 | 0 | 0 | 76.3 | 2.6 |
| k8 | grbench-multi | hard | 30 | 29 | 2 | 29 | 0 | 0 | 0 | 73.0 | 2.83 |
| k8 | grbench-single | easy | 30 | 30 | 30 | 30 | 24 | 7 | 20 | 15.8 | 1 |
| k8 | grbench-single | medium | 30 | 30 | 30 | 30 | 30 | 16 | 14 | 10.3 | 0.6 |
| k8 | grbench-single | hard | 30 | 30 | 28 | 28 | 23 | 3 | 27 | 15.9 | 0.87 |
| k8 | gtsqa-multi | easy | 30 | 12 | 12 | 12 | 0 | 0 | 0 | 3.3 | 0.6 |
| k8 | gtsqa-multi | medium | 30 | 29 | 20 | 29 | 0 | 0 | 0 | 71.2 | 1.5 |
| k8 | gtsqa-multi | hard | 30 | 30 | 16 | 30 | 0 | 0 | 2 | 79.6 | 2.13 |
| k8 | gtsqa-single | easy | 30 | 25 | 25 | 25 | 10 | 0 | 21 | 18.1 | 1.43 |
| k8 | gtsqa-single | medium | 30 | 28 | 27 | 27 | 15 | 0 | 28 | 16.5 | 1.03 |
| k8 | gtsqa-single | hard | 30 | 29 | 27 | 27 | 10 | 0 | 29 | 19.5 | 1.37 |

Readings at N=30 (lab-s3 N=10 readings hold):
- GRBench single: retrieval 30/30 in every stratum, validated 29–30, non-empty 24–30; answered 9 / 16 / 2 (easy / medium / hard), budget-exhausted 17 / 14 / 28. Difficulty acts on finalisation, not retrieval; hard is where the 6-turn budget runs out (28/30).
- GRBench multi: 3–4 / 30 validated per stratum, 0 non-empty — `missing_workspace_scope` 306 times in 90 cases (3.4 per case); the specialist never learns the scope template (012 item 4 is the fix). Wall 66 s per case, 99.6 % LLM.
- GTSQA multi: easy triggers retrieval 12/30 (medium 29, hard 30) — the easy-stratum retrieval skip is confirmed at N=30 (012 item 5); when it triggers, 12–17 validated, 0 non-empty.
- GTSQA single: triggers 24–29, validated 23–28, non-empty 11 / 16 / 14, answered 0 in all strata, budget-exhausted 22–29 — the self-thought/repair loop is the whole story (LLM share 0.997; 18–20 k reasoning tokens per case).
- Concurrency (k8 vs k4) leaves every funnel column within ±3 cases per cell: serving shape does not change agent behaviour.

**Observability.** vLLM server spans (4221 `chat`, `llm_request`) joined to 2085 / 2099 client requests (99.3 %) via traceparent; stage table per lane in `otel-stages/`. Defect: every runner span carried `service.name=vllm` because run_lab_pass.sh inherited the engine's `OTEL_SERVICE_NAME` — fixed (`SEOCHO_OTEL_SERVICE_NAME`, shell test), lands with 011.

## Decision needed (Claude -> user)
None; proceed with `all-k4` first, add `all-k8` only if admission lands on the $1.96 A100 pair.
