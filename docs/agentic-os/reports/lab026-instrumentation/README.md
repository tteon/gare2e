# Lab026: engine instrumentation and first high-K admission failure

Source50731235, lab-s25, A100 SXM80GBx2/NVLink300/TP2, host195410,
32effectiveCPUs, cgroup30.72CPU quota, Qwen3.6-27B bf16 pinned revision,
vLLM0.27.1, Dozer5.26.3/Physics90/S1. Same graph/model settings as request026.
DB heap8g/pagecache24g, maxlen32768/maxseq128/maxbatch8192. Per-worker first-case
warmup plus10s,90s dispatch window, drain excluded. Quality unscored.

| Condition | Output tok/s/GPU | task/s | Actual mean decode sequences |
| --- | ---: | ---: | ---: |
| Observer OFF K64 |428.244|1.893|unknown|
| Observer ON K64 |432.436|1.905|59.65|
| Ontology grammar K8 |53.486|0.303|7.77|
| Baseline sweep K8 |123.638|0.560|6.90|
| Baseline sweep K64 |436.396|1.848|60.60|

The OFF/ON pair shows no regression in this run, not a precise overhead estimate:
agent trajectories and token demand can differ. The5conditions contain1616tasks,
7479requests and6496tools; detailed coverage/token distributions and linked
Runtime/Tool/Agent views are in [the interactive report](index.html) and [Runtime / Tool / Agent charts](figures-v2/index.html). CSV.gz files support
chart regeneration. [conditions.json](conditions.json) contains exact engine,
CPU/grammar/KV, profiler, admission and input-hash evidence.

Grammar bitmask work averaged53.95ms wall/calling-threadCPU over1227common-window
steps; warm grammar creation averaged0.161ms wall. Two separate32-iteration
GPU profile traces each contain36478kernel events; per-rank kernel-union gap
fractions5.34%/10.37% over~0.94s. Profiling affects timing and is excluded from
headline rates; gaps alone do not prove CPU causality. Gate-only engine summaries
in this original run cover prior scheduler history; only timed common-window
summaries are used above. Their original receipts are preserved.

K128 failed before a valid window:106/128workers failed, agent threads peaked6745
versus4289atK64, and GPU sampler process creation raisedBlockingIOError. NoK128
throughput or GPU saturation claim. Original worker stderr and cgroupPID limit
were not collected; the precise first exception remains unknown. A local bounded
exact-image process test reproduced native-thread exhaustion and validated
worker-only native-thread=1. Recovery, remaining budget and caveats are registered
in [request026](../../lab/requests/026-runtime-saturation-kv-pressure.md).

Owned51135854 destroyed/absence verified; watchdog stopped after parent teardown.
Essential3433files/12025743bytes remote=localSHA256
80be72fc5cafea23d29548db1ad08e6510c9b9ef9e71c077fb6c003edb8c801d.
Bulk is intentionally partial. Hourly estimate$2.706424, not an invoice.
MTP saturation and actual long-context pressure are pending the recovery run.
