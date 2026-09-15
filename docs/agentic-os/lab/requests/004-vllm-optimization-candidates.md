---
id: 004
title: vLLM/LLM serving optimisation channel (declarative sweep) for the graph-agent workload
state: feasibility
created: 2026-09-05
owner_intent: user
bead: seocho-gj7d
---

## Intent (user)
Open a channel so an AI engineer can improve serving for this workload without touching the harness: declare a variant (vLLM args, torch.compile / CUDA-graph settings, env, later scheduler and quantisation knobs) in YAML, run it against the same frozen cells, and get a paired comparison. Individual knobs (CUDA graph mode etc.) are only the first entries in that channel, not the point.

(original draft) Use the e2e telemetry (TTFT/TPOT, queue/prefill/decode,
prefix-cache hits, KV utilisation, per-turn token composition) to shortlist vLLM
serving optimizations worth testing for agentic graph-RAG workloads.

## Acceptance (user; Claude may draft)
- [ ] Table of candidates with the telemetry signal that motivates each and how it would be measured.
- [ ] Baseline numbers from V17 (and later matrix runs) attached.

## Constraints (user)

## Feasibility (Claude)
- verdict: feasible on the persistent instance (002) via a vLLM restart helper; no new instance per configuration.
- basis: vLLM 0.27.1 exposes every knob we want (verified in the image): `--enable-prefix-caching` / `--no-enable-prefix-caching`, `--enable-chunked-prefill` + `--max-num-batched-tokens` + `--long-prefill-token-threshold`, `--compilation-config '{"cudagraph_mode":"FULL|PIECEWISE|NONE"}'` (+ `--enforce-eager` control), `--kv-cache-dtype fp8|auto`, `--speculative-config '{"method":"ngram",...}'`, `--mamba-cache-mode align|none` (Qwen3.6 hybrid layers), `--async-scheduling`, and `--enable-prompt-tokens-details` (returns `cached_tokens` per request → exact per-turn prefix hit instead of 1 s bracketing).
- channel built 2026-09-05: `examples/agentic-os/serving-sweep.yaml` (schema `seocho.agentic_os.serving_sweep.v1`: model/revision, fixed workload block, metrics_of_record, funnel_guard, variants with `vllm_args`/`env`) + `scripts/benchmarks/sweep_agentic_os_serving.py` (restart → run → pull → join → decompose → paired summary, resumable, `--dry-run`). Tests in tests/seocho/test_serving_sweep.py.
- mechanism: `docker/agentic-os-appliance/restart_vllm.sh` (stop, relaunch with `VLLM_EXTRA_ARGS`, wait readiness, write a config receipt) + `run_lab_pass.sh` (one SSH pass = optional restart + e2e under the workload wrapper so GPU/process/vLLM samplers bracket exactly that pass).
- what "improvement" means here (batch-of-one, serialized agent turns, 0.6–2k-token prompts, up to 2k-token outputs): per config, per cell — TTFT, ITL/TPOT, prefill s/req, decode s/req, wall, GPU energy per case, prefix hit per turn, KV usage. Judge on paired cells (same qid, same prompt profile); require the same funnel outcome before comparing latency.
- semantic caching is not a vLLM flag: it is a middleware cache (question/query embedding → reuse of validated query or evidence). It belongs to the CE arms (006) and is measured as hit rate and avoided LLM calls across repetitions (`cache_phase` cold/warm already exists in the schedules).
- estimated cost/time: ≈ 8 configurations × 8 gate cells ≈ 8 × (restart 3–5 min + 15 min) ≈ 2.5 h ≈ $4.5 on the 002 instance.

### Serving-configuration matrix (proposed; approve in this file)
| id | config (VLLM_EXTRA_ARGS_OVERRIDE) | hypothesis for this workload |
|---|---|---|
| V-0 | current default (prefix caching on, PIECEWISE cudagraphs, chunked prefill default) | reference |
| V-1 | `--no-enable-prefix-caching` | quantifies what prefix reuse buys: TTFT/prefill on turns ≥ 2 |
| V-2 | `--compilation-config {"cudagraph_mode":"FULL"}` | decode-bound cells: lower ITL at batch 1 |
| V-3 | `--enforce-eager` | control: how much cudagraphs give at all |
| V-4 | `--max-num-batched-tokens 2048` (chunked prefill, small chunks) | TTFT vs prefill throughput at 0.6–2k prompts; expect neutral |
| V-5 | `--kv-cache-dtype fp8` | KV capacity irrelevant at 3 % usage; check decode speed and any accuracy drift in funnel |
| V-6 | `--speculative-config {"method":"ngram","num_speculative_tokens":4,"prompt_lookup_max":4}` | Cypher/SPARQL and repair loops repeat tokens: n-gram speculation may cut decode time |
| V-7 | `--mamba-cache-mode none` | isolates the experimental Mamba prefix cache path of Qwen3.6 |
| V-8 | `--async-scheduling` | overlaps scheduling with decode; batch-1 gain expected small |

Order: V-0, V-3 (bounds), V-2, V-1, V-6, then the rest. Every pass: PE profile fixed to the one chosen in 006 (baseline first).

## Approval (user)
approved: no
budget_usd: 0

## Plan (Claude)

## Log (Claude)
- 2026-09-05 — user reframed: build the optimisation channel (sweep), knobs are examples. Sweep spec + driver + tests written; dry-run verified; helper scripts need scp onto the live appliance (bundle v25 predates them).

## Result (Claude)

## Decision needed (Claude -> user)
Approve the matrix (edit rows). Runs on the 002 instance after the PE-0/PE-1 pass; ~$4.5 within the session budget if time allows, else next session.
