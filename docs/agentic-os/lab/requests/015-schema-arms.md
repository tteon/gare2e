---
id: 015
title: Schema context axis — B0 / S1 / S4 (+ C- control) on strata-v2 at the recommended serving shape
state: done
created: 2026-09-12
owner_intent: user
bead: seocho-wqt1
---

## Intent (user)
Chat 2026-09-11: schema 2×2 (scope full/relevant × semantics structural/ontology) — start with the three
existing cells B0 / S1 / S4; add "full + ontology" as a new arm later. Measure schema tokens → Cypher/SPARQL
validity → execution success → non-empty evidence → answer, per query class (L1/L2/L3 = easy/medium/hard).

## What the arms are (as frozen today)
| arm | context the agent gets | lanes | scope × semantics |
|---|---|---|---|
| B0 | the graph's own `inspect_graph_schema` answer, frozen per case (GRBench 62 tok; GTSQA ≈ 990 tok) | all four | full × structural |
| C- | irrelevant context of the same size as S1 (negative control; GRBench 160 tok, GTSQA ≈ 1 780 tok) | grbench-single/multi, gtsqa-single | control |
| S1 | SEOCHO schema slice for the case (GRBench 160 tok, GTSQA ≈ 1 780 tok) | grbench-single/multi, gtsqa-single | relevant × structural (+ slice semantics) |
| S4 | S1 context + SEOCHO semantic bundle, queries executed through the SEOCHO Oxigraph adapter with runtime policy | gtsqa-single, gtsqa-multi | relevant × ontology-enriched |
| S2a/S2b/S3 | transport/policy variants (not schema) | gtsqa-single | — |

Note: on GRBench the "full" physical schema is *smaller* than the relevant slice (few labels, many
properties elided), so the token-cost argument for "relevant" only holds on GTSQA; on GRBench the axis
tests semantics, not size. There is no S4 for GRBench (the Cypher runner accepts B0/C-/S1 only) — the
"relevant × ontology" cell on Cypher is the new arm to build, together with "full × ontology".

## Feasibility (Claude)
- verdict: feasible now for the cells that exist; no new context material is needed — the strata-v2
  serving caches already carry B0, C- and S1 responses per case (rebuilt on the appliance by the
  cache-serving step); S4 is in-process (SeochoOxigraphExecutor + policy check), no extra service.
- code: `SEOCHO_MAIN_ARMS` (gare2e a2c0e8c7) runs the main matrix per arm with suffixed run roots; the
  coordinator validates arms per lane (grbench: B0/C-/S1; gtsqa-single: all; gtsqa-multi: B0/S4) — an
  arm a lane does not support must be skipped, not fail the pass: **one e2e change** (skip + log) before
  launch, plus catalog/funnel columns for `arm` in the stratum script.
- metrics per arm × lane × L-class (N = 30): schema tokens (from the cache), validated, executed,
  non-empty, answered, policy-rejection codes, repairs, wall p50, prefill share (schema tokens land in
  prefill; lab-s11 prefill share is 1–11 %, so token cost will be visible but small).
- cost/time: K=8 on strata-v2 (all lanes): S1 ≈ 43 min, C- ≈ 43 min, S4 (two GTSQA lanes) ≈ 25 min,
  bootstrap ≈ 17 min → ≈ 2.2 h ≈ **$6** at $2.7/h. B0 reference = lab-s11 all-k8 (same cohort, bundle v40
  vs ≥ v42: the DB fixes are identical; only the arm loop and the service-name fix differ).
