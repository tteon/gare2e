---
id: 021
title: Agent ↔ graph database interface — where tool calls spend time in DozerDB, fetch patterns, and hints / entity pre-information
state: approved
created: 2026-09-12
owner_intent: user
bead: seocho-qenu
---

## Intent (user)
Chat 2026-09-12: measure the bottleneck between the agent's tool calls and the graph database (neo4j / DozerDB), understand how it fetches data for our query patterns, and design hints and entity pre-information that help.

## What the receipts already show (free, 2026-09-12; per successful execute_cypher call)
| stage (client-measured) | T2C-B0 lab-s11 (307 calls) p50 / p90 | T2C-S1 lab-s13 (219 calls) p50 / p90 |
|---|---|---|
| argument decode + validator + pool acquire | < 0.1 / 0.4 ms | < 0.2 / 0.6 ms |
| Bolt submit (until the server has the result available) | 2.5 / 15.9 ms | **48 / 107 ms** |
| server `result_available_after` | 1 / 15 ms | **43 / 101 ms** |
| first record → hydrate (streaming rows) | 0.8 / 1.6 ms (max 379) | 2.5 / 11 ms (max **16 020**) |
| serialise to the model | 0.03 / 0.05 ms | 0.07 / 0.1 ms |
| total tool latency | 3.5 / 18 ms | 53 / 146 ms (6 calls > 1 s, all 0 rows) |
| rows | p50 1, p90 10; 77 / 307 calls return 0 rows | p50 1, p90 10; 58 / 219 zero-row |
- The client side of the tool path is negligible (< 0.5 ms). Two server-side patterns cost: (1) **name/title predicates the S1 vocabulary encourages** — `result_available_after` jumps from 1 ms (B0's id-anchored queries) to 43 ms p50 / 101 ms p90 (S1's `feature_name = …` / `CONTAINS`), i.e. the anchor lookup itself, on 1.39 M author / 1.37 M paper nodes; (2) the residual **scan tail** (hydrate up to 16 s = the 15 s Bolt bound) on zero-row queries that reach an unanchored relationship pattern despite the static guard.
- 25–27 % of executed queries return 0 rows: each is a full round trip plus a repair turn (2–3 s of decode) — the DB's cheapest failure is the agent's most expensive one.

## Questions to answer next (needs PROFILE plans; the runner has `--profile-query-plans`, never enabled in a lab pass)
1. Which operators dominate: NodeIndexSeek (id / name TEXT index) vs NodeByLabelScan vs Expand(All) into hub nodes; db hits per pattern family.
2. Are the 43 ms name lookups index seeks (then the cost is the TEXT index / lower-casing) or label scans with a filter (then a hint or a normalised property fixes it)?
3. How much of the zero-row rate is name mismatch (paraphrased title / name) vs genuinely empty relationships.

## Design candidates (to test)
| candidate | mechanism | expected effect |
|---|---|---|
| **entity pre-resolution** (from lab 018 / 019) | resolve anchors to node ids *before* the LLM runs (RetrieveNode template incl. CONTAINS fallback) and hand the ids to the agent in the context | removes the 43–101 ms name lookups from every query, removes most name-mismatch zero-row queries, lets the validator require `id:` anchors |
| **index-aware hints** | prompt idiom + validator: `USING INDEX` / `USING TEXT INDEX` on the anchor, forbid `CONTAINS` on unindexed properties, reject `toLower()` on indexed properties | seeks instead of filtered scans |
| **prepared templates for the 12 families** | the router's plan selects a parameterised statement; the LLM fills parameters only (the hop lane already shows templates never time out) | 0 policy rejections, sub-10 ms calls; loses generality — measure on the 50 held-out questions |
| **DB-side guards** | `db.transaction.timeout` 5 s for the agent role, `cypher.planner` hints, result row limit at the server | the K ≥ 16 timeout tail (lab-s12: 3 → 9 per pass) |
| **result shaping** | return `id, node_type, name` only; drop abstracts unless asked; cap 20 → measured 12 kB never reached | fewer tokens per tool result (prefill share) |

