---
id: 006
title: Long-context & context-engineering view (Graph-CoT hops vs Text2Cypher)
state: running
created: 2026-09-05
owner_intent: user
bead: seocho-he9d
---

## Intent (user)
(draft by Claude — edit freely) Watch the same graph-agent workload through a long-context lens:
how the prompt grows turn by turn, what the prefix cache actually reuses, how much of the
context is tool evidence vs reasoning, and whether Graph-CoT-style hop tools build a better
context than single-shot Text2Cypher. Use the findings to engineer the context (prefix layout,
evidence compaction, thinking budget, handoff boundaries) rather than only the model.

## Acceptance (user; Claude may draft)
- [x] Analysis adds per-turn context growth and per-turn prefix-cache hit to the attempt-window report (free, V17 data).
- [x] Ablation matrix defined with cost (below); PE `strengthened` profile implemented and wired (`--prompt-profile`, `SEOCHO_PROMPT_PROFILE`).
- [ ] First ablation run on a persistent instance (needs 002) with a paired comparison table.

## Constraints (user)
- Analysis part costs nothing; ablation runs follow the 002 budget.

## Feasibility (Claude)
- verdict: feasible; analysis now, ablations after 002.
- basis (V17 receipts): single-agent GRBench prompt grows 641 → 1,218 tokens over 6 turns (+~110/turn, tool results 2–143 bytes); GTSQA single jumps 606 → 1,860 at turn 2 (schema slice injected) and then +43/turn while output is pinned at 2,048 tokens for three turns (budget spent, no query). Manager/specialist split keeps the manager at 0.9–1.2k input tokens while the specialist accumulates 5.6k–16.7k over 5 requests and returns 2-byte evidence four times (rejected queries). Prefix-cache hit per attempt 0.36–0.73; provider cached_tokens is unreported by vLLM, so per-turn hits must be derived from the 1 s Prometheus samples bracketed by the new turn start offsets. SEOCHO exposes `query_mode="graph_cot"` and the graph-cot repo (action API, MAPLE data) is local, so a hop-tool lane is a lane definition, not new research code.
- alternatives: run the hop-tool lane on the split-host setup first (cheaper GPU) since it is a behaviour question, not a co-location one.
- estimated cost/time: analysis 1 day, $0; ablations ≈ 2–3 h per matrix pass on a persistent instance (≈ $5).

## Approval (user)
approved: yes  # free analysis step only (chat, 2026-09-05); ablations still need budget
budget_usd: 0

## Plan (Claude)
1. Extend analyze_agentic_os_attempt_windows.py: per-turn prompt tokens, output split, tool-result bytes, per-turn prefix hit from bracketed samples; add a context panel to the timeline.
2. Write the ablation matrix (arms, controls, metrics) in this file for approval.
3. Define the hop-tool lane (Graph-CoT actions over DozerDB: node lookup, feature, neighbours, degree) reusing the discovery cache contract.

## Log (Claude)
- 2026-09-09 — lab-s3 gate: PE-0 vs PE-1 (strengthened) on strata-v1, 24 cells each. PE-1 did not change the easy-stratum retrieval skip (GTSQA non-reasoning cells still answered without tools) nor the multi-agent policy rejections; it shortened GRBench multi/medium/non-reasoning wall from ~64 s to ~15 s (N=2) and validated 1/2 GRBench multi reasoning cells vs 0/2. One GRBench single/hard PE-1 attempt spent 150 s in a single Cypher (GPU busy 0.06) — the co-location signature at its extreme. Evidence: runs/lab-s3-try-222327/run-pe{0,1}-gate-strata/analysis/stratum-summary.md.
- 2026-09-05 — matrix approved in chat; first paid pass = PE-0 (baseline) + PE-1 (strengthened) gate cells on the 002 instance.
- 2026-09-05 — user approved the free analysis step in chat; starting per-turn context + cache-hit analysis on V17.
- 2026-09-05 — analyzer extended (context_profile, counter_delta, context panel, context-growth.md) + test; run on V17 — evidence: runs/v17-gate/analysis/
- 2026-09-05 — PE `strengthened` profile implemented: src/seocho/eval/prompt_profiles.py, `--prompt-profile` on all four lane runners + coordinator, `SEOCHO_PROMPT_PROFILE` in the e2e; receipts record profile name + addendum digest. Ablation matrix written above. Tests: 43 targeted passing.
- 2026-09-05 — request drafted from chat discussion.

