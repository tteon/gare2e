---
id: 022
title: Same Text2Cypher workload on three models — Qwen3.5-9B (dense) / Qwen3.6-27B (dense, baseline) / Qwen3.6-35B-A3B (MoE, 3B active) — plus draft_model speculative decoding (27B target, 35B-A3B draft)
state: approved (lab-s20 after lab-s19)
created: 2026-09-13
owner_intent: user
bead: seocho-s79g
---

## Request (user)
Chat 2026-09-13: talk section 5 — "apply the same GraphCoT workload to three LLMs and compare Cypher generation accuracy, reasoning steps, generated tokens, speculative-decoding effect, KV-cache efficiency and decode throughput", so that the model is chosen by workload rather than by size. Approved together with 020 A+B and 019 ("네 모두 진행해주세요").

## Design (Claude)
- Models (HF, immutable revisions): `Qwen/Qwen3.5-9B` c2022362 (19 GB, dense), `Qwen/Qwen3.6-27B` 6a9e13bd (56 GB, dense, the baseline of labs 013–021), `Qwen/Qwen3.6-35B-A3B` 995ad96e (72 GB, MoE 3B active). All three share the Qwen tokenizer, chat template (`enable_thinking=false`), `qwen3_coder` tool-call parser and `qwen3` reasoning parser, so the harness, discovery cache byte cap and prompts are unchanged; only the token counts in the discovery cache are re-derived per model (cache-serving after each model switch). Qwen3.6 has no dense model below 27B, hence the 3.5 generation for the small dense point — stated as a caveat.
- Serving: TP=2 on the same NVLink pair for all three (keeps K=8, `--max-num-seqs 8`, 32 k context). Draft-model arm: `{"method":"draft_model","model":"Qwen/Qwen3.6-35B-A3B","num_speculative_tokens":3}` on the 27B target (same vocabulary), judged with the parity rule of 020.
- Cohort / arm: grbench-single, S1, strata-v2 90 questions, K=8, hint off (comparable with 013–021); one pass per model + one draft arm = 4 passes.
- Metrics per model: answered / 90 by stratum, validated rate and policy-rejection codes (Cypher validity), turns and output tokens per case, decode tok/s and ITL from the vLLM metrics window, KV-cache peak, per-case wall; for the draft arm additionally acceptance rate / mean accepted length and funnel parity vs the 27B baseline.
- Queue (session-N): prefetch both new checkpoints in the background at session start (cmd row, `snapshot_download`), then for each model: `restart_vllm.sh` with `VLLM_MODEL`/`VLLM_MODEL_REVISION` (cmd row) → cache-serving for that model → e2e pass (`VLLM_MODEL=…` in the payload, no override so no second restart). 27B baseline pass reuses the session-M a0 shape.
- Cost: ≈ 4 × (restart 2–5 min + 8–12 min pass) + downloads overlapping the first pass ≈ 75–90 min ≈ $4–5 on the A100 pair (≤ $8 on an H100 pair).
- Risks: 35B-A3B MoE at TP=2 with 72 GB weights leaves ≈ 40 GB KV — fine at K=8; the small model may not follow the tool protocol (then its funnel shows `graph_tool_selected` = 0 — a result, not a failure); draft_model with a MoE draft may be slower than the target's own MTP (a8 in lab-s19) — that comparison is the point.

## Approval (user)
approved: yes   # chat 2026-09-13 "네 모두 진행해주세요"
budget_usd: 8

## Plan (Claude)
1. session-N queue + launcher (after lab-s19 finishes; one instance at a time). 2. Summaries: per-model funnel, serving profile, tokens; draft arm parity. 3. Result section + report page (three-model table for talk section 5).