## Feasibility (Claude)
- verdict: one measurement pass first (free of new code): rerun grbench-single S1 at K=8 with `--profile-query-plans` on (30 medium cases ≈ 6 min) → operator / db-hit table per family; then entity pre-resolution as the first design (it is also the router's anchor step, so 019 and 021 share the module). ≈ $3 on an H100 pair, or fold into the 020 session A instance.
- risk: PROFILE replays run after timing (already isolated in the runner), so they add wall to the pass but not to the measured case.

## Approval (user)
approved: yes   # chat 2026-09-12 "권고 순서대로 진행하시죠. 021 PROFILE부터 승인"
budget_usd: 3
step2_approved: yes   # chat 2026-09-13 "네 다음 승인 합니다." — request_analysis module + zero-row diagnostics hint, A/B ≈ $2
step2_budget_usd: 3

## Plan (Claude)
1. PROFILE pass (S1, medium, K=8) → operator table by family. 2. Entity pre-resolution module (shared with 019) + validator `id:` anchor mode; A/B on the 90 questions. 3. Hints / templates / server guards as data indicates.

## Log (Claude)
- 2026-09-13 12:50 KST — report page "Faster Tokens, Fewer Tokens" (labs 019–022, Korean): https://claude.ai/code/artifact/30a1e812-1e31-4426-8fd9-ba71bfd82189
- 2026-09-13 09:05 KST — **lab-s18 done** (instance 50820190, host 458442 A100 SXM4 NVLink, $3.30/h, ≈ 30 min ≈ $1.7): cache-serving 15 s, hint-off 461 s, hint-on 308 s, all exit 0; essential-only collection (2 020 files verified); destroyed + verified. Package `s3://…/traces/lab-s18-try-083420` (218 files, 3.0 MB; index 8 runs). Tables `runs/lab-s18-try-083420/session-summary/zero-row-ab.md`.
- 2026-09-13 08:34 KST — **lab-s18 launched** (unit seocho-vast-lab-s18, bundle v46 sha 0739872c…, queue session-L: cache-serving → hint-off-s1-k8 → hint-on-s1-k8; grbench-single, S1, all 90 questions, K=8, NVLink pair ≤ $5.5/h). Built (free, gare2e 4afb2c6b): `src/seocho/eval/request_analysis.py` (anchor-literal extraction from Cypher; exact → CONTAINS → case-fold resolution; per-relation outgoing degree; degree classes low < 20 / mid / hub ≥ 200; `zero_row_diagnostic()`; question mentions + `analyze_request()` for 019), `GRBenchDozerExecutor(zero_row_hint=True)` appends `{"rows": [], "diagnostic": {anchors[...match, candidates, degree_out, degree_class], hint}}` to an empty result and a content-free `zero_row_diagnostic` receipt block (counts, match kinds, degree classes, reads, hint_ms); runner `--zero-row-hint`, schedule forwarding (grbench-single only), e2e `SEOCHO_ZERO_ROW_HINT=1`. 19 new/updated tests + 78 passing across the touched files; live rehearsal on a throwaway DozerDB (tiny graph) exercised resolved / unresolved / title-case / numeric-id / non-empty paths. Bead seocho-5epj.
- 2026-09-13 08:20 KST — step 2 approved by the user ("네 다음 승인 합니다").
- 2026-09-12 14:50 KST — lab-s17 done (A100 SXM4 NVLink pair back at $3.91/h, 30 min ≈ $2.0): 317 PROFILE replays (S1 all strata 228, B0 medium 89), 0 replay errors; essential-only collection worked (1 420 files, minutes). Operator table in session-summary/query-plans.md.
- 2026-09-12 12:30 KST — approved (PROFILE pass first); bead seocho-qenu. Order agreed: 021 PROFILE → entity pre-resolution (shared 019/021) → 020 session A → 019 router evaluation.
- 2026-09-12 12:10 KST — drafted from chat; Bolt stage decomposition computed from lab-s11/s13 receipts.

## Result (Claude)
### PROFILE pass (lab-s17, 2026-09-12): 317 plan replays, S1 all strata (228) + B0 medium (89)
Evidence: `runs/lab-s17-try-141724/session-summary/query-plans.{md,csv}`, receipts `cases[].query_plan_replays[]`; package `s3://…/traces/lab-s17-try-141724`.

**How DozerDB fetches our queries**
- Every plan anchors through an index: NodeIndexSeek 76 % (id / feature_name / feature_title equality), NodeIndexContainsScan 24 % (CONTAINS on the TEXT indexes). **No NodeByLabelScan, no AllNodesScan** — the static anchor guard has removed the scan class entirely.
- Page-cache misses: 0 in 317 replays (the 24 GB page cache holds the working set). db hits p50 13–90, p90 250–925, worst 6 525 (collaborators_in_year). Server `result_available` p50 11–19 ms, p90 17–33 ms per replay.
- The planner flags `cardinality_misestimation` on 60 % of plans and `eager_aggregation` on the count/argmax families — real, but at these row counts they cost single-digit milliseconds.
- Expand share 0.3 (B0) → 0.7 (S1 hard): S1 queries walk one or two relationships from the anchor; B0's medium queries were mostly two-anchor intersections.

**Where the 43 ms went (lab-s13 S1) and why it is not the DB:** the PROFILE replays of the same queries run serially at 11–19 ms; the 43 / 101 ms seen in-pass came from 8 concurrent agents sharing one DozerDB plus the CONTAINS scans — contention, not plan shape. Total DB time per case stays ≤ 0.1 of wall.

**Zero-row queries (the agent's expensive failure): not the anchor.** S1: 58 / 228 replays (25 %) returned 0 rows; in 52 of them the anchor operator *did* match rows and the pattern after it produced nothing — wrong relation name / direction / second hop; only 6 were true anchor misses. B0 medium: 5 / 89 zero-row (4 anchor misses). By family: closest_collaborator 12 / 32, collaborators_in_year 10 / 27, most_cited_paper_of_author 10 / 17, venue_of_paper 7 / 33 — the argmax / multi-hop families.

**Consequence for the design**
| candidate | verdict from the data |
|---|---|
| entity pre-resolution | still useful for the router (anchor type / degree) and it removes the 24 % CONTAINS scans, but it would fix only 6 / 58 zero-row queries — not the DB lever we assumed |
| index hints (`USING INDEX`) | nothing to gain: the planner already seeks on every query |
| **zero-row diagnostics hint** (new) | the expensive failure is a valid query that returns nothing and costs a repair turn (2–3 s decode). On 0 rows the executor can attach, for ≈ 1 ms, a content-free diagnosis: anchor found (id) + the anchor's degree per relation name. The model then repairs the *pattern* instead of the anchor. This targets 52 / 58 zero-row cases. |
| DB-side guards (5 s timeout for the agent role) | keep for K ≥ 16 contention; no plan-level problem to fix |
| prepared templates per family | the hop lane already shows templates never scan; combine with the router (019) rather than as a DB fix |
| result shaping | already minimal: p50 1 row, 12 kB cap never reached |

### Step 2 — zero-row diagnostics hint A/B (lab-s18, 2026-09-13; S1, 90 questions, K=8, one pass each)
| | hint off | hint on |
|---|---|---|
| answered easy / medium / hard | 18 / 29 / 5 = 52 | 17 / 28 / 9 = 54 |
| budget-exhausted | 12 / 1 / 24 | 13 / 2 / 21 |
| zero-row query events | 16 / 2 / 40 | 25 / 3 / 34 |
| rows recovered after a zero-row event | 14 / 2 / 31 | 15 / 3 / 23 |
| per-case wall p50 (s) | 10.2 / 6.1 / 18.6 | 9.1 / 6.4 / 18.7 |
| output tokens per pass | 12 484 / 7 455 / 20 972 | 11 648 / 7 905 / 20 554 |
| hint receipts (kind) | — | easy 25 (16 resolved, 9 unresolved) · medium 3 (3 resolved) · hard 34 (30 resolved, 4 unresolved) |
| hint cost | — | p50 5–12 ms, 2 indexed reads; 0.8 s total over 90 cases |

- **The hint lands where lab-s17 said it would.** In hard, 30 / 34 empty results had a matched anchor and an empty pattern (lab-s17 attribution: 52 / 58). The family it was built for moved: closest_collaborator answered 1 → 5; hard answered 5 → 9 (S1 hard so far: 7, 7, 5 → 9 is the best of four runs but n = 30).
- **The aggregate does not move.** 52 → 54 answered is inside S1's run-to-run band (54–56); easy and medium each lost one. Turn counts, per-case wall and output tokens are unchanged, so the repair turn is not removed — the model still spends the turn, it just repairs the pattern instead of the anchor. In easy the model made *more* zero-row queries with the hint (16 → 25; 9 unresolved anchors = paraphrased titles it kept re-spelling).
- **Timing confound noted:** pass wall 461 vs 308 s is the first vLLM restart of the session (ready after 230 s with torch compile vs 83 s), not the hint; DB p50 is also lower in the second pass (warm page cache). Per-case wall is the fair reading and it is flat.
- **Verdict:** the hint is a correct, nearly free (≈ 10 ms) interface feature and a precision tool for the argmax / multi-hop families; it is not a throughput lever on its own. Ship it as the default for the router's Text2Cypher lane (019) and keep it **off** in the serving-axis sessions (020) so those stay comparable with labs 013–017. Anchor misses in easy (paraphrased titles) are a routing / pre-resolution problem, not a hint problem.

## Decision needed (Claude -> user)
Step 2 done; next steps are approved (chat 2026-09-13 "네 모두 진행해주세요"): 020 session A (lab-s19, running) → 022 three-model (lab-s20) → 019 router (lab-s21). PROFILE done. Next build (free): the shared `request_analysis` module (anchor extraction → resolved id → degree; used by 019 and here) **plus a zero-row diagnostics hint in the executor** (anchor found + per-relation degree appended to an empty result). A/B on the 90 questions in one K=8 pass (≈ $2 on the A100 pair) — approve?
