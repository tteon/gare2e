---
id: 009
title: Stratified serving profile (easy / medium / hard) at the recommended engine shape
state: done
created: 2026-09-10
owner_intent: user
bead: seocho-ds17
---

## Intent (user)
"우선 우리가 모두를 돌려볼 수 없으니 easy, medium, hard 3개로 나누어서 진행하시죠" (2026-09-10). We cannot run the
full cohorts (90 GRBench single / 210 GTSQA single / 60 GTSQA multi); run the serving study per
difficulty stratum with the strata-v1 samples (10 cases per stratum per lane) instead.

## Acceptance (user; Claude may draft)
- [ ] For each stratum (easy, medium, hard): 4 lanes at K=1 and K=4 on the TP=2 monolith
      (session B's recommended shape), same frozen inputs, reasoning off, baseline prompt.
- [ ] Per-stratum serving profile (tokens, turns, decode/prefill split, per-request latency,
      throughput, energy) and funnel, side by side; K=4 vs K=1 gain per stratum.
- [ ] Medium reused from lab-s5 mono-k1/mono-k4 (same host class, P2P on) — no re-run.

## Constraints (user)
- Paid time bounded; reuse existing medium data.

## Feasibility (Claude)
- verdict: feasible
- basis: strata-v1 schedules carry all three strata; SEOCHO_MAIN_STRATUM filters; lab-s3 ran
  easy/medium/hard at K=1 (P2P off, --max-num-seqs 4) so this adds K=4 and a P2P-on baseline.
- alternatives: run all 3 strata fresh (+2 passes, +.5) for a single-session baseline.
- estimated cost/time: 4 passes (easy-k1, easy-k4, hard-k1, hard-k4) ≈ 2.2 h incl. bootstrap ≈ .5.

## Approval (user)
approved: yes  # chat 2026-09-10 "진행하시죠"; budget set by Claude from the estimate
budget_usd: 6

## Plan (Claude)
1. Queue session-C: easy-k1, easy-k4, hard-k1, hard-k4 (TP=2, --max-num-seqs 8, baseline prompt).
2. Bundle v32 (includes wrapper exit-policy fix), launcher lab-s7 (2×A100-80GB, cuda≥13, 3.5 h ceiling).
3. Analyse with summarize_agentic_os_queue_session.py + catalog; apply labels v1.1 post hoc.
4. Report: stratum × K table, publish.

## Log (Claude)
- 2026-09-10 10:35 KST — report published: https://claude.ai/code/artifact/cccc3c76-c591-48d5-9bd5-ba8c80e2b3b8 . Open follow-ups: validate the DB timeout+index fix live (one GRBench pass), repair-loop levers (007/006), spec parity study with a draft model (deferred).
- 2026-09-10 10:20 KST — lab-s7 complete: 6/6 passes exit 0, lifecycle state=complete (first session with the wrapper exit fix), $4.99, instance destroyed. Host 67231 is PCIe (PHB), P2P off by nccl_env auto — comparable to session A, not to lab-s5 NVLink; K=1/K=4 medium reused from lab-s5 (NVLink), which session B showed to be equivalent within ±3 %.
- 2026-09-10 07:45 KST — lab-s7 admitted at 07:42 (instance 50436838, host 67231, 2× A100 PCIe 80 GB, UK, $2.06/h, cuda 13.1, no NVLink) *before* my stop/relaunch for the queue change; the runner survived the unit stop (KillMode=process) and continues. Queue rewritten in place before staging uploaded it: easy-k1, hard-k1, easy-k4, hard-k4, easy-k1-spec, hard-k1-spec (medium-k1-spec dropped: identical to lab-s4 k1-spec). Remote timeout stays 12000 s, so hard-k1-spec may be cut if the session runs long. Slip: a failed rewrite briefly truncated the queue file; rebuilt within a minute, verified before upload.
- 2026-09-10 07:36 KST — launched lab-s7 (unit seocho-vast-lab-s7, bundle v32 incl. wrapper exit fix, queue session-C: easy-k1, easy-k4, hard-k1, hard-k4; 3.5 h ceiling ≈ $6.9 max, expected ≈ 2.2 h / $4.5).

## Result (Claude)
### Stratum × configuration (lab-s7 easy/hard; medium from lab-s5 mono and lab-s4 k1-spec)
Pass wall s (40 cases): easy 1410 / medium 2409 / hard 1850 at K=1; 540 / 831 / 738 at K=4 (2.6× / 2.9× / 2.5×); spec K=1: 734 / 1377 / 911.
GPU energy kJ per case: 12.6 / 24.6 / 20.2 at K=1 → 5.4 / 9.5 / 8.1 at K=4.
- **Difficulty barely changes single-agent serving profile.** Per-seq decode 44–46 tok/s everywhere; output tokens per case 380–441 (GRBench) / 486–604 (GTSQA). Easy is cheaper only because GTSQA-multi easy does not trigger retrieval (10 % triggered, 76 out tok/case, 2 s/case vs 57–64 s) — the retrieval-trigger problem from request 007, not a serving effect. Medium's longer wall comes from GRBench DB tail cases (p90 DB s/case 75 vs 7–16 for easy/hard).
- **K=4 gain is uniform across strata** (2.5–2.9×) with the funnel unchanged in every lane (GRBench single validated 1.0/1.0/0.9 at both K; GTSQA single 0.8–0.9; multi identical). Recommendation from session B holds for all strata.
- **Bottleneck by stratum (LLM / graph-DB share):** GRBench single easy 0.65/0.36, medium 0.26/0.74, hard 0.67/0.33 — the DB dominates only where the tail queries land (medium sample has 4 scans of 43–75 s); per-call p50 is 0.6–1.3 s in all strata. GTSQA single 0.99/0.00 everywhere; multi 0.92–0.99 LLM everywhere; framework residual ≤ 1 %. Hard raises retries+repairs (GRBench 4.0 → 4.8–5.2; GTSQA 4.0 → 5.1) and tool-call tokens (4.2 k → 5.8 k per 10 cases) rather than DB time.
- **Speculative decoding by stratum: parity fails in all strata.** GRBench single validated easy 1.0→0.9, medium 1.0→0.5, hard 0.9→0.8; non-empty easy 1.0→0.4; tool-failure rate 0.00–0.12 → 0.46–0.65. Decode is 63–102 tok/s (vs 45) and GRBench DB share drops because rejected queries never reach the DB — the wall gain (1.9–2.0×) is again mostly cases failing early. GTSQA single: validated unchanged, tool-failure 0.05–0.16 → 0.34–0.53. Multi: output collapses 2.5 k → 0.3–0.5 k tokens/case. Conclusion unchanged: not adoptable under greedy decoding for query-writing turns.
### Verdict on the user's question (where is the bottleneck)
Self-thought (Graph-CoT) is the bottleneck for GTSQA and every multi-agent lane (99 % LLM, 4–6 repair turns per case, ~2.6 k specialist tokens). The graph-database interface is the bottleneck only for GRBench single, and only through a tail of unbounded Cypher scans (fixed in gare2e 71b3b28c: real 15 s Bolt transaction timeout + lookup indexes; to be validated next session). The agent interface (tool-call JSON → validator → executor → EvidenceHandoff) costs ≤ 1 % everywhere.
### Bottleneck decomposition — preliminary (free; lab-s5 medium K=1, lab-s3 easy/medium/hard K=1)
Share of case wall (median case): LLM / graph-DB / framework residual
- GRBench single (DozerDB, 67 M rels): **0.26 / 0.74 / 0.00** at medium (lab-s5); lab-s3 easy 0.45/0.55, medium 0.58/0.42, hard 0.63/0.37. The graph database is the bottleneck, and it is a tail: 6 of 138 Cypher calls (> 5 s) are 68 % of all DB time, 4 calls of 43–50 s are 60 %. Those slow calls returned 1 row / ~20 bytes — correct-but-unbounded scans (count/aggregate or unindexed property lookup over 2.7 M nodes). Per-call p50 is 0.6–0.7 s. Self-thought is absent here: 60 reasoning tokens vs 3.6 k tool-call tokens per 10 cases; the model goes straight to Cypher.
- GTSQA single (Oxigraph in-process, small per-case graphs): **0.99 / 0.00 / 0.01**. Graph calls are 0.4–1 ms. The whole cost is LLM decode of the ReAct trace (5.5–7.7 k output tokens per 10 cases) and repair loops (4–5 query retries+repairs per case, 6 turns → turn budget exhausted). Bottleneck = self-thought + repair, i.e. the Graph-CoT loop itself.
- Multi-agent (both benchmarks): **0.99 / 0.00 / 0.01**. Handoff cost ≈ 1 ms of tool time; the manager→specialist step shows up only as time-to-first-graph-tool 2.5–4 s (vs 0.45–0.7 s single) — manager thinking + specialist prompt prefill. 5–6 retries+repairs per case on the specialist, 2.6 k output tokens per case. Bottleneck = specialist decode + repair loop; policy rejections (`query_policy_rejected`, 107/113 in session A) make the loop unproductive.
- Agent framework (Agents SDK, coordinator, HTTP, receipts): residual ≤ 1 % everywhere — not a bottleneck.
Stratum effect at K=1: GRBench hard shifts time from DB to LLM because more queries are rejected before execution (validated 100/91/80 % easy/medium/hard); GTSQA barely changes with stratum.
Levers implied: (a) DozerDB: per-query timeout (~5 s) + plan guard against full scans + indexes on lookup properties → removes 60–70 % of GRBench DB time; (b) Graph-CoT loop: cap repair attempts / make repairs productive (schema-aware repair, scope injection for multi) → the only lever for GTSQA and multi; (c) serving: K=4 batching stays the lever for LLM-bound lanes.

## Decision needed (Claude -> user)
