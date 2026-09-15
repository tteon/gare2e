---
id: 026
title: Engine observations, same-host saturation, and actual long-context KV pressure
state: live staging
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
