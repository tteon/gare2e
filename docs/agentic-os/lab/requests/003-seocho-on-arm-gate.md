---
id: 003
title: Does the SEOCHO-on arm (scope injection / query repair) pass the one-case gate?
state: feasibility
created: 2026-09-05
owner_intent: user
bead: seocho-gevz
---

## Intent (user)
(draft by Claude — edit freely) The raw model fails validation mostly on missing
`_workspace_id` scope and read-only policy. Check whether the SEOCHO treatment arm
turns those rejections into validated queries in the same 8 cells.

## Acceptance (user; Claude may draft)
- [ ] 8-cell funnel for SEOCHO-on next to V17's B0 funnel.
- [ ] Rejection classes per cell before/after.

## Constraints (user)

## Feasibility (Claude)
- verdict: feasible on a persistent instance (needs 002); code change is small.
- basis: frozen schedules already carry treatment arms — GRBench single/multi: `B0, C-, S1`;
  GTSQA single: `B0, C-, S1, S2a, S2b, S3, S4`; GTSQA multi: `B0, S4`. The coordinator
  accepts `--only-arm` and validates arms per lane (`_LANE_ARMS`). The gate validator,
  however, hard-codes `raw_b0` (`arm == "B0"` check) so a treatment-arm gate needs an
  `--arm` option there. Evidence: `.seocho/benchmarks/agentic-os/gate-10/*-schedule.json`,
  `scripts/benchmarks/run_agentic_os_schedule.py:31`, `scripts/benchmarks/validate_agentic_os_one_case_gate.py`.
- alternatives: run S1 (ontology/scope treatment) only for the 4 GRBench+GTSQA single cells first;
  multi-agent treatment exists only for GTSQA (S4).
- estimated cost/time: ≈ 1 h code+tests; ≈ 20 min runtime per 8-cell pass on a live instance (≈ $0.6).

## Approval (user)
approved: no
budget_usd: 0

## Plan (Claude)

## Log (Claude)

## Result (Claude)

## Decision needed (Claude -> user)
Which treatment arm first: S1 (GRBench+GTSQA single) or S4 (GTSQA multi)? Depends on 002 being approved.
