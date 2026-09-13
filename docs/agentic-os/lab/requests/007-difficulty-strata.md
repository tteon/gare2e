---
id: 007
title: Difficulty-stratified cohort (easy / medium / hard) and per-stratum e2e reading
state: done
created: 2026-09-08
owner_intent: user
bead: seocho-nxv6
---

## Intent (user)
(draft by Claude — edit freely) Split the workload into easy / medium / hard and see how the e2e
(funnel, turns, rejections, latency, GPU time) changes per stratum. Needs a difficulty rubric that
works for both Cypher (GRBench) and SPARQL (GTSQA) questions.

## Acceptance (user; Claude may draft)
- [x] Rubric written down (scripts/benchmarks/label_agentic_os_difficulty.py docstring + tests with one example per family).
- [x] Stratum distribution tables (below; `.seocho/benchmarks/agentic-os/strata-v1/difficulty-distribution.md`).
- [ ] Stratified cohort frozen (schedules with `stratum` set; discovery caches rebuilt) — the frozen
      GTSQA cohort today is one shape only: 3 hops, 2 seeds, 4 patterns, ((2)(1)).
- [x] Per-stratum funnel + decomposition from lab-s3 (30 B0 cases per lane, reasoning off) — below.

## Constraints (user)
- Labelling and cohort freezing are free; discovery-cache token validation and the run need an instance.

## Feasibility (Claude)
- verdict: feasible; the schedule builder already carries a `stratum` field (`--cases` JSONL: qid, stratum).
- basis: GTSQA rows expose `n_hops` (1: 521, 2: 601, 3: 450, 4: 50), seed count (1–4), gold `sparql_query`
  (1–5 triple patterns), `graph_isomorphism` shape, `test_type` (in_distribution 300 / unseen_relation 555 /
  unseen_graph 719 / both 48), multi-answer (228). GRBench processed data has only qid/question/answer; the
  paper's easy (single-hop) / medium (multi-hop) / hard (inductive) labels must be recovered from the HF
  release or assigned from the question template.
- proposed rubric (structural score, same for both lanes):
  - easy: 1 hop, 1 anchor, ≤ 2 patterns, single answer, no aggregation/filter
  - medium: 2 hops or 2 anchors or 3 patterns, or a set answer
  - hard: ≥ 3 hops, or a join across ≥ 2 anchors with ≥ 4 patterns, or aggregation / ordering / negation / inductive step
  - orthogonal tags kept as columns, not folded into the stratum: GTSQA `test_type` (distribution shift),
    text-property lookup required (exact-string risk), discovery breadth (distinct labels/predicates needed),
    expected result rows (evidence size).
- guard: never stratify by observed outcome (turns, rejections) — that is the dependent variable.
- estimated cost/time: labelling + cohort ≈ half a day, $0; discovery cache rebuild + stratified gate ≈ 40 min ≈ $1.5.

## Approval (user)
approved: yes  # chat 2026-09-08: rubric + n=10 per stratum approved (free steps now, paid step later)
budget_usd: 0

## Plan (Claude)
1. Label GTSQA from its columns; label GRBench by template (and cross-check against the HF release if it carries levels).
2. Write `scripts/benchmarks/label_agentic_os_difficulty.py` → cases JSONL (qid, stratum, tags) + distribution tables; tests.
3. Sample n per stratum (balanced), freeze cohort + schedules with `build_agentic_os_cohort_schedule.py`.
4. Rebuild discovery caches on the next instance; run the gate; extend catalog/analysis grouping by `stratum`.

## Log (Claude)
- 2026-09-08 — rubric v1.1: 'number of papers written by author X' was matching authors_of_paper (easy); now author_paper_count (medium). GRBench 38/57/45. Four labels changed (qids 101, 103, 104, 109); two of them (101, 109) sit in the strata-v1 *easy* cohort that lab-s3 is running. Analysis re-labels via `--difficulty-labels` (labels win over schedule strata); the cohort is re-frozen as strata-v2 for later sessions.
- 2026-09-08 — paid step queued in lab-s3: serving-token cache validation, per-stratum gate (--only-stratum), 30-case B0 pass per lane (reasoning off).
- 2026-09-08 — approved in chat; labeler + tests written; both datasets labelled; cohorts/schedules frozen; staging caches built; coordinator/analyzer stratum support added (15 tests green).
- 2026-09-08 — drafted from chat discussion.

## Result (Claude)
### Rubric (adopted 2026-09-08)
score = hops(1→0, 2→1, 3→2, 4+→3) + [anchors ≥ 2] + [patterns ≥ 4] + [aggregation] + [value filter] + 2·[argmax/superlative]; easy = 0, medium = 1–2, hard ≥ 3. Same formula for Cypher and SPARQL; computed from gold SPARQL structure (GTSQA) or question template family (GRBench), never from observed behaviour. Orthogonal tags kept as columns: GTSQA `test_type`, multi-answer, graph shape; GRBench text-title lookup.

