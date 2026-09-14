---
id: 024
title: Approved throughput comparisons and explanatory observability
state: measured partial; report available; remaining TP comparison budget-limited
created: 2026-09-13
bead: seocho-7yaz
---

## Approval and continuity

Owner approved all five pending comparisons with “네 모두 진행하시죠” after the
~$13 plan in HANDOFF §9–10. This covers free replay preparation and the five
comparisons; no further paid-run confirmation is needed within that scope.
Latest steering adds memory/cache observability while avoiding more intervention
axes. Router V1, public SEOCHO landing and unrelated experiments remain separate.

Closest evidence: labs 019–022 are measured under static shards, K8 and short
observed context with low KV pressure. Lab 023 (`f079c0dd`) implements dynamic
FIFO/persistent workers and measurement windows, with local contract tests only.
No new throughput claim exists before this run. Hosted API/MARA results remain
separate. Protected colleague instance 50468810 and all non-owned instances are
outside this task's lifecycle.

## Incremental hypotheses and registered comparisons

- Dynamic FIFO fresh versus persistent at K8 isolates interpreter/module reuse.
- K8/16/32/64 with baseline and MTP tests whether the MTP gain survives sustained
  request supply. Server max_num_seqs stays 128; K is application task concurrency.
- Frozen request replay: serving-only, immediate recorded tool response, and
  actual read-safe DB execution use the same captured request payloads and output
  length targets. Model responses never alter the next frozen payload. This is
  substrate evidence, not autonomous-agent quality or a model answer comparison.
- At high K64, block32/128 and chunk2048/8192 test whether previous low-load null
  results persist. Call this saturation only if the K curve establishes a plateau.
- Same grammar protocol: constrained query fractions 0/.5/1 under 16/4 logical
  CPU affinity. Measure the actual selected query fraction and output length.
- Repeated TP2 control versus two TP1 sticky replicas at K64 uses two physical
  GPUs in both arms. Combined running gauges do not expose actual decode batch.

