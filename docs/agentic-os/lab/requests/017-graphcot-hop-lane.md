---
id: 017
title: Graph-CoT hop-action lane vs iterative Text2Cypher on the same GRBench questions
state: done
created: 2026-09-12
owner_intent: user
bead: seocho-1i7q
---

## Intent (user)
Chat 2026-09-12: "안만든건 빨리 만들고 진행하세요 … 2번도 바로 진행하시구요" — build the original Graph-CoT action set (RetrieveNode / NodeFeature / NeighbourCheck / NodeDegree, one hop per call) as a lane over DozerDB and compare it with the iterative Text2Cypher lane on the same 90 questions, so the talk can say where each GraphCoT variant is lost (retrieval vs finalisation) and what it costs (turns, tokens, decode, DB calls).

## Design
- `src/seocho/eval/grbench_graphcot.py`: four hop actions as fixed, parameterised Cypher templates executed through the same `GRBenchDozerExecutor` boundary (validator, workspace scope, 15 s Bolt timeout, row/byte caps); RetrieveNode = exact name/title match with a CONTAINS fallback (TEXT index); NeighbourCheck capped by the row cap; NodeDegree exact.
- `scripts/benchmarks/run_grbench_graphcot_react.py`: single-agent ReAct loop with the four tools, up to 12 turns (hops need more than whole queries; same 16 384-token budget), several hops per turn allowed (≤ 4 calls). Receipts share the Text2Cypher case schema (turns, tools, funnel, answer_status) so every summary applies; `hops_by_tool` added.
- Arms: **B0** = tool signatures + node types / relation names + directions (hop tools cannot be called without the vocabulary — information-equivalent to Text2Cypher S1); **O1** = + relation meanings (lab 016 semantics).
- Lane `grbench-graphcot` in the coordinator (`_LANE_ARMS` B0/O1) and the e2e matrix, behind the new `SEOCHO_MAIN_LANES` filter (default = the historical four lanes, so nothing changes for old queues); `SEOCHO_GRAPHCOT_MAX_TURNS` (12).
- Funnel semantics for hop tools: validated = executed = at least one hop returned ok; non-empty = any hop returned rows; answered as before.

## Comparison (same 90 questions, K=8, strata-v2)
| lane | context | reference |
|---|---|---|
| Text2Cypher B0 / S1 / O1 | schema tool result | lab-s11 k8, lab-s13 S1, lab-s14/15 O1 |
| Graph-CoT B0 / O1 | prompt vocabulary (± semantics) | this request |
Metrics per L-class: funnel, answered, turns, hops per case, tokens in/out, wall, decode share, DB calls p50/p90, timeouts.

## Approval (user)
approved: yes   # chat 2026-09-12 "2번도 바로 진행하시구요"
budget_usd: 4   # two passes (B0, O1) on grbench-graphcot only ≈ 2 × 25 min + boot, on an NVLink pair ≤ $2.9/h; runs in the same session as 016 (session-I)

## Plan (Claude)
1. Code + tests (done: 61 passed across the touched suites). 2. Local rehearsal: throwaway DozerDB with a 6-node GRBench-shaped graph + scripted mock vLLM → runner receipt. 3. Bundle v44, queue session-I = cache-serving → arms-o1-k8 (single lanes, lab 016) → graphcot-b0-k8 → graphcot-o1-k8. 4. Launch when an NVLink pair ≤ $2.9 exists. 5. Report: Text2Cypher vs Graph-CoT per class.

## Log (Claude)
- 2026-09-12 11:40 KST — done; combined report "Hops vs Queries" (URL in Result).
- 2026-09-12 11:05 KST — lab-s16 (H100 pair, $5.07/h, 78 min ≈ $6.6): graphcot-o1 and graphcot-b0 complete (90 + 90 receipts, spans). The essentials-first collection saved the session; the bulk copy of private logs still failed on the UAE uplink and cost ≈ 50 paid minutes → next: `--collect-bulk off|essential-only` for slow hosts. Analysis by arm next.
- 2026-09-12 10:00 KST — salvage: graphcot-b0-k8 had 84/90 receipts (scp cut) and no spans (exported locally). Graph-CoT B0 answered 19/21/5 of 30/26/28 (easy/medium/hard), wall p50 6.1/3.5/7.2 s, 0 repairs, 0 policy rejections, DB p50 6 ms; 18 cases hit the token budget (hop results accumulate), 19 unsupported answers. graphcot-o1 lost. Runner collection hardened (essential archive first, 3600 s, retry; partial copy no longer fatal) — session-J = cache-serving → graphcot-o1 → graphcot-b0 (full 90) on lab-s16.
- 2026-09-12 09:40 KST — lab-s15 (H100 PCIe+NVLink pair, UAE, $4.27/h) ran the whole queue but the artifact download (`scp -r /artifacts`, 1800 s cap) timed out from the UAE host and the fail-closed runner destroyed the instance → results lost, ≈ $5.3. Fix before relaunch: compress on the appliance, copy the small content-free receipts first, retry, and never destroy before an essential copy is verified.
- 2026-09-12 08:25 KST — no A100 NVLink pair ≤ $2.9 on the market; user allowed H100/H200. Admission widened: GPU RAM floor 163 840 → 160 000 MB (H100 80 GB pairs report slightly under 2×81 920) and ceiling $4.5/h → the H100 PCIe + NVLink pair on host 598051 ($4.27/h) qualifies. Session ≈ 2 h ≈ $8.5 (016 + 017 budgets $9). Relaunched.
- 2026-09-12 08:30 KST — code written and tested (templates pass the read policy; dispatch, fallback, funnel, coordinator, e2e lane filter). Rehearsal on a throwaway DozerDB (6 nodes, 18 rels) with a scripted mock vLLM: RetrieveNode → NeighbourCheck → NodeDegree → answer; receipt terminal=final, answered, funnel graph_query_calls 3, non-empty, manifest lane GRBench-GraphCoT. Bundle v44; session-I queue; launcher lab-s15 (NVLink ≤ $2.9). Waiting for an admissible host.

