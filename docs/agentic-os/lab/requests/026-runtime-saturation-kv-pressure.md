---
id: 026
title: Engine observations, same-host saturation, and actual long-context KV pressure
state: completion prepared; instrumentation and baseline K128 complete
created: 2026-09-15
bead: seocho-6niy
---

## Authorization and continuity

User explicitly authorized stages1/2/3 sequentially without further permission
on2026-09-15. This is new scope following the completed30 conditions; historical
unspent money is not reused. Operator execution bound: one A100 SXM80GB NVLink
pair, disk-inclusive <=$3.60/h, independent12600s watchdog, maximum hourly
estimate$12.60. Admission failure costs$0. Preserve every prior instance receipt
and never operate on peers. No replacement while an owned instance may exist.

Closest evidence: lab025 TP2K64 426.09 output tok/s/GPU, all6332 exact request
stage joins, actual contained inputs <=3062 tokens, no preemption. Lab024 showed
MTP helping K8 and hurting K64, but neither established the throughput plateau.
Engine sequence counts, grammar CPU and active versus reclaimable KV were
unmeasured. Repeated controls here intentionally establish same-host comparisons
and observer overhead; old results are not overwritten or pooled.

## Incremental hypotheses and sequence

1. A pinned async-scheduler observer can expose actual scheduled prompt/decode
   sequences, committed output tokens, CPU timing and disjoint KV block counts.
   Validate graph/trace/engine gates, observerOFF/ON K64 and a grammarK8 probe.
   CPU schedule-to-output spans overlap async batches and are never GPU kernel
   times. A separate bounded profiler capture supplies direct kernel intervals;
   profiler data is excluded from headline throughput comparisons.
2. Increasing applicationK from8/64 to128/256 may reveal an execution plateau or
   a KV/admission limit; MTP benefit may change with actual decode batch.
   Baseline/MTP each get two sweeps, ascending then descending K. Fixedmaxseq128
   distinguishes application backlog from the engine cap; report waiting and
   preemption. Two repetitions give observed variation, not paper-grade CIs.
3. Real graph-corpus context of8192/24576 tokens, K8/64 and shared/distributed
   prefixes tests reuse and memory pressure. These are fixed-output serving
   diagnostics using actual corpus tokens, not answer-quality or autonomous
   long-context agent claims. Compare two-call tasks and recorded reuse intervals;
   DB stays warm/fixed. No padding-based short-context claims, DB allocation
   sweep, offload backend substitution or eviction-policy ranking.

Keep Qwen3.6-27B bf16 revision6a9e13bd6fc8f0983b9b99948120bc37f49c13e9,
vLLM0.27.1 image0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967,
TP2, maxlen32768, maxbatch8192, heap8g/pagecache24g, Physics90/S1 and the prior
graph digest. Actual hardware/versions/source hashes belong in the live result.

## Acceptance and reporting

Primary output tok/s/GPU uses a common measurement window and two independently
observed physical GPUs, never sums TP rank outputs or overlapping request time.
Also record task/s, per-task input/output, actual context/cache/queue distributions.
Preserve exact request joins and unknown telemetry. Empty graph results are not
errors; final termination is not judged accuracy. Runtime/Tool/Agent charts for
agent comparisons stay separate from fixed-corpus serving diagnostics.

Do not proceed past failed live readiness/graph/trace/engine admission. Stop on
budget reserve, preserve partial results, collect/checksum essentials and verify
destroyed/absent. Instrumentation tests alone establish no performance claim.

## Result

Local implementation and exact-image preflight passed.73focused tests; basic
1112passed3skipped and all structural contracts passed. Source50731235, bundle
43ee747ef962adc160073fab6ebd8f02f544a440d793a349c014571de1503f71.
Owned51135854 created1789489330.799 on A100NVLink host195410; staging running,
no comparison result yet. Initial live$3.3562963/h, watchdog12600s. See HANDOFF17.


## First run result and recovery registration (2026-09-16 KST)

Lab-s25 completed stage1: observer OFF/ON K64 428.2441/432.4357 output tok/s/GPU
and1.89273/1.90476task/s. OFF2293requests all exact; ON trace/engine gates pass.
This pair shows no throughput regression, but one evolving agent workload is
not a precise instrumentation-overhead estimate. Direct observed K64 decode
sequences mean59.65; baseline-r1K8/K64 mean6.90/60.60 with123.6381/436.3959
tok/s/GPU. No saturation conclusion yet.

