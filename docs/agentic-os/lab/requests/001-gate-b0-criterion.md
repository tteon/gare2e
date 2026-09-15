---
id: 001
title: One-case gate criterion for the raw B0 baseline
state: approved
created: 2026-09-05
owner_intent: user
bead: seocho-q174
---

## Intent (user)
(draft by Claude — edit freely) Decide whether the pre-matrix one-case gate should
keep requiring a validator-accepted query in all 8 cells for raw B0, or only a
query attempt, so that a paid full matrix can be admitted.

## Acceptance (user; Claude may draft)
- [ ] Gate semantics for B0 are written down (validated vs attempted) with the reason.
- [ ] `validate_agentic_os_one_case_gate.py` + tests reflect the decision.
- [ ] A gate-only run passes the new criterion, or the decision is to keep it and go via 003.

## Constraints (user)
- No paid run needed for the code change itself.

## Feasibility (Claude)
- verdict: feasible (code-only)
- basis: V17 evidence — 8/8 cells triggered the graph tool and discovered schema;
  7/8 issued ≥1 query; 3/8 passed validation (GRBench single/off with 6 rows;
  GTSQA multi ×2 with empty results). Rejections: `missing_workspace_scope`,
  `query_policy_rejected`, SPARQL policy; one cell hit the token budget before
  any query. Evidence: `.seocho/benchmarks/agentic-os/colocated-appliance/runs/v17-gate/e2e/one-case-gate.json`.
- alternatives:
  - A. B0 criterion = `query_attempted` (keep `query_validated` for treatment arms). Validation
    failure rate becomes a reported baseline metric. Unblocks the matrix now.
  - B. Keep `query_validated` for all arms; admit the matrix only after the SEOCHO-on arm passes (see 003).
  - C. Keep as is and raise B0 token/turn budgets first (GTSQA single/off exhausted 16k without a query).
- estimated cost/time: A ≈ 1 h code+tests, /bin/bash. B depends on 003.

## Approval (user)
approved: yes  # chat 2026-09-05: option A
budget_usd: 0

## Plan (Claude)
1. On approval of A: add `--b0-criterion {validated,attempted}` to the gate validator (default stays validated), wire it through the e2e script env, tests.
2. Record the choice in DECISIONS.md and the execplan.

## Log (Claude)
- 2026-09-05 — decision A (B0 criterion = attempted) taken in chat; runs use SEOCHO_GATE_B0_CRITERION=attempted and report validation-failure rate as a baseline metric.
- 2026-09-05 — option A implemented as a switch, default unchanged: validate_agentic_os_one_case_gate.py `--b0-criterion {validated,attempted}`, e2e env `SEOCHO_GATE_B0_CRITERION`, receipt records `b0_criterion` and per-cell `query_validated_observed`; test added. The decision is now which value to run with.

## Result (Claude)

## Decision needed (Claude -> user)
Pick A (run with `SEOCHO_GATE_B0_CRITERION=attempted`), B (keep `validated`, go via 003), or C. The code supports A and B without further change.
