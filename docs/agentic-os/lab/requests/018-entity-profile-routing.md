---
id: 018
title: Entity-centric profile of the questions (duplication, hubness, per-stratum anchors) as a routing / caching input
state: done
created: 2026-09-12
owner_intent: user
bead: seocho-f592
---

## Intent (user)
Chat 2026-09-12: "GRBench와 같은 데이터셋에서 entity 중복도가 높은지 등 query에서 호출되는 entity를 데이터 사이언티스트 관점에서 접근해서 routing 정책에 활용하는 디자인이 필요… e2e만 돌리다 보니 놓치고 있다." Look at *what the questions ask about* (which nodes, how often, how connected) before deciding how requests should be routed (direct answer vs Text2Cypher vs hop walk; cache / pre-warm; DB-side guards).

## Questions this should answer (free, offline, no LLM)
1. **Duplication**: how many distinct anchor entities do the 140 GRBench questions (and the 90-case cohort) name; how many nodes are hit by ≥ 2 questions. Same for GTSQA seeds (first pass below).
2. **Hubness**: degree of each anchored node per relation; per-stratum and per-template-family distribution. Hub anchors are where unanchored scans and the 15 s timeouts came from (lab-s11/12).
3. **Resolvability**: share of named entities that resolve to a node by exact name / title (RetrieveNode's first step); the unresolved ones are where the hop lane will fail at hop 0.
4. **Routing implications**: which features are known *before* the LLM runs (entity type, degree class, cohort familiarity) and could pick the execution path or a cached subgraph.

## First pass (GTSQA seeds, 2026-09-12)
All 1 622 cases: 3 118 seed mentions, 2 665 distinct (dup rate 0.15); 76 seeds used ≥ 3× (French, German, Berlin, English, World War II…); 78 answers reused ≥ 2×. Our 90-case cohort: 148 seeds, all distinct — zero reuse. GTSQA is a long-tail workload for this cohort: nothing to cache across cases, so routing has to key on entity *class* (language / country / period seeds are hubs) rather than identity.

## First pass — GRBench (2026-09-12, offline; nodes scan 15 s, 67 M-edge scan 56 s)
- **Duplication: none.** 140 questions name 174 entities (60 paper titles, 76 author names — the rest are template words), all distinct; every named entity resolves to exactly one node by exact title / name; no node is anchored by two questions. Same in the 90-case cohort (110 anchor nodes, all distinct). Like GTSQA, this cohort is pure long tail: there is nothing to cache *by identity* across cases.
- **Hubness by class (anchor degree = edges out of the anchored node):** easy p50 12 (max 132), medium p50 71 (max 1 823 — one hub author), hard p50 2 (max 23). Class mix: easy 18 low (<20) / 11 mid / 1 none; medium 8 low / 20 mid / 1 hub / 1 none; hard 21 low / 1 mid / 8 none. Hard questions are anchored on *low-degree authors* (closest_collaborator, most_cited_paper_of_author, collaborators_in_year: p50 degree 1–4) and then ask for an argmax over their neighbourhood — the cost is in the second hop and the aggregation, not in the anchor. Medium (author_of_both_papers 75, citation_count 42, coauthor_on_paper 29) sits on the busiest anchors: that is where the 43–63 s scans (lab-s7) and the 15 s timeouts (lab-s11/12) came from.
- **Resolvability gap = the hard tail.** 10 cohort questions have no capitalised / quoted anchor my extractor can resolve: 6 of them are `paper_recommendation` (hard; the question describes a paper rather than naming one), plus closest_collaborator 44, most_cited_paper_of_author 95, author_paper_count 102, authors_of_paper 105 (lower-case names). RetrieveNode's exact-match first step will fail on these — the same cases Text2Cypher lost.
- **Routing signal available before the LLM runs:** template family (from the question shape) predicts anchor type and degree class well (family → degree p50: authors_of_paper 19, venue_of_paper 9, citation_count 42, author_of_both_papers 75, closest_collaborator 2, most_cited_paper_of_author 1, author_paper_count 1). A router could therefore pick (a) direct one-hop Cypher for paper-anchored L1, (b) hop walk for author-anchored argmax families, (c) a tighter row cap / DB timeout when the anchor is a hub, (d) a retrieval-first path when no exact anchor resolves.
- Files: scratchpad `entity/grbench_entity_profile.json` (per-question rows: qid, stratum, family, anchors, degree); extractor `entity/grbench_entity_profile.py`.

## Router memo (first version, 2026-09-12)
Features known before the LLM runs: question template family (regex over the question shape, 12 families, 0 unmatched), anchor type (paper / author / venue), anchor degree class (low < 20 / mid / hub ≥ 200 — from a name → id → degree lookup that costs one indexed read), resolvability (does an exact name / title match exist).
Evidence so far (N = 30 per class): whole-query Text2Cypher with the S1 slice wins on aggregation families (medium: 29 / 30) where a hop walk needs several turns and hits the budget (hop medium 22–23); the hop lane matches or beats on 1-hop lookup families (easy: 19 vs 20 at half the wall) and never scans the DB (no timeouts, p50 4–6 ms), so it is the safe path for hub anchors; neither handles hard argmax families (5–9 / 30). Unresolvable anchors (6 paper_recommendation + 4 lower-case names in the cohort) fail both at step 0 and need a retrieval-first path.
Candidate policy: family → {{lookup: hop lane, S1 vocabulary}}, {{aggregation: Text2Cypher S1}}, {{argmax / recommendation: Text2Cypher S1 with a finalisation rule, or abstain early}}; degree class → row cap / Bolt timeout / hop cap; unresolved anchor → CONTAINS retrieval before any lane. An experiment would route the same 90 questions by this table and compare answered / wall / DB tail against the best single lane (Text2Cypher S1: 56 / 90).

## Plan (Claude)
1. GRBench profile script (running): quoted titles + capitalised names → node ids via the import CSV → per-relation degree from the 67 M-edge CSV → duplication, hub share, per-stratum / per-family anchor degree, unresolved list.
2. Join with lab-s11/s13 receipts by qid: answered / exhausted / DB timeout vs anchor degree class → does hubness predict the failures?
3. Router design memo: features available pre-LLM, candidate policies (degree-class → row cap / timeout / lane; familiar-anchor → cached slice), what an experiment would measure.

## Log (Claude)
- 2026-09-12 11:40 KST — done; combined report "Hops vs Queries" (URL in Result).
- 2026-09-12 08:35 KST — request opened from chat; GTSQA seed pass done.
- 2026-09-12 09:45 KST — GRBench profile done (see First pass); next: join with lab-s11/s13 receipts by qid (hubness vs exhaustion / timeouts) and the router memo.

## Result (Claude)
Report: https://claude.ai/code/artifact/6853722a-06db-4090-8074-5f184cee079d


## Decision needed (Claude -> user)
None yet — analysis is free. A routing experiment (if the profile shows a usable signal) will come as its own request with a budget.