Two GPU rank traces contain36478kernel events each, with interval-union gaps
5.34%/10.37% over~0.94s captured per rank. These profiled timings are diagnostic,
excluded from primary throughput. GrammarK8 produced1227bitmask observations
within the common window: mean53.95ms wall and53.95ms calling-threadCPU. Warm
grammar creation mean0.161ms wall. This separates grammar CPU from GPU kernels.
Gate-only engine summaries before the repair cover the scheduler lifetime up
to that gate; they are admission evidence, not isolated per-gate performance.
Timed summaries already use the common window.

K128 failed before a valid window:106of128workers failed; GPU sampler process
creation also raised BlockingIOError. Agent process-tree threads peaked4289 at
K64 and6745 atK128, total measured service threads7576. No GPU saturation or
K128 throughput claim. Original per-worker stderr was outside essential-copy
coverage, so the first exception and original pids limit remain unknown.

A bounded local test in the exact image reproduced native-thread exhaustion:
8NumPy processes under pids.max64 yielded7thread-creation failures; scoped
native-thread=1 yielded8successful processes. This verifies a client-runtime
mechanism, not GPU performance. Recovery sets native-library thread counts to1
only in persistent workload workers, records error class/errno and cgroup
pids.current/max/events, and preserves worker stderr privately. Serving/DB
thread policies remain their existing settings. See
[OpenBLAS usage](https://github.com/OpenMathLib/OpenBLAS/blob/develop/USAGE.md).

Owned51135854 destroyed and absent; watchdog parent_teardown_verified_absent.
Lifetime2902.9403s at$3.3562963/h ≈$2.706424. Essential3433files/12025743bytes
remote=localSHA25680be72fc5cafea23d29548db1ad08e6510c9b9ef9e71c077fb6c003edb8c801d.
Bulk remains intentionally partial; original failed/_PartialArtifacts lifecycle
is preserved. Original task failure exit2 is independent of partial-copy status.

Recovery `--runtime-recovery` skips completed stage1, retains all16baseline/MTP
K8/64/128/256 comparisons (two sweeps) and the10context cells. Low-K controls
repeat because client policy changed and every new-host comparison must be
within that host. A fresh lab-s25b has9800swatchdog, <=3.60/h =>max$9.80;
combined hourly bound$12.506424 stays below$12.60. Remote8600s, session7400s,
reserve600s. No replacement while prior ownership is unresolved.


## 2026-09-17 recovery checkpoint: s25b and bounded completion

s25b source b5ce2327: gate and baseline K8/K64/K128 passed exact trace and
engine admission. Rates121.459/436.707/485.151 output tok/s/GPU,
task/s0.54936/1.88381/2.09153. Native-thread=1 reduced K128 agent threads to513;
cgroup pids peak1434, limit7680, pids.max events0. K256 failed40/256 worker
starts with OSError before a valid window;216 tasks completed during draining.
Original coordinator catch omitted errno, so exact live errno is unknown.
K256 pids peak1838 and no pids/max/OOM events: this is not GPU saturation evidence.

Bounded local startup reproduction (96children, RLIMIT_NOFILE256): concurrent
creation yielded61EMFILE failures; serialized creation started96/96. Fix scopes
serialization to Popen startup; actual tasks remain concurrent. Coordinator
errors now record numeric errno and RLIMIT_NOFILE without private error text.
This is a verified possible mechanism, not proof of the missing live errno.

Instance51161376 destroyed/absent; watchdog parent_teardown_verified_absent.
Essential5219files/7280675bytes SHA256
6d07cc9904aefd0c46dc3e866e25697fe3cf3f686e026351bda931f78093f078.
Bulk remains intentionally partial. Runtime2547.817764s at3.229907407/h cost
2.285893186; s25+s25b total4.992317572. Historical cumulative approx123.4123.

Registered next delta: `--runtime-completion`, lab-s25c, same pinned stack and
worker thread policy plus serialized startup. One same-host paired sweep of
baseline/MTP at K8/64/128/256 (8cells +2gates), then all10actual context cells.
The second sweep is removed to prioritize completing the three authorized
stages within the original12.60 bound; no repeat variance or confidence interval
claim. Prior lowK controls repeat because a new host/startup policy needs a
matched control. No previously completed stage1 repeat. Hard lifetime7500s,
rate<=3.60/h =>max7.50; cumulative upper estimate12.492318. Remote6900s,
session6100s/reserve300s. No replacement while existing ownership is unresolved.
