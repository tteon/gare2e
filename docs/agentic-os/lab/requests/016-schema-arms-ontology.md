---
id: 016
title: Missing schema cells — relevant × ontology for Cypher (GRBench S4-equivalent) and full × ontology on both graphs
state: done
created: 2026-09-12
owner_intent: user
bead: seocho-ijkj
---

## Intent (user)
Complete the schema 2×2 from lab 015 (chat 2026-09-11: "B0/S1/S4 먼저, full + ontology arm은 나중에").

## What lab 015 measured
S1 (relevant × structural) lifted GRBench single answers 7/16/3 → 20/29/7 (L1/L2/L3), the size-matched control C- collapsed them to 0/2/1; S4 (relevant × ontology, SPARQL only) was indistinguishable from S1 on GTSQA. Missing cells: GRBench relevant × ontology (no Cypher S4 exists), and full × ontology on both graphs.

## Feasibility (Claude)
- verdict: buildable; the honest prior from 015 is a small or null effect on top of S1.
- work: (1) a Cypher-side "S4": S1 slice + SEOCHO semantic bundle (domain/range, relation semantics) as context, executed through the GRBenchDozerExecutor boundary — runner `--arm S4` in run_direct_react_text2cypher.py + discovery cache arm; (2) "F+O" arm: B0's full schema answer + ontology bundle, both runners; (3) strata-v2 caches regenerated with the new arms (offline staging → appliance cache-serving). ≈ 1–1.5 days of code + one K=8 session ≈ $5 (two arms × GRBench lanes ≈ 40 min each + GTSQA F+O ≈ 30 min).
- alternatives: run only the Cypher S4 first (≈ $3); skip full × ontology if S4 ≈ S1 again.

## Approval (user)
approved: yes   # chat 2026-09-12 "016 우선 하시죠"; scope narrowed to single-agent lanes (user: multi-agent is out of focus)
budget_usd: 5

## Plan (Claude)
1. Runner + cache arms, tests. 2. Bundle v43+, queue session-H. 3. Report as an extension of 015.

