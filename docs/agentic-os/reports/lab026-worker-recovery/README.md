# Lab026: worker thread recovery and baseline K128

Source `b5ce2327`, run `lab-s25b`. The pinned model, vLLM/Dozer versions,
dataset, two A100 SXM4 80GB GPUs, TP=2, observer and steady-state measurement
policy follow [request 026](../../lab/requests/026-runtime-saturation-kv-pressure.md).
Native numerical-library threads are limited to one in each client worker.
There are no MTP or long-context measurements in this run.

| Application K | Output tok/s/GPU | task/s | Agent thread peak |
| --- | ---: | ---: | ---: |
| 8 | 121.459 | 0.5494 | 33 |
| 64 | 436.707 | 1.8838 | 257 |
| 128 | 485.151 | 2.0915 | 513 |

All three conditions passed exact request-stage and engine admission. Moving
from K64 to K128 increased generation throughput by 11.1%; this alone does not
establish a plateau. See [Runtime / Tool / Agent charts](figures/index.html) and [linked task views](index.html) and
[engine/counter receipts](conditions.json). The export contains 1,497 tasks,
7,035 requests and 6,146 tool calls. Behavioral charts use contained tasks;
throughput uses the common engine counter window and two physical GPUs.

K256 has no valid window: 40 of 256 worker starts returned OSError. Cgroup
PID usage peaked at 1,838 of 7,680 with zero limit events; no OOM event was
observed. The original coordinator omitted numeric errno. A bounded local
96-child test at RLIMIT_NOFILE=256 reproduced EMFILE during concurrent process
creation; serialized creation started all 96. The subsequent fix serializes
startup only and records numeric errno/limits. This demonstrates a possible
mechanism, not the exact missing live errno or GPU saturation.

Instance 51161376 was destroyed and independently verified absent. Essential
copy: 5,219 files, 7,280,675 bytes, SHA256
`6d07cc9904aefd0c46dc3e866e25697fe3cf3f686e026351bda931f78093f078`.
Bulk copy remains intentionally partial. Hourly cost estimate: $2.285893;
this is not an invoice. Original receipts are preserved.
