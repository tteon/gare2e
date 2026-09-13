---
id: 014
title: Experiment 0 — observability baseline for the GraphCoT talk (3-layer trace, turn-level telemetry, tool-use gap, decode ratio by query class)
state: done
created: 2026-09-11
owner_intent: user
bead: seocho-ezdh
---

## Intent (user)
Chat 2026-09-11: build the instrumentation before optimising — explain "why is it slow" per query and per
reasoning turn. Three layers (task/semantic, GraphCoT pipeline, LLM/system) joined by request_id + turn_id;
query classes L1/L2/L3 with structural metadata; schema strategy as a 2×2 (scope: full/relevant × semantics:
structural/ontology); single orchestrator with adaptive depth instead of multi-agent by default; decode ratio
= T_decode / T_inference and Σ T_decode / E2E; tool-use gap = T_parse + T_graph + T_serialize + T_prompt_build
+ T_tokenize + T_scheduler_wait; concurrency sweep; result-size sweep; sync vs async scheduling; the
12-metric baseline per query with speculative decoding and LMCache off.

## What already exists (lab-s3 … lab-s11 evidence)
| proposed | present today | evidence |
|---|---|---|
| request_id + turn_id joining three layers | trace_id = sha256(run_id, case_id); span tree case > chat (per turn) > execute_tool > neo4j/oxigraph query; traceparent to vLLM; 99.3 % client↔server join | lab-s11 `run-all-k4/otel-stages/`, `otel/collector-spans.jsonl` |
| L1/L2/L3 with hops / aggregation / filter / multi-turn metadata | easy/medium/hard from the structural rubric (hops, anchors, patterns, aggregation, value filter, argmax) computed from gold query / template, never from behaviour; `*-difficulty.jsonl` carries every column | `label_agentic_os_difficulty.py`, strata-v2 |
| per-turn telemetry (context tokens, cached, output, latency, tool gap) | single-agent receipts: `turns[]` (input/cached/output/thinking/tool-call tokens, latency, started_elapsed_ms, span_id), `tools[]` (latency, rows, bytes, outcome, argument-decode ms); multi-agent: `events[]`, `llm_requests[]` | lab-s11 run-receipt.json / receipts.jsonl |
| T_queue / T_prefill / T_decode per request; decode ratio | vLLM spans give queue, TTFT, prefill, decode per request; `lane-stages.md` has decode share of wall (0.84 single … 0.98 multi), prefill share, client-minus-server (3 ms) | `summarize_agentic_os_otel_stages.py` |
| tool-use gap | `gap_before_s` per request (p50 4–7 ms, p90 25–200 ms) and, from receipts, gap between turns split into graph-tool time vs residual | this file, table below |
| concurrency sweep | K=1/4/8 done (lab-s4/s7/s11): K=4 2.5–3.0× wall gain, K=8 +68 % tok/s at +11–15 % per-case wall; funnel unchanged | 008/009/013 |
| speculative decoding | ngram spec: +47–70 % decode at K=1, but greedy Cypher/SPARQL changed (validated 100 → 50 %) → parity failure, not adopted | lab-s4/s7 |
| LMCache | impossible for Qwen3.6-27B (hybrid KV specs) | lab-s5 |
| schema strategy | arms: B0 = model discovers schema via `inspect_graph_schema` (full, structural); C- = irrelevant-context control; S1 = SEOCHO schema slice (relevant, structural); S4 = SEOCHO semantic bundle (relevant, ontology-enriched); S2a/S2b/S3 = transport/policy variants. Only B0 has been run at scale. | `discovery_cache.py`, `agentic_os_treatments.py` |
| single orchestrator vs multi-agent | both lanes exist (single ReAct runner; manager + query specialist). Multi costs 5× wall and 6× output tokens for 0 non-empty results in every stratum | lab-s11 funnel |

