---
id: 019
title: Request-analysis metrics and router policy — design before the routing experiment
state: approved
created: 2026-09-12
owner_intent: user
bead: seocho-d3ks
---

## Intent (user)
Chat 2026-09-12: before running a routing experiment, design the policy — how the router splits requests, triggered by a *request analysis* whose metrics do not exist yet.

## What the data already says (free, 2026-09-12; per-question outcomes of the five variants on the same 90 GRBench questions)
| family | class | n | T2C-B0 | T2C-S1 | T2C-O1 | HOP-B0 | HOP-O1 | oracle (any) | best single |
|---|---|---|---|---|---|---|---|---|---|
| authors_of_paper | L1 | 17 | 3 | 12 | 8 | 10 | 10 | 15 | S1 (12) |
| venue_of_paper | L1 | 13 | 4 | 8 | 9 | 9 | 9 | 9 | O1 / HOP (9) |
| author_of_both_papers | L2 | 6 | 0 | 5 | 4 | 6 | 5 | 6 | HOP-B0 (6) |
| author_paper_count | L2 | 3 | 3 | 3 | 0 | 0 | 0 | 3 | S1 (3) |
| citation_count | L2 | 10 | 10 | 10 | 10 | 10 | 10 | 10 | all |
| coauthor_on_paper | L2 | 6 | 1 | 6 | 6 | 6 | 6 | 6 | S1 / HOP |
| joint_paper_count | L2 | 5 | 2 | 5 | 4 | 1 | 1 | 5 | S1 (5) |
| closest_collaborator | L3 | 7 | 1 | 1 | 0 | 0 | 1 | 3 | — |
| collaborators_in_year | L3 | 7 | 0 | 2 | 6 | 1 | 2 | 7 | O1 (6) |
| most_cited_paper_of_author | L3 | 7 | 0 | 0 | 0 | 1 | 1 | 1 | — |
| paper_recommendation | L3 | 6 | 2 | 4 | 3 | 3 | 2 | 4 | S1 (4) |
| venue_of_collaboration | L3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | — |
- **Oracle ceiling 69 / 90** (some variant answered); S1 ∪ HOP-B0 = 62; best single lane S1 = 56; in-sample family routing (S1 vs HOP per family) = 59 at wall p50 7.9 s (S1 8.9 s, HOP 4.8 s). Routing between *these* lanes buys ≈ +3 answers and ≈ 1 s; the larger lever is the 21 questions nobody answers (most_cited_paper_of_author 6, closest_collaborator 4, venue_of_paper 4, venue_of_collaboration 3, …) — finalisation / argmax handling, not lane choice.
- Counts (citation_count 10/10 everywhere) and one-hop lookups do not need routing; the router's value is (a) sending lookups to the cheaper lane, (b) protecting the DB on hub anchors, (c) refusing / redirecting unresolvable anchors, (d) choosing contexts per family (collaborators_in_year: O1 6 vs S1 2 — the only family where meanings helped).

## Request-analysis metrics (to build; all computable before the LLM runs)
| metric | how | cost |
|---|---|---|
| `family` (12 template families) + `intent` (lookup / count / set-intersection / argmax / recommendation) | regex on the question shape (labeler already does this for GRBench; a small classifier or few-shot for open text) | µs |
| `anchors[]`: text, kind (title / name), `resolved_id`, `anchor_type` (paper / author / venue) | quoted-title + capitalised-name extraction → indexed exact lookup, CONTAINS fallback (RetrieveNode template) | 1–2 indexed reads, ≈ 5–50 ms |
| `anchor_degree` per relation, `degree_class` (low < 20 / mid / hub ≥ 200) | NodeDegree template on the resolved id | 1 read, ≈ 5 ms |
| `resolvability` (exact / contains / none) | from the lookup | — |
| `expected_cardinality` (1 / few / many), `needs_aggregation`, `needs_argmax`, `has_value_filter`, `hops_estimate` | from family / intent | — |
| `context_size_budget` | from anchors + family (S1 slice tokens) | — |
Emit as a content-free `request_analysis` block in the receipt (digest of the question, no text) so the router decision is auditable.

