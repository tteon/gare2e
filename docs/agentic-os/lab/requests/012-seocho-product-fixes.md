---
id: 012
title: SEOCHO product fixes surfaced by the bottleneck study
state: approved
created: 2026-09-10
owner_intent: user
bead: seocho-pe6s
---

## Intent (user)
Same approval as 011. Fix the defects the stratified bottleneck decomposition and the OTel trace exposed.

## Items (beads)
seocho-5qmw seocho-75qy seocho-jc6e seocho-7w0m seocho-xp47 seocho-z8ks
1. single Bolt execution boundary (two copies of the timeout bug).
2. indexes as part of the projection contract + gate check (indexes were never created in queue passes).
3. static query guard before execution (label-scan / aggregate-only shapes) — rules from PROFILE replays.
4. productive repair loop (99 % of GTSQA/multi cost).
5. GTSQA-multi easy retrieval trigger (lab 007 defect).
6. freeze serving defaults (TP=2, K=4, no spec/replicas).

## Approval (user)
approved: yes
budget_usd: 6  # one validation pass per lever group

## Log (Claude)
- 2026-09-11 11:30 KST — items 1–3 validated live on lab-s11 (bundle v40, N=90 GRBench single per pass): indexes ONLINE at start; graph-DB share 0.74 → ≤ 0.05, call p90 35 ms, 15 s timeout fired 3/723 (max 16.5 s); anchor guard rejected 16–20 first attempts in 9–12 cases, all repaired to validated queries, 1–3 non-empty. Item 4 evidence: GRBench multi missing_workspace_scope 306 rejections / 90 cases, 3–4 validated per stratum. Item 5 evidence: GTSQA multi easy triggers 12/30 vs 29–30 medium/hard. See 013 Result.
- 2026-09-11 07:40 KST — lab-s9 (22:18–23:12, $2.6) failed before any pass: the new `dozer_indexes` step in entrypoint.sh ran at service_start, before DozerDB accepted Bolt → ServiceUnavailable → appliance_fatal 3. Fixed: block moved after the DozerDB config validation / readiness (before dozer_java_pid); `bash -n` ok, tests/seocho/test_agentic_os_appliance_shell.py 23 passed; bundle v40. Live validation of items 1–3 now rides on lab 013 (all-k4 over strata-v2).
- 2026-09-10 22:50 KST — items 1–3 implemented (gare2e; bundle v39): single execution boundary (direct runner uses GRBenchDozerExecutor.execute_rows), index contract (runtime constants → exporter manifest → loader → admission gate indexes_online), static anchor guard (unanchored_scan repair code; SEOCHO_GRBENCH_REQUIRE_ANCHOR=0 disables). Behaviour change to expect: more query_policy_rejected:unanchored_scan on first attempts, then anchored repairs; DB tail should disappear. Validate with one GRBench-heavy pass after lab-s9.
- 2026-09-10 22:30 KST — request + beads created; order 1 → 2 → 3 (DB), then 4 → 5 (agent loop), 6.
