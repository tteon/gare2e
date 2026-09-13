---
id: 005
title: Attempt-window telemetry analysis pipeline (GPU / CPU / vLLM per attempt)
state: done
created: 2026-09-05
owner_intent: user
bead: seocho-6evc
---

## Intent (user)
Analyse the co-located Vast run telemetry so that, per attempt (case), we can see where GPU, CPU (vLLM / DozerDB / agent) and DB time goes, where the GPU idles, how prefix caching behaves per turn, and how much thinking tokens cost. Start with V17 gate data; the same pipeline runs on matrix data later.

## Acceptance (user; Claude may draft)
- [x] V17 telemetry joined to attempt windows (gate-case-telemetry.json).
- [x] Decomposition table per cell: wall = LLM + tool + residual (+ observability overhead), GPU-busy share, per-role CPU seconds, prefix hit per turn, thinking-token share.
- [x] One timeline figure per cell (turns/tool calls over GPU util·power·KV, DB CPU).
- [x] Script written under scripts/benchmarks with tests (commit pending with seocho-56wk) under scripts/benchmarks with tests; no paid run.

## Constraints (user)
- Local compute only; no new instance.
- Gate data is N=1 per cell: describe, do not claim distributions.

## Feasibility (Claude)
- verdict: feasible now
- basis: all inputs exist in `.seocho/benchmarks/agentic-os/colocated-appliance/runs/v17-gate/` (gpu.csv 946 samples/GPU at 1.1 s, host-process.csv per role, vllm-metrics.jsonl 1,016 snapshots × 34 families incl. prefill/decode/queue sums and prefix-cache counters, per-attempt receipts with turn latencies/tokens and DB stage timers, coordinator receipts with absolute windows). The join script exists; the gate's fail-closed exit skipped the join step, so it runs locally.
- estimated cost/time: $0; ~2 h.

## Approval (user)
approved: yes
budget_usd: 0

## Plan (Claude)
1. Run join_agentic_os_gpu_samples.py over the 8 gate roots → gate-case-telemetry.json.
2. Write scripts/benchmarks/analyze_agentic_os_attempt_windows.py: decomposition table (markdown/CSV) + per-cell timeline PNG/SVG.
3. Tests on a synthetic mini run; run on V17; attach outputs under the run dir and summarise here.

## Log (Claude)
- 2026-09-05 — join fixed (v1|v2 schema, int case_id) and rerun 8/8 — evidence: runs/v17-gate/e2e/gate-case-telemetry.json
- 2026-09-05 — analysis script + test; outputs written; artifact published — evidence: runs/v17-gate/analysis/
- 2026-09-05 — request created from chat (user: "그렇게 해주세요"), approved in chat for a free local analysis.

## Result (Claude)
- Outputs: `.seocho/benchmarks/agentic-os/colocated-appliance/runs/v17-gate/analysis/{attempt-decomposition.csv,attempt-decomposition.md,attempt-timelines.html}`; page published at https://claude.ai/code/artifact/c2d9778e-aafd-46a7-8225-23e268b745f5
- Two join defects fixed on the way: process window schema v2 not accepted; GTSQA single receipts carry int case_id.
- Readings (N=1 per cell, descriptive only):
  - GPU is decode-bound whenever the model is talking: busy share 0.90–1.00, power 240–260 W, decode 9–27 s per request in reasoning-on cells, TTFT 0.1–0.5 s, KV usage ≤ 3.2 % (batch of one).
  - The only cell that reached the database (GRBench single/off) shows the co-location signature: GPU busy 0.53, tool time 10.2 s of 21.2 s wall, DozerDB CPU 12.3 s in-window. GPU idles while DozerDB executes.
  - Prefix-cache hit per attempt 0.36–0.73; 0.0 in GRBench single/reasoning (3 turns, each prompt unique to the cache).
  - Multi-agent receipts attribute the whole wall to middleware residual (llm_ms = 0): accounting gap, filed as a bead.

## Decision needed (Claude -> user)
None for this request. Follow-ups: multi-agent time attribution fixed 2026-09-05 (bead seocho-npyq, LLMRequestTimer, verified on mock vLLM); rerun this pipeline on matrix data for distributions.