## Result (Claude)
Report: https://claude.ai/code/artifact/6853722a-06db-4090-8074-5f184cee079d

### lab-s15 (salvage) + lab-s16, 2026-09-12 — same 90 GRBench questions, K=8, reasoning off, strata-v2 N=30 per class
Evidence: `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s16-try-094447/session-summary/` (arm-lane-comparison, stratum-funnel, turn-summary, layer-shares, lane-client, pass-serving), `run-graphcot-*/catalog`, `run-graphcot-*/otel-stages` (498 / 512 requests, 100 % joined to vLLM spans); lab-s15 salvage for Text2Cypher O1; packages `s3://…/traces/lab-s15-try-082017`, `lab-s16-try-094447`.

| stratum | variant | N | validated | non-empty | answered | budget-exhausted | wall p50 s | repairs/case |
|---|---|---|---|---|---|---|---|---|
| easy | Text2Cypher B0 (lab-s11 k8) | 30 | 30 | 24 | 7 | 20 | 15.8 | 1.0 |
| easy | Text2Cypher S1 (lab-s13) | 30 | 29 | 28 | 20 | 9 | 8.8 | 0.37 |
| easy | Text2Cypher O1 (lab-s15) | 30 | 30 | 27 | 17 | 12 | 9.7 | 0.13 |
| easy | Graph-CoT hops B0 (lab-s16) | 30 | 30 | 27 | 19 | 8 | 7.0 | 0.0 |
| easy | Graph-CoT hops O1 (lab-s16) | 30 | 30 | 24 | 19 | 6 | 4.9 | 0.0 |
| medium | Text2Cypher B0 (lab-s11 k8) | 30 | 30 | 30 | 16 | 14 | 10.3 | 0.6 |
| medium | Text2Cypher S1 (lab-s13) | 30 | 30 | 30 | 29 | 1 | 6.2 | 0.13 |
| medium | Text2Cypher O1 (lab-s15) | 30 | 30 | 30 | 24 | 6 | 5.0 | 0.7 |
| medium | Graph-CoT hops B0 (lab-s16) | 30 | 30 | 26 | 23 | 3 | 3.9 | 0.0 |
| medium | Graph-CoT hops O1 (lab-s16) | 30 | 30 | 25 | 22 | 3 | 3.9 | 0.0 |
| hard | Text2Cypher B0 (lab-s11 k8) | 30 | 28 | 23 | 3 | 27 | 15.9 | 0.87 |
| hard | Text2Cypher S1 (lab-s13) | 30 | 27 | 26 | 7 | 22 | 18.4 | 0.87 |
| hard | Text2Cypher O1 (lab-s15) | 30 | 30 | 23 | 9 | 13 | 11.9 | 0.27 |
| hard | Graph-CoT hops B0 (lab-s16) | 30 | 30 | 16 | 5 | 12 | 7.7 | 0.0 |
| hard | Graph-CoT hops O1 (lab-s16) | 30 | 30 | 20 | 6 | 15 | 8.9 | 0.0 |

**Readings**
- **The hop lane reaches the Text2Cypher-S1 answer level without writing a query.** Graph-CoT B0 / O1: 47 / 47 answered of 90 (Text2Cypher S1 56, O1 50, B0 26), at a median wall of 3.9–8.9 s versus 5–18 s, with 0 repairs and 0 policy rejections (fixed templates cannot be rejected) and DB calls at p50 4–6 ms / max 208 ms.
- **Where the hop lane is lost is different — and partly our accounting.** 21 / 24 cases stopped on the 16 384-token *cumulative* budget after ~10 turns (prompt re-sent every turn; input p50 4.5–5.9 k per turn), and 21 / 22 of them already held non-empty rows; 20 / 19 gave an unsupported answer (no rows) — 13 / 9 of those hard. Text2Cypher spends its budget on repairs; the hop lane spends it on turns. The same cumulative budget therefore penalises the hop lane: a fairer comparison uses a per-turn or per-case *fresh-context* budget (follow-up).
- **The model uses RetrieveNode as a search engine, not as an anchor.** 406 / 387 RetrieveNode calls vs 60 / 63 NeighbourCheck, 10 / 11 NodeDegree, 2 / 8 NodeFeature; 37 / 38 cases never left RetrieveNode, and 79–82 % of RetrieveNode calls fell back to the CONTAINS match because the model paraphrases titles and names. Exact-match anchoring — the assumption behind the paper's action set and behind our O1 semantics — rarely happens with this model.
- **O1 semantics change nothing here either** (47 vs 47 answered; hard non-empty 16 → 20, answered 5 → 6): relation meanings in the prompt do not alter how the model walks.
- **Serving profile of the hop lane:** 168–174 output tokens per case (Text2Cypher 326–430), 5–6 requests per case, request latency p50 0.96 s, decode share 0.77–0.87 with prefill 0.09–0.19 (more context per turn, 68–72 % cached), host 0.03, graph 0.003–0.008, agent 0. Still decode-bound; prefill is 2× the whole-query lane's share.
- **Per class:** easy 19 / 19 (S1 20), medium 23 / 22 (S1 29), hard 5 / 6 (S1 7, O1 9). Medium is where whole-query wins (aggregation / count questions need one Cypher, several hops); hard is lost by everyone, for different reasons (Text2Cypher: finalisation with rows in hand; hops: budget and unsupported answers).

## Decision needed (Claude -> user)
None.