### Turn-level table from lab-s11 (K=4, B0, all strata) — already computable, no new run
GRBench single (Text2Cypher): turn 1 in 736 tok / out 18 / 0.52 s → turn 2 833 / 96 / 2.27 s → turn 3 1022 (793 cached) / 96 / 2.35 s → turn 6 1894 (1189 cached) / 109 / 2.61 s. Latency tracks output tokens (≈ 23 ms/token = ITL), not context: prefix caching keeps prefill flat while context doubles.
GTSQA single (Text2SPARQL): turn 1 598 / 73 / 1.24 s → turn 2 1760 / 186 / 4.04 s → turn 6 3620 (2211 cached) / 107 / 2.16 s.
Tool-use gap between consecutive turns (GRBench single): easy 10 ms p50 / 85 p90, medium 7 / 19, hard 17 / 28 — and 100 % of it is the graph call; parse + serialise + prompt build + client residual = 0 ms at p90. GTSQA (Oxigraph in-process): 0 ms p50 / 3–5 ms p90.

## Feasibility (Claude)
- verdict: the observability baseline the proposal asks for is ~80 % built; the remaining 20 % is (a) a **turn-level summary table** as a first-class artifact (script over existing receipts, free), (b) **host-side stages inside vLLM** (tokenise / detokenise / scheduler wait are not separated in the vLLM spans we get — only queue, TTFT, prefill, decode; needs vLLM detailed-trace stages or a CPU-side sampler), (c) **result-size and concurrency ≥ 16 sweeps** (paid), (d) the **schema 2×2** needs one new arm (full + ontology-enriched) — B0/S1/S4 cover three cells.
- **The CPU-orchestration hypothesis is already answered negatively for this stack at K ≤ 8:** framework residual 0.000–0.008 of wall, client−server 3 ms, tool gap ≤ 17 ms p50, queue ≈ 0, GPU util 91–93 %. Decode is 84–98 % of wall. Where it could still appear: K ≥ 16 (never run), CPU-starved hosts (ours had 24–32 effective cores), large tool results (GRBench rows are capped; result_bytes p50 small), and the multi-agent boundary — but lab-s11 shows the multi-agent penalty is 6× output tokens (manager + specialist self-talk), not boundary crossings (gap_before p90 0.2 s of a 66 s case).
- cost/time: free part 0.5 day (turn-level + decode-ratio + tool-gap scripts over lab-s11, and a "Experiment 0" report page). Paid part one session ≈ 2.5 h ≈ $6–7: concurrency K=1/16/32 at the same 360 cases (+ `--async-scheduling` on/off at K=16), a result-size sweep by `--row-cap` 10/100/1000 on GRBench single, vLLM detailed traces on. Schema 2×2 (B0/S1/S4 + new arm) is a separate request (needs the S1/S4 discovery caches re-tokenised and the new arm implemented).
- alternatives: rename easy/medium/hard → L1/L2/L3 in the talk only (same rubric); skip async-scheduling if the vLLM 0.27.1 image lacks the flag.

## Approval (user)
approved: yes   # chat 2026-09-11 "네 진행하시죠. 유료 sweep 승인, 이름만 바꾸고 B0/S1/S4 먼저"
budget_usd: 7

## Plan (Claude)
1. Free: `summarize_agentic_os_turns.py` (per pass × lane × stratum × turn: context/cached/output tokens, latency, tool gap split graph vs residual), `decode ratio` columns (per request and Σ decode / E2E) added to `summarize_agentic_os_otel_stages.py`, stacked Graph / Agent / Host / GPU share table by stratum; Experiment-0 report from lab-s11 data.
2. Paid (after approval): queue session-F on strata-v2 — k1, k16, k32, k16-async; row-cap 10/1000 at k4 for GRBench single; detailed vLLM traces; same bundle line (≥ v41 with the service-name fix).
3. Decision items below.