- risk: S1/S4 have never run at scale on the appliance (only lab-s3's PE gates ran B0); expect
  frozen-cache misses to surface as `frozen discovery cache lacks case` errors — the `--continue-on-error`
  path records them as failures without stopping the lane.

## Approval (user)
approved: yes   # chat 2026-09-12 "네"
budget_usd: 6

## Plan (Claude)
1. e2e: skip unsupported (lane, arm) pairs with a progress.tsv note; test.
2. Queue session-G on strata-v2: cache-serving → `arms-k8` with `SEOCHO_MAIN_ARMS="S1 C- S4"`
   (B0 from lab-s11) at K=8, max-num-seqs 8… or 32 if lab-s12 shows K=16 is safe.
3. Analysis: stratum funnel + turn summary grouped by arm; schema tokens joined from the cache; report
   page "Schema context axis".
4. Then the new arms (GRBench relevant × ontology; full × ontology on both) as request 016.

## Log (Claude)
- 2026-09-12 06:45 KST — done. Report https://claude.ai/code/artifact/d9af04dd-cc57-409e-8d9e-9a35f11f8441. Follow-up request 016 (missing 2×2 cells) drafted; 012 item 4 (seocho-7w0m) confirmed as the multi-agent blocker.
- 2026-09-12 06:30 KST — analysis by arm done; Result written; package uploaded (842 files, 15 MB, leak 0; index 4 runs). Report next.
- 2026-09-12 05:50 KST — lab-s13 complete (50622600, host 458442 NVLink pair, $2.30/h, 129 min ≈ $4.9): S1 / S4 / C- passes ran every supported lane (3 + 2 + 3 lane roots, suffixed); unsupported pairs skipped as designed. Defect: the skip guard as an `&&` list made the matrix return 1 when the last lane was skipped → pass exit 1 and wrapper exit 4 (data complete). Fixed with if/fi + `return 0`, shell tests. Analysis by arm running.
- 2026-09-12 00:40 KST — approved (≈ $6); bead seocho-wqt1. Runs as session-G after lab-s12, on an NVLink pair per the new host rule.
- 2026-09-12 00:20 KST — plan step 1 done: unsupported (lane, arm) pairs are skipped with a progress.tsv row (gare2e, 26 shell tests). SEOCHO_MAIN_ARMS landed (a2c0e8c7). Ready to queue session-G on approval.

## Result (Claude)
Report: https://claude.ai/code/artifact/d9af04dd-cc57-409e-8d9e-9a35f11f8441

### lab-s13 (2026-09-12, bundle v42, 2× A100 SXM4 NVLink on host 458442, K=8, reasoning off, strata-v2 N=30 per cell) — B0 reference = lab-s11 all-k8
Evidence: `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s13-try-034142/session-summary/` (arm-comparison, stratum-funnel, turn-summary, layer-shares, lane-*), `run-arms-*/catalog`, `run-arms-*/otel-stages`, serving caches `.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s13-try-034142/strata-*-discovery-serving.json` (vLLM tokenizer: GRBench B0 62 / S1 = C- 160 tokens; GTSQA B0 990 / S1 = C- 1 787); package `s3://…/traces/lab-s13-try-034142`.

| lane | stratum | arm | schema tokens | validated | non-empty | answered | budget-exhausted | wall p50 s | repairs/case |
|---|---|---|---|---|---|---|---|---|---|
| grbench-single | easy | **B0** (lab-s11 k8) | 62 | 30 | 24 | 7 | 20 | 15.8 | 1.0 |
| grbench-single | easy | S1 | 160 | 29 | 28 | 20 | 9 | 8.8 | 0.37 |
| grbench-single | easy | C- | 160 | 15 | 5 | 0 | 27 | 12.5 | 3.6 |
| grbench-single | medium | **B0** (lab-s11 k8) | 62 | 30 | 30 | 16 | 14 | 10.3 | 0.6 |
| grbench-single | medium | S1 | 160 | 30 | 30 | 29 | 1 | 6.2 | 0.13 |
| grbench-single | medium | C- | 160 | 21 | 18 | 2 | 28 | 12.4 | 3.8 |
| grbench-single | hard | **B0** (lab-s11 k8) | 62 | 28 | 23 | 3 | 27 | 15.9 | 0.87 |
| grbench-single | hard | S1 | 160 | 27 | 26 | 7 | 22 | 18.4 | 0.87 |
| grbench-single | hard | C- | 160 | 15 | 3 | 1 | 29 | 13.5 | 3.93 |
| grbench-multi | easy | **B0** (lab-s11 k8) | 62 | 3 | 0 | 0 | 0 | 73.4 | 2.7 |
| grbench-multi | easy | S1 | 160 | 22 | 0 | 0 | 0 | 113.1 | 2.33 |
| grbench-multi | easy | C- | 160 | 0 | 0 | 0 | 0 | 75.2 | 2.53 |
| grbench-multi | medium | **B0** (lab-s11 k8) | 62 | 6 | 0 | 0 | 0 | 76.3 | 2.6 |
| grbench-multi | medium | S1 | 160 | 16 | 0 | 0 | 0 | 81.0 | 2.5 |
| grbench-multi | medium | C- | 160 | 0 | 0 | 0 | 0 | 75.6 | 2.77 |
| grbench-multi | hard | **B0** (lab-s11 k8) | 62 | 2 | 0 | 0 | 0 | 73.0 | 2.83 |
| grbench-multi | hard | S1 | 160 | 18 | 0 | 0 | 0 | 76.8 | 2.57 |
| grbench-multi | hard | C- | 160 | 0 | 0 | 0 | 0 | 68.5 | 2.67 |
| gtsqa-single | easy | **B0** (lab-s11 k8) | 990 | 25 | 10 | 0 | 21 | 18.1 | 1.43 |
| gtsqa-single | easy | S1 | 1787 | 26 | 14 | 0 | 25 | 14.9 | 1.2 |
| gtsqa-single | easy | S4 | 1787 + bundle | 25 | 13 | 0 | 25 | 12.8 | 1.0 |
| gtsqa-single | easy | C- | 1787 | 20 | 12 | 0 | 17 | 11.9 | 0.6 |
| gtsqa-single | medium | **B0** (lab-s11 k8) | 990 | 27 | 15 | 0 | 28 | 16.5 | 1.03 |
| gtsqa-single | medium | S1 | 1787 | 25 | 7 | 0 | 28 | 14.2 | 1.03 |
| gtsqa-single | medium | S4 | 1787 + bundle | 28 | 7 | 0 | 28 | 15.0 | 0.9 |
| gtsqa-single | medium | C- | 1787 | 20 | 17 | 1 | 19 | 11.3 | 0.3 |
| gtsqa-single | hard | **B0** (lab-s11 k8) | 990 | 27 | 10 | 0 | 29 | 19.5 | 1.37 |
| gtsqa-single | hard | S1 | 1787 | 27 | 12 | 0 | 29 | 17.0 | 0.83 |
| gtsqa-single | hard | S4 | 1787 + bundle | 27 | 12 | 0 | 29 | 16.2 | 1.13 |
| gtsqa-single | hard | C- | 1787 | 17 | 15 | 0 | 18 | 17.1 | 0.83 |
| gtsqa-multi | easy | **B0** (lab-s11 k8) | 990 | 12 | 0 | 0 | 0 | 3.3 | 0.6 |
| gtsqa-multi | easy | S4 | 1787 + bundle | 13 | 0 | 0 | 11 | 6.0 | 0.2 |
| gtsqa-multi | medium | **B0** (lab-s11 k8) | 990 | 20 | 0 | 0 | 0 | 71.2 | 1.5 |
| gtsqa-multi | medium | S4 | 1787 + bundle | 17 | 0 | 0 | 24 | 71.1 | 1.5 |
| gtsqa-multi | hard | **B0** (lab-s11 k8) | 990 | 16 | 0 | 0 | 2 | 79.6 | 2.13 |
| gtsqa-multi | hard | S4 | 1787 + bundle | 6 | 0 | 0 | 29 | 85.2 | 2.67 |

**Readings**
- **The SEOCHO schema slice (S1) is the first treatment that moves the answer column.** GRBench single, same 90 questions, same model and serving: answered 7 / 16 / 3 (B0) → 20 / 29 / 7 (S1) for easy / medium / hard; budget exhaustion 20 / 14 / 27 → 9 / 1 / 22; non-empty 24 / 30 / 23 → 28 / 30 / 26; repairs per case 1.0 / 0.6 / 0.87 → 0.37 / 0.13 / 0.87; median wall 15.8 / 10.3 / 15.9 s → 8.8 / 6.2 / 18.4 s. Medium is essentially solved (29 / 30); hard doubles but stays budget-bound (22 / 30 exhausted). The extra 98 schema tokens cost nothing measurable (prefill share 0.05–0.09, unchanged).
- **The negative control behaves as a control.** C- (irrelevant context of S1's size): validated 15 / 21 / 15, answered 0 / 2 / 1, repairs 3.6–3.9 per case, `missing_grbench_node_label` 206 times — the model queries the labels the fake context named. So the S1 gain is the *content* of the slice, not the extra tokens or a prompt-shape effect.
- **GRBench multi: S1 fixes validation, not evidence.** Validated 3 / 6 / 2 → 22 / 16 / 18 (`missing_workspace_scope` 300 → 170), still 0 non-empty in every cell; C- 0 validated. The specialist now writes scoped Cypher but the manager–specialist loop still never turns rows into an answer — 012 item 4 stands.
- **GTSQA (SPARQL over Oxigraph) does not respond to any schema arm.** S1 / S4 vs B0: validated within ±3, non-empty 14 / 7 / 12 (S1) vs 10 / 15 / 10 (B0), answered 0 everywhere, exhaustion 25–29 / 30. S4's semantic bundle adds nothing over S1 (validated 25 / 28 / 27 vs 26 / 25 / 27; wall −2 to +1 s). GTSQA multi with S4: validated 13 / 17 / 6 vs 12 / 20 / 16 and 11–29 exhausted where B0 abstained — worse on hard. The GTSQA bottleneck is the reasoning loop (lab 013/014), and schema context is not where it is.
- **Serving is unchanged by the arm** (S1 175 tok/s, C- 176, S4 163 at K=8; decode share 0.82–0.90, host ≤ 0.02, graph ≤ 0.10). Note graph share 0.07–0.10 for S1 GRBench single: 13 calls hit the 15 s bound (of 262) — the better-anchored queries reach the expensive relationship patterns more often; the DB-side cost guard (012) moves up the list.
- **Lane exit codes:** S1 and C- passes exited 1 only because the skipped last lane (gtsqa-multi) set the matrix return status; the lifecycle read `failed` (wrapper 4) while every lane completed. Fixed (if/fi + `return 0`), and `run_lab_pass.sh` now exports OTel and writes its receipt even when the workload fails (it did not for these two passes; client spans were exported locally afterwards, joined 96–97 % to the vLLM spans).

## Decision needed (Claude -> user)
Approve ≈ $6 for session-G (S1 / C- / S4 at K=8 on strata-v2)? It can chain right after lab-s12's analysis.