## Policy v0 (table; to be evaluated against the oracle)
| condition | plan |
|---|---|
| intent = count, anchors resolved | Text2Cypher S1, row cap 20, 1 query expected; finalise on first non-empty |
| intent = lookup (authors_of_paper, venue_of_paper, coauthor_on_paper), anchors resolved | Graph-CoT hops B0, ≤ 4 turns, fresh-token budget |
| intent = set-intersection / joint count (author_of_both_papers, joint_paper_count) | Text2Cypher S1 (whole query beats hops 5–6 vs 1) |
| intent = argmax / recommendation (L3 families) | Text2Cypher O1 with a finalisation rule (answer on the first non-empty result set), higher row cap; else abstain early |
| degree_class = hub | Bolt timeout 5 s, row cap 20, forbid CONTAINS on the hub property |
| resolvability = none | retrieval-first (CONTAINS over title/name) before any lane; if still none → abstain (saves ≈ 10 cases × 6 turns) |
Router metrics to report: coverage (share of requests with a confident family), decision accuracy vs oracle (did the routed lane answer when some lane could), answered / 90, wall p50, DB max call, tokens per case, and the cost of the analysis step itself.

## Feasibility (Claude)
- verdict: buildable in ~1 day: `request_analysis.py` (family regex from the labeler, anchor extraction from lab 018, two templated DB reads), a `router` runner that emits the plan and dispatches to the existing runners, receipts extended with the analysis block; evaluation on the 90 questions in one K=8 session ≈ 40 min ≈ $3–4 (H100 pair) or $2 (A100 pair).
- risk: in-sample policy fitted on the same 90 questions — report it as such; the honest test is the held-out 50 GRBench questions not in strata-v2 (140 − 90).

## Approval (user)
approved: yes   # chat 2026-09-13 "네 모두 진행해주세요"; bead seocho-d3ks; runs after lab-s20
budget_usd:          # ≈ 4

## Plan (Claude)
1. request_analysis module + receipt block + tests. 2. router runner (plan → runner). 3. Evaluate on the 90 (in-sample) and the 50 held-out questions. 4. Report.

## Log (Claude)
- 2026-09-13 12:50 KST — report page "Faster Tokens, Fewer Tokens" (labs 019–022, Korean): https://claude.ai/code/artifact/30a1e812-1e31-4426-8fd9-ba71bfd82189
- 2026-09-13 12:29 KST — **lab-s21 done** (instance 50836154, host 125728 A100 SXM4 NVLink pair, Czechia, $2.375/h, 11:13 → 12:29 ≈ 76 min ≈ $3.0). Router passes: router-v0-k8 394 s, router-v0a-k8 194 s (90 each), heldout-s1-k8 120 s, heldout-router-v0-k8 137 s (50 each); all exit 0. Essential collection 8 064 files verified; destroyed + verified. Package `s3://…/traces/lab-s21-try-111306` (880 files, 12 MB; index 11 runs). Tables `runs/lab-s21-try-111306/session-summary/{serving-arms,scheduler-occupancy,router-eval,grammar-eval}.md`.
- 2026-09-13 09:45 KST — **router built (gare2e 4969ad1f, bundle v47)**: `src/seocho/eval/request_router.py` (policy V0 / V0A table with reasons[]), `scripts/benchmarks/run_grbench_router.py` (labels family, resolves anchors through the executor boundary, plans, dispatches to the Text2Cypher or hop runner, adds content-free `request_analysis` + `router_plan` blocks to the receipts; V0A abstains without an LLM call), lane `grbench-router` in the schedule coordinator and the e2e matrix (arm = policy). Held-out cohort: the 50 GRBench questions outside strata-v2 (labeler strata 8 / 27 / 15; families led by citation_count 10, authors_of_paper 6), schedule arms S1 + V0; in-sample router schedule V0 + V0A; discovery records rebuilt for all 140 (cohort 90 identical). Queue session-O (lab-s21, after lab-s20): cache-serving → router-v0-k8 → router-v0a-k8 → heldout-s1-k8 → heldout-router-v0-k8 (≈ 4 passes, ≈ 35 min ≈ $2.5). Revisions vs the v0 table: hops get token budget 32 768 (cumulative-budget fairness), unresolved anchors get a bounded 3-turn S1 attempt in V0 (abstain only in V0A), the Text2Cypher lane runs with the lab-021 hint on; hub timeout / CONTAINS ban are notes (no runner knob yet).
- 2026-09-12 12:10 KST — drafted from chat; oracle / family table computed from lab-s11/s13/s15/s16 receipts.