## Log (Claude)
- 2026-09-12 04:25 KST — done. Report https://claude.ai/code/artifact/408db85a-26ac-4341-ac61-985310a48b70. Service-name fix validated live on lab-s12 (client spans = seocho-agentic-os). Follow-ups: 011 per-process vLLM CPU split; 012 DB-side query cost guard (timeouts 3→9 per pass at K≥16).
- 2026-09-12 04:10 KST — analysis done (catalogs, session summary, otel stages, turns/layer shares, stratum funnel, host-process CPU); Result written; package uploaded (1552 files, 24 MB, leak 0), manifest/index in gare2e d9bf854d. Report next.
- 2026-09-12 03:40 KST — lab-s12 try 3 complete on host 458442 (A100 SXM4 NVLink, $2.30/h, disk 3 TB — the earlier $2.30 listing with 53 GB disk was a different machine on the same host id), 00:47–03:37, ≈ $6.5. All five queue items exit 0; NCCL P2P enabled on NVLink (topology gate). Analysis running.
- 2026-09-12 00:50 KST — user allowed host 458442 (A100 SXM4 NVLink, $2.30/h; V14 NCCL hang was on this host id, nccl_env.sh now gates P2P by topology). Exclusion removed; try 3 relaunched with the NVLink filter.
- 2026-09-12 00:25 KST — try 2 (50604096, RTX PRO 6000 Max-Q pair, Romania) aborted at boot (≈ $1.1): user rule from now on = 2+ GPUs joined by NVLink only. Runner gains --require-nvlink (bw_nvlink > 0, recorded in the offer receipt); launcher query adds bw_nvlink>0 and drops RTX PRO 6000. Market at 00:20: NVLink pairs ≤ $2.9/h exist only on host 458442 (A100 SXM4, $2.30, excluded since V14); next are H100 PCIe+NVLink pairs at ≥ $4.27/h.
- 2026-09-12 00:00 KST — lab-s12 try 1 (50601901, Czechia host 258806, $2.27/h) failed at startup: SSH endpoint never ready in 900 s; destroyed + verified, ≈ $0.95. Relaunched as try 2 with host 258806 excluded.
- 2026-09-11 23:34 KST — free part done: summarize_agentic_os_turns.py → lab-s11 session-summary/turn-summary.md + layer-shares.md (decode 0.80–0.98 of wall in all 24 cells; host 0.2–8.6 %; agent ≤ 0.7 %; graph ≤ 2.7 %; turn latency tracks output tokens, tool gap 7–21 ms all graph). Result-size axis added (SEOCHO_GRBENCH_ROW_CAP/BYTE_CAP → --row-cap/--byte-cap; 37 tests). Bundle v41 (bfb94e9f…, also carries the service-name fix). lab-s12 launched (unit seocho-vast-lab-s12, label seocho-agentic-os-lab-s12): queue session-F = cache-serving → all-k16 (max-num-seqs 32) → all-k32 (64) → medium-k4-rowcap100 (byte cap 60000) → all-k16-noasync (--no-async-scheduling, last so a start failure costs one pass); ceiling 10800 s / inner 10200 s, ≤ $2.9/h. gare2e 58cff4d1.
- 2026-09-11 13:10 KST — approved: paid sweep ≈ $7; L1/L2/L3 = easy/medium/hard (rename only); schema 2×2 starts with B0/S1/S4 (separate request later). Bead seocho-ezdh.
- 2026-09-11 13:00 KST — drafted from chat; turn-level and tool-gap numbers computed from lab-s11 receipts (this file).

## Result (Claude)
Report: https://claude.ai/code/artifact/408db85a-26ac-4341-ac61-985310a48b70

### lab-s12 (2026-09-12, bundle v41, 2× A100 SXM4 NVLink NV12 on host 458442, TP=2, reasoning off, B0, strata-v2) + lab-s11 (K=4/8, RTX PRO 6000 PCIe pair)
Evidence: `runs/lab-s12-try-004752/session-summary/` (pass-serving, lane-client, lane-bottleneck, lane-funnel, stratum-funnel, turn-summary, layer-shares), `run-*/catalog`, `run-*/otel-stages`, `run-*/workload/host-process.csv`; package `s3://…/traces/lab-s12-try-004752`.

**Concurrency sweep (same 360 B0 cases; K=4/8 from lab-s11 on a PCIe pair, K=16/32 on the NVLink pair — engine-side numbers comparable, cross-host caveat noted):**
| K | pass wall s | gen tok/s | ITL ms | queue s/req | KV max % | GPU util % | energy kJ | grbench-single wall p50 s | grbench-multi wall p50 s | vLLM host CPU (cores busy) |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 4283 | 126.8 | 25.2 | 0.002 | 1.7 | 93.0 | 2470 | 12.2 | 66.2 | 3.0 |
| 8 | 2559 | 212.8 | 29.0 | 0.003 | 2.8 | 90.8 | 1477 | 13.9 | 73.4 | 3.0 |
| 16 | 2179 | 284.1 | 40.0 | 0.021 | 5.8 | 72.5 | 1038 | 16.8 | 120.6 | 3.0 |
| 32 | 1681 | 376.6 | 58.5 | 0.067 | 13.9 | 66.1 | 739 | 23.5 | 213.7 | 2.9 |
| 16, `--no-async-scheduling` | 2201 | 271.4 | 42.8 | 0.025 | 5.8 | 68.2 | 1004 | 18.4 | 124.7 | 3.0 |