## Result (Claude)
### Framing (agreed 2026-09-05)
- **Prompt engineering (PE) arm**: everything the model needs is in the system prompt up front (rules, tool schemas, scope); the model manages its own multi-turn context. This is B0 today. What we watch: how the context changes turn by turn and whether constraints survive.
- **Context engineering (CE) arm**: agent engineering (hooks, skills, handoffs, compaction) curates the context each turn — scope injected by a hook, schema delivered by a discovery skill, validation errors returned as structured repair cues, evidence compacted, specialist context isolated behind a typed handoff. This is S1/S2b/S4 territory. What we watch: whether the same constraints hold without occupying the prompt, and at what token/GPU cost.
### Context-preservation metrics (both arms, per cell)
1. Invariant survival: share of turns ≥ k whose query still satisfies the scope/read-only rules (PE relies on memory; CE on the hook).
2. Prefix reuse per turn: prefix-cache hit bracketed per turn; stable-prefix share = turn-1 prompt / turn-k prompt.
3. Growth composition: Δprompt per turn split into evidence tokens (≈ result_bytes/4) vs the model's own prior outputs.
4. Budget efficiency: validated queries per 1k prompt tokens; capped-output turns (output = max).
5. Recovery: turns from first rejection to first validated query (repair-loop length).
### V17 readings (PE arm only, N=1 per cell)
- GRBench single/off: prompt 641 → 1,218 (+115/turn); evidence only 670 bytes in total, so ~85 % of the growth is the model's own prior outputs. Per-turn prefix hit 0 (cold) → 0.92 → 0.83 → 0.73 → 0.64: reuse decays as history accumulates in front of the new turn.
- GRBench single/reasoning: prefix hit 0.0 on all three turns; the third turn is 2,048 tokens of message with no tool call. Reasoning mode changes the prompt shape enough to defeat reuse and then spends the output budget.
- GTSQA single: +1,254 tokens at turn 2 when the schema slice arrives as a tool result, then +43/turn; three consecutive turns capped at 2,048 output tokens with no query (PE could not turn the schema into a query within budget).
- Multi-agent: manager stays at 0.9–1.2k input; specialist accumulates 5.6k–16.7k over 5 requests while returning 2-byte evidence four times — a handoff boundary that preserved the manager's context but not the specialist's constraint.
- Caveat: turn brackets use 1 s counter samples; turn-1 rows can absorb the previous request's counters (prompt tokens served > input tokens). Sub-second turns read as None.
### PE-vs-CE ablation matrix (proposed; approve in this file)
Same 8 cells (GRBench/GTSQA × single/multi × reasoning on/off), same frozen cases, one cache phase (warm), paired by qid.

| id | arm | what changes | knob | cost/pass |
|---|---|---|---|---|
| PE-0 | B0 baseline prompt | today's system prompt | `--prompt-profile baseline` | shared gate pass |
| PE-1 | B0 strengthened prompt | scope pattern + read-only whitelist + repair table + finalisation rule in the system prompt; nothing injected per turn | `--prompt-profile strengthened` | +8 cells ≈ 25 min |
| PE-2 | PE-1 + thinking cap | reasoning-on cells with a lower `--max-output-tokens` (1024) | env `SEOCHO_MAX_OUTPUT_TOKENS` | +4 cells |
| CE-1 | S1 discovery skill | SEOCHO ontology-aligned schema slice replaces the neutral slice (discovery cache S1) | `--only-arm S1` (gate validator `--arm` needed: bead seocho-gevz) | +8 cells |
| CE-2 | S2b governed path | scope injection + read-only policy enforced by the operating layer, structured repair cues | `--only-arm S2b` (GTSQA today; GRBench needs the arm wired) | +4–8 cells |
| CE-3 | S4 governed + semantic bundle | CE-2 plus admitted ontology bundle in context | `--only-arm S4` | +8 cells |
| CE-4 | evidence compaction | tighter `--row-cap/--byte-cap` on tool results (context growth control) | runner flags | +8 cells |
| CE-5 | hop tools (Graph-CoT actions) | node lookup / feature / neighbours / degree over DozerDB instead of single-shot Text2Cypher | new lane definition, `query_mode=graph_cot` | +4 cells (GRBench) |

Metrics per cell: the five context-preservation metrics above + funnel + wall/LLM/tool decomposition + GPU busy share + per-turn prefix hit. Judge only on matrix data (10 cases × paired repetitions), not on gate N=1.
Order: PE-0/PE-1 first (cheap, isolates prompt vs engineering), then CE-1/CE-2, then compaction and hop tools. Estimated full pass ≈ 2.5 h on a persistent A100×2 instance ≈ $5.

### Outputs
- `runs/v17-gate/analysis/context-growth.md`, updated `attempt-decomposition.csv`, context panel in `attempt-timelines.html` (artifact republished).

## Decision needed (Claude -> user)
Approve the matrix (edit rows freely). Runs need 002 (persistent instance) with `budget_usd` set. Suggested first paid pass: PE-0 + PE-1 gate cells (~50 min, ~$1.6).