## Log (Claude)
- 2026-09-12 11:40 KST — done; combined report "Hops vs Queries" (URL in Result).
- 2026-09-12 11:05 KST — nothing further needed from the appliance: O1 (lab-s15 salvage) is complete; report combined with 017.
- 2026-09-12 10:00 KST — salvage: the partial download (27 MB) held the complete arms-o1-k8 pass (90 + 90 receipts, spans). O1 on GRBench single: answered 17/24/9 (easy/medium/hard) vs S1 20/29/7 vs B0 7/16/3; repairs 0.13–0.7; 0 timeouts; unanchored_scan 4, all recovered with rows. GTSQA O1: non-empty 4/5/2 (B0 10/15/10, S1 14/7/12), answered 0 — the domain/range block hurts SPARQL. Result written below once session-J confirms nothing else is missing.
- 2026-09-12 09:40 KST — lab-s15 (H100 PCIe+NVLink pair, UAE, $4.27/h) ran the whole queue but the artifact download (`scp -r /artifacts`, 1800 s cap) timed out from the UAE host and the fail-closed runner destroyed the instance → results lost, ≈ $5.3. Fix before relaunch: compress on the appliance, copy the small content-free receipts first, retry, and never destroy before an essential copy is verified.
- 2026-09-12 08:25 KST — no A100 NVLink pair ≤ $2.9 on the market; user allowed H100/H200. Admission widened: GPU RAM floor 163 840 → 160 000 MB (H100 80 GB pairs report slightly under 2×81 920) and ceiling $4.5/h → the H100 PCIe + NVLink pair on host 598051 ($4.27/h) qualifies. Session ≈ 2 h ≈ $8.5 (016 + 017 budgets $9). Relaunched.
- 2026-09-12 08:30 KST — folded into session-I with lab 017 (arms-o1-k8 on the two single lanes, then the two Graph-CoT passes); bundle v44; launcher lab-s15.
- 2026-09-12 07:50 KST — lab-s14 poller stopped after 77 admission misses (no NVLink pair ≤ $2.9; only H200 pairs at $7.6+). No instance was created, $0. User chose to pause paid runs and synthesise all sessions first. Everything for O1 is frozen (bundle v43, session-H, launch_lab_s14.sh) — relaunch is one command when a host appears.
- 2026-09-12 06:25 KST — design: S1 already lists the full GRBench vocabulary, so the 2×2 collapses; the one missing cell is **O1 = vocabulary + relation semantics** (direction, domain/range, meaning, property roles). S4 was S1 context with a different executor, not an ontology arm. Built: optional-arm discovery cache, o1_response builders (GRBench hand-authored projection semantics; GTSQA domain/range from the case graph's P31 triples), runners/schedule/e2e accept O1 for the single lanes, strata-v2 records + schedules-o1 + staging caches, bundle v43, session-H (cache-serving → arms-o1-k8). Sizes: GRBench O1 2.8 kB (S1 0.6 kB); GTSQA O1 ≤ 9.4 kB (S1 ≤ 5.7 kB; cap 12 288). Tests 49 passed. lab-s14 launched (NVLink rule, ≤ $2.9/h, ceiling 2 h). gare2e 15b4475a.
- 2026-09-12 07:00 KST — approved; user direction: single-agent lanes only from now on (multi-agent lanes dropped from new experiments). Scope = grbench-single + gtsqa-single: Cypher relevant × ontology, full × ontology on both.
- 2026-09-12 06:40 KST — proposed after lab-s13.

## Result (Claude)
Report: https://claude.ai/code/artifact/6853722a-06db-4090-8074-5f184cee079d

### O1 (ontology-enriched vocabulary) — lab-s15 salvage, K=8, strata-v2, single lanes
| lane | class | B0 (lab-s11) | S1 (lab-s13) | **O1 (lab-s15)** |
|---|---|---|---|---|
| grbench-single answered | L1 / L2 / L3 | 7 / 16 / 3 | 20 / 29 / 7 | **17 / 24 / 9** |
| grbench-single budget-exhausted | | 20 / 14 / 27 | 9 / 1 / 22 | 12 / 6 / 13 |
| grbench-single repairs / case | | 1.0 / 0.6 / 0.87 | 0.37 / 0.13 / 0.87 | 0.13 / 0.7 / 0.27 |
| grbench-single wall p50 s | | 15.8 / 10.3 / 15.9 | 8.8 / 6.2 / 18.4 | 9.7 / 5.0 / 11.9 |
| gtsqa-single non-empty | L1 / L2 / L3 | 10 / 15 / 10 | 14 / 7 / 12 | **4 / 5 / 2** |
| gtsqa-single answered | | 0 | 0 | 0 |

- **On Cypher, O1 ≈ S1.** Relation meanings on top of the S1 vocabulary move hard from 7 to 9 answered and cut hard exhaustion 22 → 13 (the model stops earlier), but lose 3–5 answers on easy / medium. Total 50 vs 56 — within what N = 30 cells can resolve, but no gain. The schema-semantics axis is saturated after S1: the vocabulary (which property holds the name, which relation name exists) was the missing information; meaning and direction were not.
- **On SPARQL, O1 hurts.** Non-empty falls to 4 / 5 / 2 from 10 / 15 / 10 (B0) while validation stays 23–26 / 30: the domain / range block (≈ 1 800 → 2 500 tokens) makes the model write queries that are well-formed but return nothing. GTSQA stays at 0 grounded answers under every context arm tried (B0, C-, S1, S4, O1) — the SPARQL loop, not its context, is the problem.
- Tokens: O1 GRBench 2.8 kB vs S1 0.6 kB; prefill share unchanged (0.05–0.09). Timeouts 0 / 229 calls; unanchored_scan 4, all recovered with rows.

## Decision needed (Claude -> user)
Build both missing cells (≈ $5 + code), only the Cypher S4 (≈ $3), or stop the schema axis here?