- Throughput keeps rising to K=32 (3.0× K=4) while per-case wall doubles (grbench-single 12 → 23 s) and multi-agent cases triple (66 → 214 s): the batch is never full (KV ≤ 14 %), so every added agent pays in latency what the pool gains in throughput.
- **Host-side stays small at every K.** Layer shares (single lanes): decode 0.82–0.93, prefill 0.04–0.09, host (vLLM queue + unaccounted + client−server) 0.01–0.09 (max at K=32 grbench-single L1), agent residual 0.000–0.011, graph 0.00–0.08. vLLM's host processes use a flat ≈ 3.0 CPU cores from K=4 to K=32 (of 32 or 128), the agents 0.3 → 1.6 cores, DozerDB ≤ 0.1. The CPU-underprovisioning hypothesis is not supported in this stack; whether the flat 3 cores are saturated single threads or idle spin needs a per-process breakdown (011 item).
- **GPU utilisation falls with K (93 → 66 %) although throughput rises** — the idle comes from decode being memory-bound at small batch and from agents leaving the batch during tool calls, not from CPU contention (queue ≤ 0.07 s/req).
- **Async scheduling** (vLLM 0.27.1 default on): turning it off costs 4.5 % tok/s, +7 % ITL, +7–10 % per-case wall at K=16. Small but consistent; keep the default.
- **DB tail returns under contention:** GRBench single execute_cypher p50/p90 16/35 ms at K=4 → 38/99 ms at K=16 → 7/48 ms at K=32; calls hitting the 15 s bound 3 (K=4) → 9 (K=16) → 8 (K=32) of ≈ 360. The timeout does its job, but 8–9 cases per pass now lose one attempt to a scan that DozerDB cannot finish while 16–32 agents share it — graph share reaches 0.08 in L3. Room for the DB-side guard (query cost limit) from 012.
- **Result size (row cap 20 → 100, byte cap 12 k → 60 k, medium stratum, K=4):** grbench-single validated 30/30, non-empty 30/30 (unchanged), answered 17 vs 16, wall p50 9.9 vs 8.9 s, DB p50 13 ms, no timeouts; output tokens per case 371 vs 428. Result size is not a lever at these caps; the 60 kB cap is never reached.
- **Funnel is invariant to serving shape:** every funnel column within ±3 cases per cell across K=4/8/16/32 and async on/off (stratum-funnel.md).

**Turn level (grbench-single L3):** turn latency p50 2.4–2.8 s at K=4 → 3.2–4.1 s at K=16 → 5.1–5.6 s at K=32 for the same 100–140 output tokens; input grows 753 → 1 724 tokens over six turns with 60–70 % cached. Tool-use gap p50 4–49 ms, always the graph call; residual 0 at p90 (the p90 gap of 15 s at turns 3–4 is the Bolt timeout, not orchestration).

**Decode ratio for the talk:** decode / inference per request p50 0.90–0.96; Σ decode / E2E 0.84–0.95 (single lanes) and 0.98 (multi lanes) — in every L-class, at every K. "GraphCoT is decode-bound" holds; the heterogeneous-pipeline story does not appear at K ≤ 32 on this stack.

## Decision needed (Claude -> user)
1. Approve the paid concurrency/result-size/async session (≈ $7)? The free part starts regardless.
2. Schema 2×2: run B0 / S1 / S4 as the three existing cells now, and add "full + ontology" as a new arm later — or wait for all four?
3. Keep our structural rubric as the L1/L2/L3 definition (rename only), or re-label with the proposal's coarser criteria (1-hop / 2–3 hop + filter / multi-turn)?
