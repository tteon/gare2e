# Workload factors and observability

## Three linked characterization views

The owner's latest requirement is categorized **results**, following
[Agentic AI Workload Characteristics, §§4–6](https://arxiv.org/pdf/2605.26297),
not three disconnected dashboards or an expanded intervention matrix.

| View | Result tables and comparisons |
| --- | --- |
| Agent Execution Characterization | Per-task LLM/tool calls, total and maximum input length, output tokens by response kind, context growth, repair/empty-result retries, terminal and answer status; successful versus unsuccessful completion |
| Runtime Performance Characterization | Common-window output tok/s/GPU and task/s; request queue/TTFT/prefill/decode/TPOT; linked task/LLM/tool/GPU timeline; K8 versus high K, baseline versus MTP, KV/cache/preemption observations |
| Tool Call Characterization | Lexical Cypher shape versus tool latency, rows/bytes, validation/submit/hydrate/consume/serialization timing and error class; schema observations separate from graph rows |

Use `(run_id, case_id)` to distinguish repeated questions. Each task records an
actual wall-clock anchor; `step_id` identifies one LLM invocation, and
`tool_call_id` plus `parent_request_id` connects every tool to the generating
LLM request. The existing trace/span IDs connect serving and graph spans.
Historical task timelines anchored only by question ID require re-export from
their lifecycle receipts; do not reuse those timestamps for repeated schedules.

Runtime percentages must state their denominator: request decode/LLM time is
not a fraction of GPU-exclusive time. TP ranks must not duplicate output-token
counters. Cypher/tool-argument generation is output; returned graph data is a
later input. Where component token counts are unexported, content-free UTF-8
message byte counts are labeled **bytes**, not substituted for tokens. Output
totals grouped by response kind are not exact token attribution within a mixed
response. Semantic answer accuracy remains unscored unless a separate evaluator
supplies it; the completion/evidence funnel is not accuracy.

The three new grammar K8 controls use the same 16-logical-CPU setup and query
ratios 0/.5/1 as the K64 conditions. This adds a load interaction comparison
within the existing cost cap. Offloading remains a future hypothesis requiring
actual long contexts, reuse and memory pressure. A low-pressure null result
does not reject it.

The paper's cache/decode findings describe resident contexts without capacity
eviction/offloading. Its hardware and vLLM version differ from this appliance;
its reported percentages are background, not this run's results.

Detailed engine scheduled/decode sequence counts, active versus reclaimable KV,
and grammar/kernel-gap CPU tracing remain explicit instrumentation gaps where
the pinned engine does not export them. Do not replace these with running-request
gauges, iteration-token averages or sums of overlapping request durations.

The research question is how graph retrieval and agent execution shape the
request lengths, arrival gaps and prefix reuse presented to vLLM, and which
serving strategy improves sustainable output tokens/s/GPU for that workload.
Always accompany token throughput with task completions/s, latency, output length
and the answer/evidence funnel. More generated text is not necessarily more useful
work. A completed runner is not a correct answer.

## Keep observation separate from intervention

For lab 024, start with request supply (application K) and baseline versus MTP.
The other approved comparisons diagnose the high-load regime: three fixed-request
replay substrates, block/chunk settings, grammar ratio/CPU affinity, and TP2 versus
two TP1 replicas. They are staged comparisons, not a Cartesian product.

Keep graph/dataset, model revision, request protocol, DB heap/page-cache budget,
and context ceiling fixed within each comparison. The latest memory discussion
adds observations; it does **not** add DB-cache allocation, offloading capacity,
eviction-policy or long-context sweeps to this paid run.

Promote a new factor to an intervention only after a relevant bottleneck appears:

| Evidence | Next controlled experiment |
| --- | --- |
| Higher K closes LLM supply gaps and raises throughput | Locate the supply plateau before changing cache policies |
| Grammar ratio increases server CPU demand and latency | Compare fixed logical CPU affinity at the same protocol and K |
| Repeated prefill, prefix reuse and memory pressure coincide | Fixed prompt trace; vary KV capacity or reuse interval |
| DB wait and physical read bytes grow with working set | Fixed Cypher/parameter replay; vary DB page-cache capacity |
| DB and KV compete within a confirmed shared DRAM limit | Fix heap and total budget; vary page-cache versus CPU-KV allocation |

These are diagnosis rules, not sufficient proofs of causality. Do not infer KV
eviction from a new entity, long tool wait, or cache-hit ratio alone.

## Measurement surface

| Layer | Observed source | Interpretation and limits |
| --- | --- | --- |
| Application supply | Coordinator lifecycle, configured K, request/tool intervals | Active tasks include tool wait. Host queue wait differs from server queue wait. |
| Request shape | Client receipts: input/output/cached tokens, turn and tool counts | Actual distributions, separate from max_model_len. Fixed replay preserves request payloads; it does not score answers. |
| Serving | Prometheus running/waiting requests, generation tokens, iteration histogram; exact trace joins for TTFT/queue/prefill/decode | Running requests and iteration tokens are not actual scheduled decode sequence count. Missing stages remain unknown. |
| KV reuse/pressure | Exported cached-token counter, computed-prefill histogram, KV usage, preemptions, prefix queries/hits; offload counters if exposed | No exported offload metric means unknown, not zero. Prefix lookup tokens and actual computed-prefill tokens have different denominators. |
| Process resources | Explicit service PID trees: CPU, RSS, threads, read/write bytes | Tree RSS can double-count shared pages; process I/O is not Neo4j logical DB hits. PID membership changes invalidate cumulative deltas. |
| Host/cgroup memory | `host-process-memory.jsonl`: MemAvailable, Cached, Shmem, swap, cgroup current/limit/file/anon/events | Host and sampler-visible cgroup scopes are distinct. Host Cached includes shmem. A missing v2 interface is unknown. Do not sum RSS and file cache as disjoint allocations. |
| Configuration | `hardware.json`: DB heap/page-cache settings, server concurrency/replica settings, CPU affinity budget | Settings are budgets, not observed consumption. The restart receipt records actual selected CPU affinity. CPU count means logical CPUs. |
| DB internals | Existing query receipt timing, rows and optional out-of-band PROFILE evidence | JVM heap occupancy/GC and DB page-cache residency are currently unexported. PROFILE must stay outside timed agent execution. |

`summarize_agentic_os_factors.py --pass-root <pass>` writes `otel/factors.json`
and `otel/throughput.json`. Rates use the same validated inner scrape interval
inside the warmup-excluded backlog window. Missing samples, resets or gaps >2.5 s
invalidate throughput. Length/latency summaries use fully contained cases and
explicitly retain their finite-window selection limitation. Physical GPU count
comes from observed UUIDs; validate that it matches the admitted allocation.

For replica endpoints, raw metric parsing sums label series. In particular, the
KV utilization gauge is a **sum of replica fractions**, not the utilization of
one GPU or a capacity-weighted fleet fraction. Preserve per-replica evidence
before comparing cache occupancy; a combined ratio alone is insufficient.

All shared artifacts are content-free. Frozen prompt/query/observation captures
use mode-0600 `.private.` files, remain excluded from shareable trace packages,
and are only used on the owned appliance or in restricted local artifacts.

## Causal boundaries

Graph representations may reduce evidence tokens, but schema, repeated names,
paths and source passages can reverse this. Measure equivalent evidence before
claiming compression. A normal external tool loop ends the LLM request before
the DB call; it does not continuously occupy GPU compute across the task.

Prefix caching saves repeated prefill work; it does not make long-context decode
free. See the [vLLM APC limits](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/).
New entity access is a DB locality question; KV reuse concerns token prefixes.
The prior n-gram output collapse is an anomaly in the tested version/model/
sampling/parser combination. It does not establish that n-gram speculative
decoding generally breaks Cypher; no algorithm-wide conclusion is justified.
DB page cache, JVM heap and OS file cache are separate accounting categories;
see [Neo4j memory configuration](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/).
The actual runtime is the pinned DozerDB appliance: current Neo4j documentation
does not establish which metrics that edition/version exports.

The cached-token, preemption and computed-prefill families were checked in the
local `vllm/vllm-openai:v0.27.1` image before adding them to the allowlist.
Actual availability must still be recorded on the live endpoint.

## Replica fractions and additive observation

The current router sums all numeric series. This is correct for request counts
and generation counters but its sum of per-replica KV fractions is **not** a
single cache-usage fraction. Reports label it as a sum, leave a global fraction
unknown, and use separately sampled backend endpoints when available.
`sample_agentic_os_replica_metrics.py` adds two content-free metrics GETs per
cycle, records sample timestamps/scrape cost and stops on pass completion or a
bounded deadline. It never changes the request workload. Full-window coverage
is checked separately; late or missing samples are not labeled complete coverage.
Lab-s23b received this observer additively after startup; its source/remote hashes
and staging receipt are preserved. Treat the extra observation cost as part of
the measured setup. It is not a runtime scheduler or memory-policy intervention.

## Lab-s23b trace propagation audit

The deployed replica router omitted W3C traceparent/tracestate from its forwarded
headers. All1,918 replica client requests lack an exact server parent match even
though standalone vLLM server spans exist. Keep request-level server stages null;
do not join by timestamps or token counts. Single-engine contained-cohort requests
all match. The router allowlist is now fixed and locally regression-tested; this
fix was made after teardown and has not been verified in a new live replica run.
Future essential collection also includes the content-free router receipt JSONL.

Replica samples covered the complete inner window and exposed unequal mean
running requests (47.60 versus13.33) and KV fractions (0.648 versus0.169).
The measured TP comparison therefore describes this sticky routing configuration;
it does not establish the intrinsic performance ranking of balanced TP layouts.