### Distributions
- GTSQA (1,622): easy 150 · medium 945 · hard 527. By hops: 1-hop splits 150 easy / 371 medium (two anchors); 2-hop 406 medium / 195 hard; 3-hop 168 medium / 282 hard; 4-hop 50 hard. Shift: in_distribution is mostly medium (245) with 50 easy; unseen_graph_type is 476 hard / 243 medium; unseen_relation_type is 447 medium / 100 easy.
- GRBench MAPLE/Physics (140): easy 42 · medium 53 · hard 45, 12 template families, 0 unmatched. easy = authors/venue of a paper; medium = citation counts, co-author on a paper, author of both papers, joint-paper counts, author paper counts; hard = closest collaborator, most-cited paper of an author, collaborators in a year, venue of collaboration, paper recommendation (Graph-CoT's inductive class).

### Frozen artifacts (`.seocho/benchmarks/agentic-os/strata-v1/`, mirrored to gare2e `examples/agentic-os/frozen/strata-v1/`)
- `grbench-cases.jsonl`, `gtsqa-cases.jsonl` (qid, stratum; seed 0, 10 per stratum)
- `grbench-single-cohort.json` + `-schedule.json` (arms B0/C-/S1, cohort 346c55b5…), `gtsqa-single-*` (B0/C-/S1/S2a/S2b/S3/S4, cohort e270812c…), `gtsqa-multi-*` (B0/S4)
- `*-tool-schema.json` (digests equal the gate-10 contracts), `*-discovery-staging.json` (WhitespaceTokenCounter; must be re-tokenised by vLLM before a main-stage run)
- Coordinator gained `--only-stratum`; analyzer gained `case_id`/`stratum` columns, `--schedule`, and `stratum-summary.md`.

### lab-s3 results (2026-09-08/09, PE-0 baseline prompt, B0, reasoning off, corrected labels v1.1)
| lane | topology | stratum | N | triggered | validated | nonempty | answered | budget-exhausted |
|---|---|---|---|---|---|---|---|---|
| grbench | multi | easy | 8 | 8 | 2 | 0 | 0 | 0 |
| grbench | multi | medium | 12 | 12 | 1 | 0 | 0 | 0 |
| grbench | multi | hard | 10 | 10 | 0 | 0 | 0 | 0 |
| grbench | single | easy | 8 | 8 | 8 | 8 | 0 | 8 |
| grbench | single | medium | 12 | 12 | 12 | 12 | 4 | 8 |
| grbench | single | hard | 10 | 10 | 9 | 9 | 1 | 9 |
| gtsqa | multi | easy | 10 | 1 | 1 | 0 | 9 | 0 |
| gtsqa | multi | medium | 10 | 10 | 7 | 0 | 0 | 0 |
| gtsqa | multi | hard | 10 | 10 | 9 | 0 | 0 | 0 |
| gtsqa | single | easy | 10 | 8 | 8 | 5 | 2 | 7 |
| gtsqa | single | medium | 10 | 9 | 8 | 5 | 1 | 9 |
| gtsqa | single | hard | 10 | 9 | 8 | 3 | 1 | 9 |

Readings (N ≈ 10 per cell·stratum; describe, don't generalise):
- **Difficulty barely moves the GRBench single funnel**: triggered 30/30, validated 29/30, non-empty 29/30 across easy/medium/hard; the loss is finalisation — 0/8 easy, 4/12 medium, 1/10 hard answered, the rest exhausted 6 turns while holding rows. Median wall 17 → 15 → 21 s, decode 1.65 → 1.62 → 2.08 s/req.
- **Topology dominates difficulty**: GRBench multi is rejected by the query policy in 107/113 specialist calls regardless of stratum (validated 3/30, non-empty 0/30). GTSQA multi executes validated SPARQL (7–9/10 on medium/hard) but every result is empty (43 empty + 23 error executions; 0/30 non-empty).
- **Easy questions suppress retrieval**: GTSQA multi easy triggered 1/10 — the manager answered 9/10 from memory; GTSQA single easy triggered 8/10. Medium/hard trigger 9–10/10. This is the retrieval-decision effect the funnel was built to expose, and it appears only on the easy stratum.
- **GTSQA single evidence falls with difficulty**: non-empty 5/10 → 5/10 → 3/10; answered 2 → 1 → 1; budget exhaustion 7 → 9 → 9.
- Gate verdict (attempted criterion): PE-0 medium and hard 8/8 pass, easy fails on the two GTSQA no-tool cells; PE-1 (strengthened prompt) same easy failures plus one medium blocker — the stronger prompt did not change the easy-stratum retrieval skip.
- Telemetry gap: nested nvidia-smi samplers timed out (bead filed); GPU busy share missing for many cells this session.

### Next
- Re-freeze strata-v2 from labels v1.1 for future sessions; add reasoning-on main pass; extend the catalog to group by stratum; targeted treatments: finalisation rule (GRBench single), policy-aware specialist prompt/scope injection (multi), retrieval trigger for easy questions.
### Paid step as run (≈ 3.7 h, $5.38)
On the appliance: `build_agentic_os_discovery_cache.py --base-url http://127.0.0.1:8000` over the staging records → `*-discovery-serving.json`; then a stratified gate (`--only-stratum easy|medium|hard`, one B0 case each) and, if admitted, the 30-case B0 pass per lane for the first per-stratum funnel table.

## Decision needed (Claude -> user)
None for the free part. The paid step joins the next instance session (002 re-approval).