Pinned conditions: Qwen3.6-27B revision
`6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, vLLM image0.27.1, bf16, maxlen32768,
prefix caching, non-reasoning, GRBench S1, maxturn6, per-request maxoutput2048,
task tokenbudget16384, existing MAPLE/Physics graph/workspace. DB heap8g and
pagecache24g remain fixed. NVLink pair required. No LMCache compatibility retry.

90-second windows start after every worker finishes its first case plus 10 seconds
warmup; a 40-repeat balanced backlog prevents early exhaustion. Stop dispatch at
the deadline and drain active cases. Short/exhausted/failed windows yield unknown
rates. Repeated questions are not independent answer-quality samples. Pass order
is registered and carries warm-cache/order effects; single-pass results remain
exploratory and do not establish confidence intervals.

## Execution and evidence

`run_agentic_os_throughput_session.py --print-plan` exposes the full pre-staged
queue. The existing Vast lifecycle owns admission, checksum staging, readiness,
collection and verified teardown. The session stops on service/gate/workload
failure. A live successful graph tool and valid trace are required before the
matrix. Existing single-agent-only scope overrides the older multi-lane gate.

Budget target: hourly ceiling $3.60 and hard lifetime 13000 s, worst-case $13
before any provider-specific non-hourly charge. Admission checks the disk-inclusive
live price. The owned label and selected offer are recorded before creation.
Session stops accepting another pass with less than 1500 s local reserve;
independent lifecycle watchdog remains authoritative.

See [observability factors](../../OBSERVABILITY_FACTORS.md) for measurement scope,
unknown telemetry, factor promotion rules and causal interpretation.

## Result

Preparation implemented. Existing focused tests: 91 passed; new replay/window/
memory tests: 9 passed. Basic CI: 1112 passed, 3 skipped; runtime/ownership/import/
root/environment/docs checks passed. Final identity check still rejects the five
pre-existing SSH fixture addresses in `test_vast_colocated_agentic_os_run.py`.
This is not a green full-CI claim.

Incremental source/exact-image preflight passed. The unchanged Dozer runtime and
image match `appliance-preflight-v28.json`; its successful combined live smoke is
reused, not rerun locally. New entrypoints parse under the image's Python.
That was the preparation checkpoint; live results below supersede it.

### Live result — lab-s22b

22 comparison passes plus gate/capture completed, all24 exit0. Baseline K8/16/32/64
output tok/s/GPU:130.30/225.64/331.47/441.45; MTP:163.31/223.52/268.29/289.20.
See [categorized report](../../reports/lab024-characterization/index.html) and
[full findings/limits](../../reports/lab024-characterization/README.md) for replay,
grammar, task token distributions, exact joined request stages and graph calls.
Each condition has one short ordered window; no confidence intervals or semantic
accuracy claim. Baseline saturation and offloading pressure remain unestablished.

Three block/chunk passes lack GPU samples inside their window; primary GPU rates
are null. TP2-control/replicas2 were not run because the queue reached its budget
reserve. Additional grammar K8 and newest explicit task/tool instrumentation were
not deployed after an interrupted amendment. Historical timing/parent links are
reconstructed with provenance; unmeasured components remain unknown.

Instance50866184 destroyed+absence verified by independent watchdog. Estimated
cost$12.1227. Collection archive recovered and locally reconciled51,590 files;
remote digest checkpoint absent, so `artifacts_partial_reconciled` is retained.
The missing-glob archive bug and pre-extraction checkpoint were fixed locally.
Follow-up scope/ceiling is in HANDOFF§12; original allocation nearly consumed.

### Final local validation for reporting/recovery changes

Focused reporting/OTel/collector tests:29 passed. Ruff on changed Python passed.
`bash scripts/ci/check-doc-contracts.sh` passed. Full
`UV_PROJECT_ENVIRONMENT=<shared-venv> UV_NO_SYNC=1 bash scripts/ci/run_basic_ci.sh`
ran1112 passing tests,3 skipped, with contracts green; its final identity check
still fails on the same five existing SSH fixture addresses. This is not a full
green-CI claim. Headless Chrome rendered10 tables,1 timeline SVG and25 selectors'
options; JavaScript `node --check` passed. These checks validate report/collection
contracts, not new live GPU/DB behavior.

### Approved bounded continuation — lab-s23

Owner explicitly approved the additional **maximum$5.40 /90minutes** with
“네 모두 하시죠” after the remaining scope and cost were presented. No approval
is pending within this extension. New instance must use A100 SXM80GB NVLink pair,
at least32 logical CPUs, <=$3.60/h including disk. Protected peers remain excluded.

Registered order: live gate105; TP2-control K64; twoTP1 sticky replicas K64;
grammar K8 CPU16 at constrained query ratio0/.5/1. No completed K/MTP/replay/
block/chunk/K64 grammar arm is repeated. The TP2 control is a necessary same-host
comparator for the previously unrun replica condition. Fresh host/cache/order are
recorded; comparison with prior K64 grammar remains cross-session exploratory.

Same model revision/image/data/schema/budgets and DB capacities as lab-s22b.
New explicit task clocks, LLM/tool parent IDs, lexical query shape and context byte
composition are enabled; these are not retroactively claimed for lab-s22b. No
engine CPU/kernel/decode-step instrumentation or offloading intervention added.
GPU sampler now resumes after transient sample failures and records error/gaps.
Collection uses exact file globs and persisted copy/extraction checkpoints.

Launcher `launch_lab_s22.py --continuation` sets label lab-s23,5400s watchdog,
4200s remote command cap. Session3000s with600s reserve runs only the six registered
passes including gate; collection remains bounded and partial failures preserved.
49 focused tests and Ruff pass before staging. Exact bundle/image preflight and
live readiness/gate remain required before the five comparisons.
