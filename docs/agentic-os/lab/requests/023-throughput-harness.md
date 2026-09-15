---
id: 023
title: Free throughput harness repair
state: implemented; local contracts validated; paid measurement pending
created: 2026-09-13
bead: seocho-65n1
---

## Intent and continuity

Continue the free harness work authorized in the 2026-09-13 handoff. Baseline:
`88bbed7a`, lab 020's 13:30 server-span reanalysis, lab-s19 and lab-s21 artifacts
listed in HANDOFF §9 and `traces/`. Existing measured evidence: zero server queue
and uneven static-shard supply; 1.93 s/case dead time in that reanalysis. The old
aggregate tokens/s figures do not establish a serving-configuration ranking.

Incremental hypothesis: replacing static shards and per-case interpreter imports
with a shared FIFO and persistent processes removes avoidable supply gaps.
This is an implementation hypothesis, **not a measured speedup**. No paid calls,
Vast operations, new dataset, model, prompts, graph tools, or answer scoring.
External-API/MARA findings in local memory remain a separate track.

## Implementation

- `run_agentic_os_schedule.py`: explicit `--dynamic-queue`, `--workers K`,
  `--runner-mode fresh|persistent`. FIFO dispatch preserves frozen ordinals and
  case identities; completion order may differ. Fail-fast stops new dispatches
  and drains running cases; continue-on-error records every attempted case.
- `agentic_os_schedule_worker.py`: one interpreter/module per worker, repeated
  lane `main()` calls with fresh arguments and per-case locals. Driver and sampler
  lifetimes remain case-scoped. Only interpreter/import work is reused; this is
  not a persistent DB connection or conversation cache. Python and native output
  go to mode-0600 `*.private.log` files, separately from the worker protocol.
- Dynamic execution supports GRBench single, Graph-CoT and grammar lanes. The
  direct CLI also supports a fresh-process router FIFO; persistent router mode
  rejects because its inner dispatcher still spawns a child for every case.
  GTSQA/multi-agent work remains outside this slice.
- Dynamic execution rejects static sharding, shared cache-control hooks, and
  inter-attempt sleeps. Cold schedules fail without a cache-control hook. It
  requires appliance-owned pass telemetry; overlapping per-case server metric
  attribution is disallowed.
- Execution fingerprints include dispatch/runner mode, concurrency, warmup,
  worker source hash, workspace, prompt profile and row/byte caps.

## Measurement contract

`coordinator-receipts.jsonl` retains existing correlation fields and adds dispatch,
queue wait, worker slot/PID (persistent mode), runner setup and wall times.
Host FIFO queue wait is distinct from vLLM request queue time.

`coordinator-window.json` starts after **every worker completes its first case**,
plus `--steady-state-warmup-s`. It ends when the final queued case is dispatched,
excluding the drain tail. There must still be backlog after warmup. An insufficient
cohort, incomplete run or process failure produces an unavailable window, not a
zero throughput estimate. Warmup and drain case receipts remain in the artifacts.
A host backlog alone does not prove steady server saturation or a stable rate.

Reuse `summarize_agentic_os_otel_stages.py` for request-level queue / TTFT /
prefill / decode from server spans. It now joins `llm_request` by **trace ID and
parent span ID**, rejects ambiguous matches, records request timestamps and
computes decode TPOT as `decode_s * 1000 / (completion_tokens - 1)` when observed.
Missing server stages stay unknown. `--window` marks fully contained requests;
the existing lane summary continues to describe the full pass.

With `--metrics`, `throughput.json` reports task completions/s and output tokens/s
on the **same inner scrape interval** within the backlog window. A task completion
means runner exit 0, not a correct answer; retain the answer/evidence funnel.
Missing counters, counter resets, missing lifecycle or scrape gaps >2.5 s leave
rates unobserved. The pass sampler must cover only this workload/endpoint.

The sampler now retains `vllm:iteration_tokens_total` sum/count/buckets. In pinned
[vLLM 0.27.1](https://github.com/vllm-project/vllm/blob/v0.27.1/vllm/v1/metrics/loggers.py#L691)
this is a histogram of tokens per engine step, not an output-token counter.
Upstream notes a possible multi-API-server accuracy caveat; preserve that limitation
in replica comparisons. The report exposes tokens/step independently of tok/s.

The trace packer includes coordinator-window and throughput receipts, preserving
its existing private-file exclusions. No historical artifacts are rewritten.

## Use after a paid run is separately approved

The appliance pass environment can opt in with:

```bash
SEOCHO_E2E_PHASE=main
SEOCHO_MAIN_SKIP_CLOSED=1
SEOCHO_MAIN_LANES=grbench-single
SEOCHO_SCHEDULE_MODE=dynamic
SEOCHO_RUNNER_MODE=persistent
SEOCHO_CONCURRENCY=8
SEOCHO_STEADY_STATE_WARMUP_S=10
```

These are pass/queue settings, not authorization to launch. Keep the chosen frozen
schedule, arm, model, budgets, graph and serving settings explicit. The selected
schedule must contain only warm/none entries and enough tasks to retain backlog
through warmup. Compare fresh FIFO vs persistent FIFO before attributing a change
to process reuse. Old launcher defaults stay static; opt in deliberately.

The dynamic coordinator writes under `e2e/<lane>/shard-dynamic/` so existing
shard-based catalogs and turn summaries continue to find the cases. For example,
after collecting the pass and exporting client spans:

```bash
python3 scripts/benchmarks/summarize_agentic_os_otel_stages.py \
  --client "$PASS/otel/spans.jsonl" \
  --server "$RUN/otel/collector-spans.jsonl" \
  --window "$PASS/e2e/grbench-single-tool-false/shard-dynamic/coordinator-window.json" \
  --metrics "$PASS/workload/vllm-metrics.jsonl" \
  --output-dir "$PASS/otel"
```

Use a separate output directory per coordinator when a pass contains multiple
lanes/arms. Do not mix lifecycle windows or endpoint counters across passes.

## Result

Implemented and tested locally, without an LLM or graph service. Regression tests
cover real worker processes/module reuse, native/private streams, failure drain,
FIFO refill past a blocked early task, static vs dynamic appliance commands,
trace matching and missing/reset telemetry. A synthetic-provider test invokes the
actual Text2Cypher main twice and checks case/trace separation.

Focused result: **91 passed**. Validation command (using the existing environment without dependency changes):

```bash
python -m pytest tests/seocho/test_agentic_os_schedule.py \
  tests/seocho/test_agentic_os_dynamic_schedule.py \
  tests/seocho/test_agentic_os_stage_measurement.py \
  tests/seocho/test_agentic_os_observability.py \
  tests/seocho/test_agentic_os_appliance_shell.py \
  tests/seocho/test_pack_agentic_os_traces.py -q
```

Basic CI was run via `UV_PROJECT_ENVIRONMENT=<existing-venv> UV_NO_SYNC=1 bash
scripts/ci/run_basic_ci.sh`: 1112 passed, 3 skipped. Runtime shell, ownership,
import, root, environment and agent-doc contracts passed. The final identity
check failed on five existing Vast SSH fixture addresses in unchanged
`test_vast_colocated_agentic_os_run.py`; full CI is therefore **not green**.

No throughput improvement, GPU occupancy, live compatibility or answer parity
claim follows from these tests. Next free preparation: synthetic serving replay
and tool-stub executor. The five paid comparisons (~$13), router V1 and public
seocho landing remain awaiting owner approval.
