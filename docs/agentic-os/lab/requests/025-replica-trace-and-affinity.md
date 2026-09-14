---
id: 025
title: Replica trace admission and per-task affinity comparison
state: prepared and preflight passed; bounded continuation within remaining approved money
created: 2026-09-15
bead: seocho-odhn
---

## Continuity and incremental hypothesis

Closest evidence is lab024/lab-s23b: TP2 K64 439.59 versus sticky2xTP1
273.73 output tok/s/GPU. Replica running load47.60 versus13.33 and missing exact
server parent joins on all1,918 replica requests prevent a pure TP-layout claim.
Lab024 data and its limitations remain unchanged. User asked to continue.

This follow-up first validates the existing W3C forwarding repair, then tests
whether per-execution task round robin changes replica supply and throughput
compared with content-hash sticky assignment. It does not promise equal active
load: equal admission counts differ from decode demand and task duration.
Repeating TP2/sticky on the same new host supplies controls and validates the
previously missing linkage; this is an intentional replication, not new evidence
from the old receipts. Long-context/offload/grammar CPU are separate follow-ups.

## Implemented contract

- Default sticky routing remains unchanged. New `task-rr` alternates new W3C
  task trace IDs across replicas and retains the same assignment on later turns.
  The map holds at most16,384 assignments for one router lifetime; it never evicts
  an active affinity. New admissions fail503 at capacity; missing valid context
  fails400. Each benchmark run remains below the registered map limit.
- Router receipts v2 record only validated trace_id/parent_span_id, backend,
  assignment count and existing timing/digest/size fields. Prompts, answers,
  authorization and arbitrary tracestate contents are not recorded.
- `audit_agentic_os_trace_gate.py` requires nonempty unique valid client IDs,
  exactly one server parent match and finite nonnegative queue/TTFT/prefill/decode
  values. Replica requests additionally require one successful router receipt;
  timed replica passes require observed requests on both backends. Missing,
  duplicate, incomplete or ambiguous evidence fails closed. Collector flush is
  allowed15s; timestamps/token counts are never used to invent a join.
- The session automatically samples each backend and terminates that observer
  after workload completion/failure. Timed comparisons require a valid common
  window, two observed physical GPUs and complete per-backend window coverage.
- Essential collection includes router receipts and trace-admission receipts.
  Reports display recorded router policy. Previous reports are not regenerated.

## Registered queue and fixed conditions

`run_agentic_os_throughput_session.py --replica-audit --print-plan` exposes:

| Order | Serving/routing | Application K | Purpose |
| --- | --- | ---: | --- |
| 1 | TP2 | 1 | Graph case105 and exact stage linkage gate |
| 2 | TP2 | 64 | New same-host control |
| 3 | 2xTP1 sticky | 1 | Graph case105 and routed stage linkage gate |
| 4 | 2xTP1 sticky | 64 | Replicate failed-linkage setup with repair |
| 5 | 2xTP1 task-rr | 1 | Graph case105 and task-affinity linkage gate |
| 6 | 2xTP1 task-rr | 64 | New routing intervention |

A small gate covers whichever backend receives its task; the timed pass requires
both backends. Each configuration restarts services and rechecks readiness;
policy and warmup/order are recorded. Single pass per condition, no confidence
interval or accuracy claim. Keep the lab024 pinned A100 SXM80GB NVLink pair,
model revision/vLLM0.27.1/Dozer/Physics90/S1/DB heap8g/pagecache24g/maxlen32768/
maxseq128/maxbatch8192 and90s dispatch window. Report output tok/s/GPU, task/s,
per-task input/output, per-backend running/KV, client/server/tool timelines and
valid-empty/error/termination distributions. Do not call K64 proven saturation.

## Resource bound and authorization

User asked to continue. Prior extension usage is$1.9208 against approved$5.40,
leaving approximately$3.4792. That spend is retained in the ledger. The former
5400s/90min bound was an individual instance watchdog; the next instance has a
stricter3600s/60min bound. No additional monetary budget is requested or assumed.

Prepared launcher mode `--replica-audit --cuda-forward-compat` uses a NEW lab-s24
root, <=$3.40/h disk-inclusive, hard3600s watchdog (maximum$3.40), remote2700s,
session2300s with300s reserve and bounded collection. Combined extension usage
is capped at approximately$5.3208 in hourly estimates, below the existing$5.40.
This is a new, explicitly scoped validation run under the user's continuation;
not an automatic repetition of the completed matrix or a reset of spend.

The launcher retains NVLink/A100/CPU/price admission, exact-image/source checks,
CUDA compatibility probe, graph/trace gates, independent watchdog and verified
teardown. Preserve all peer instances and previous artifacts. Only the private
gare2e/agentic-os-e2e branch receives commits from a temporary worktree.

## Validation result

54 focused tests passed, including actual loopback HTTP through the router to
two lightweight test backends. This verifies transport/contracts only, not vLLM
compatibility or throughput. Historical lab-s23b audit passes TP2 2,278/2,278
requests with all four stages and rejects replica0/1,918 exact matches. This
confirms the gate detects the recorded failure; it does not repair historical data.
Ruff passed. Exact-image source preflight and basic-CI checkpoint are recorded
in HANDOFF after completion. Live W3C correction and routing effect remain unmeasured.

Exact-image preflight passed source/standalone checksum, image identity, unchanged
Dozer runtime, exact changed-file bundle hashes, new entrypoint parsing and
built-in CUDA compatibility libraries. No GPU exercised in local preflight.
Bundle SHA256 `8a4c7aad21acc988cac8311df1b730585072ec7d45091eafda079bb69d073884`.
Basic CI1112 pass3skip; final same five existing SSH identity fixture violations.
Live gates and measurements remain pending; do not treat readiness as results.