## Result (Claude)
### Router V0 / V0A vs S1 (lab-s21; K=8, Qwen3.6-27B, hint on in the router's Text2Cypher lane)
| pass | cohort | answered e/m/h (total) | policy + DB rejections | zero-row | out tokens | per-case wall p50 | analysis cost |
|---|---|---|---|---|---|---|---|
| S1 baseline (lab-s20 27B, same day) | 90 in-sample | 19/29/8 (56) | 48 | 57 | 41 k | 9.0 s | — |
| S1 baselines lab-s18 / s19 a0 | 90 in-sample | 52 / 52 | 39–40 | 62 | 40–41 k | 8.6–9.8 s | — |
| **router V0** | 90 in-sample | 21/23/**12** (56) | **10** | 53 | **32 k** | **7.1 s** | 11.5 ms p50 (1–3 indexed reads) |
| router V0A (abstain when nothing resolves) | 90 in-sample | 22/24/12 (58) | 8 | 47 | 33 k | 6.9 s | 8.4 ms |
| S1 baseline | 50 held-out | 5/27/2 (34) | 17 | 29 | 20.6 k | 8.0 s | — |
| **router V0** | 50 held-out | 4/21/6 (31) | 4 | 26 | 19.9 k | 7.2 s | 7.8 ms |

Routing that happened (V0, in-sample): 35 lookups → Graph-CoT hops B0 (answered 26 / 35), 20 argmax / recommendation → Text2Cypher O1 with row cap 50 (10 / 20), 35 counts / set-intersections / other → Text2Cypher S1 (20 / 35). Every one of the 80 questions with a named entity resolved at least one anchor (exact or CONTAINS), so V0A never abstained — V0A ≡ V0 on this benchmark and its +2 is run variance. Degree classes: low 35, mid 41, hub 4.

**Readings**
- **What the router buys is cheaper, cleaner runs, not more answers.** In-sample it matches the best S1 run (56) while cutting policy + DB rejections 48 → 10, output tokens −22 %, per-case wall −21 %; hard answered 12 is the best of any 27B run (S1: 5, 5, 7, 8). Out-of-sample it answers 31 / 50 against S1's 34 with the same −20 % tokens / −10 % wall.
- **Where the policy is right:** argmax families to O1 + row cap 50 — most_cited_paper_of_author 5 / 7 in-sample and 3 / 3 held-out where S1 answered 0 / 7 and 0 / 3 (the family lab 021 identified as pattern-error prone); venue_of_paper via hops 10 / 13 vs 7 / 13.
- **Where it is wrong (the held-out −3):** (a) counts with the 4-turn cap — author_paper_count 1 / 4 vs S1 4 / 4; (b) one-hop lookups sent to hops — authors_of_paper 3 / 6 vs 5 / 6 held-out (in-sample 11 / 17 vs 12 / 17): the hop lane still spends turns on RetrieveNode paraphrases; (c) joint_paper_count 3 / 5 vs 5 / 5 is the same S1 lane, so variance. Policy V1 follows directly: drop the count turn cap, keep lookups on S1 unless the anchor resolved *exactly* (hops only pay when RetrieveNode needs no fallback), keep O1 + row cap 50 for argmax.
- **Decision accuracy vs the oracle:** the in-sample oracle (best lane per question over labs 013–017) is 69 / 90; V0 reaches 56 with a table of five rules and no fitted parameters; the remaining gap is the 21 questions nobody has answered plus the count / lookup misroutes above.
- **Cost of analysis:** 8–12 ms per request (p50), one outlier 243 ms (cold plan); negligible against a 7 s case.

**Verdict:** ship the router as the default Text2Cypher entry point for the *efficiency* claim (−20 % tokens, −20 % wall, −80 % rejections at equal answers) and report the held-out −3 honestly; V1 policy edits are one-line changes and a ≈ $1 held-out re-run.
## Decision needed (Claude -> user)
Approved 09-13. Fairness handled by the plan's larger hop budget rather than a semantic change; report it as such.