## Log (Claude)
- 2026-09-13 12:50 KST — report page "Faster Tokens, Fewer Tokens" (labs 019–022, Korean): https://claude.ai/code/artifact/30a1e812-1e31-4426-8fd9-ba71bfd82189
- 2026-09-13 12:29 KST — 9B follow-up ran in lab-s21: query-first protocol without grammar 45 / 90 at 3.6 s per case (vs 20 / 90 under tool calling), with the ontology grammar 48 / 90 but 13.1 s per case (see 020 Result). The small model's failure was the tool-calling protocol, not Cypher.
- 2026-09-13 11:11 KST — **lab-s20 done** (instance 50831945, host 458442 A100 SXM4 NVLink, $3.14/h, 10:30 → 11:10 ≈ 40 min ≈ $2.1). Three model passes ran (27B 474 s, 9B 115 s, 35B-A3B 205 s; model switches 206–270 s each incl. load); the draft arm failed at vLLM start (20 s). Offline reproduction of the config: vLLM 0.27.1 rewrites any `qwen3_5` / `qwen3_5_moe` draft model into `method=mtp` (`Qwen3_5MoeMTP` architecture) regardless of `method=draft_model`, so a Qwen3.6 draft for a Qwen3.6 target is not a draft-model arm on this stack — it tries to load the draft's MTP head against the target and crashes. No other same-vocabulary family exists (Qwen3 has a 151 k vocabulary vs 248 k). Draft arm dropped with this evidence; MTP (lab-s19 a8) is the model-based speculation for this family. Essential collection 3 011 files verified; destroyed + verified. Package `s3://…/traces/lab-s20-try-103026` (324 files, 4.5 MB; index 10 runs). Tables `runs/lab-s20-try-103026/session-summary/{serving-arms,scheduler-occupancy}.md`.
- 2026-09-13 09:40 KST — session-N queue written (prefetch → 27B baseline → switch/cache/e2e per model → 27B + draft 35B-A3B, `--max-model-len 16384` for the draft arm); launcher `launch_lab_s20.sh` (bundle v47, disk 280 GB) ready; launches when lab-s19 ends.
- 2026-09-13 09:20 KST — drafted; models and revisions verified on HF; bead seocho-s79g.

## Result (Claude)
### Same Text2Cypher workload, three models (lab-s20; S1, 90 questions, K=8, TP=2, hint off)
| model | answered e/m/h (total) | policy rejections | DB errors | zero-row | queries p50 | turns p50 | out tokens | TPOT p50 | per-case wall p50 | gen tok/s | running /8 | GPU util | KV max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3.6-27B (dense, baseline) | 19/29/8 (**56**) | 17 (scope 16, unanchored 1) | 31 | 57 | 2.5 | 4.5 | 41 k | 27.9 ms | 9.0 s | 180 | 4.8 | 95 % | 2.7 % |
| Qwen3.6-35B-A3B (MoE, 3 B active) | 3/10/7 (20) | **147** (scope 137, label 4, unanchored 6) | 12 | 91 | 5.0 | 6.0 | 54 k | 15.6 ms | 8.8 s | 274 | 4.2 | 70 % | 1.2 % |
| Qwen3.5-9B (dense) | 5/12/3 (20) | **232** (scope 171, missing_return 50, unanchored 11) | 35 | 61 | 5.0 | 6.0 | 56 k | 10.5 ms | 6.4 s | 517 | 5.2 | 85 % | 0.7 % |

**Readings**
- **Faster tokens did not buy answers.** The 9B decodes 2.7× faster per token and the MoE 1.8×, yet both answer 20 / 90 against the 27B's 56, and both generate *more* tokens per case (54–56 k vs 41 k) because they spend all six turns repairing. Per-case wall barely improves (6.4 / 8.8 vs 9.0 s) — the repair turns eat the speed.
- **The failure is protocol, not reasoning:** 137 and 171 `missing_workspace_scope` rejections — the small models drop the mandatory `{_workspace_id: $workspace_id}` map that the 27B keeps in 74 / 90 cases; the 9B also omits `RETURN` 50 times. These are exactly the errors the read-only grammar makes impossible by construction, so the guided-decoding arms in lab-s21 now include the 9B (`m9b-grammar-none` vs `m9b-grammar-onto`): if the grammar lifts the 9B toward the 27B's answered count at 2.7× the token speed, "choose the model by workload, then constrain it" becomes a measured claim rather than a slogan.
- **Cost view:** 27B answered 56 in 474 s of pass wall; 9B answered 20 in 115 s — per *answered question* the 27B costs 8.5 s, the 9B 5.8 s of GPU time, but 70 questions go unanswered. Without grammar the small model is cheaper per hit and useless per task.
- **Scheduler:** the MoE ran at 70 % GPU util with running 4.2 / 8 — the 3 B active path leaves the batch under-filled and the GPU under-driven; more concurrency (K) would pay off there, not for the dense models.
- **Draft-model speculation is not available on this stack** (see Log); MTP is the speculation to ship for Qwen3.6.
