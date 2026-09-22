# Graph storage, execution and hardware observability

Status: implemented collection/reporting extension, **live appliance validation
pending**. Bead: seocho-tq2c. Added 2026-09-22 at the owner's request. This expands
observations within Runtime / Tool / Agent; it does not launch a hardware,
procedure, store-format, offload or DB-product comparison.

## Questions and linked views

| View | Question | Measurements |
| --- | --- | --- |
| Runtime Performance | Does graph execution leave LLM supply gaps, or compete with serving for CPU/memory/I/O? | DB version/edition/store format, actual query runtime, Bolt workers/transactions, root-thread CPU execution and runnable wait, CPU/NUMA affinity, quota throttling, NUMA allocation, device I/O and pressure, page cache, heap/GC, PMU/cache/TLB, bandwidth, KV transfers |
| Tool Call | What physical work produces the result? | Cypher/procedure identity, plan/operator shape, logical DB hits, plan rows, actual traversal visits/frontier, degree/type/direction strata, pool/server/receive/serialization intervals, return rows/bytes/tokens, projection build/refresh costs |
| Agent Execution | How does that work change inference demand? | Calls/repairs, context growth, generated query/answer tokens, tool-to-next-LLM submission gap, completion and separately evaluated answer equivalence |

Keep output tok/s/GPU primary, alongside task/s, task input/output tokens and
quality. DB queries/s, p50/p95 and CPU-seconds/query are explanatory metrics.
Faster DB queries may reduce latency or required K without raising GPU throughput
when the LLM is already saturated. A procedure that returns different evidence
is not an equivalent implementation just because it is faster.

## What is implemented versus required instrumentation

The machine-readable catalog is `GRAPH_OBSERVATIONS` in
`src/seocho/eval/agentic_os_graph_system.py`. Every exported condition gets its
coverage rows, including historical conditions that lack the new samples.

| Surface | Implementation now | Remaining boundary |
| --- | --- | --- |
| CPU/RSS/threads/process I/O | Reuses existing `host-process.csv` process-tree sampler | RSS may double-count shared pages; I/O is not query-level physical reads |
| Thread scheduling | Optional `/proc/PID/task/TID/schedstat`, with PID/TID start identities and scan cost | Root process only; no descendants. Churn, counter resets, sampling gaps or disabled schedstats make affected deltas unknown |
| CPU/NUMA placement | Root thread's allowed CPU/node masks | Allowed is not actual placement; does not assert all threads share affinity |
| CPU limits/pressure | Sampler-visible `cpu.max`, `cpu.stat`, CPU/memory/I/O PSI totals | Cgroup mount may cover multiple services; no false per-DB attribution |
| NUMA memory | Cgroup `memory.numa_stat` anon/file/shmem bytes by node | Allocation is not remote access or bandwidth; shmem is not disjoint from file |
| Storage I/O | `/proc/diskstats` numeric series by major:minor | Host-wide; do not sum stacked devices. Disk sectors are 512-byte accounting units. No inferred DB-device assignment |
| DB capability | Pre-workload bounded read-only components, selected settings and selected procedure inventory | Permission/edition failures recorded by class; availability does not prove a procedure executed |
| Store structure | Explicit coverage field with required source | Verify existing-store metadata first. `db.format` and product name do not establish existing store format; linked-list versus block/tree remains unknown until then |
| Actual runtime | Bounded runtime label from existing out-of-band PROFILE replay | Requested runtime and available edition features are separate. Does not add PROFILE to throughput calls |
| Plans/DB hits | Reuses existing plan audit/replay receipts | Plan rows are not frontier/visited nodes; DB hits are not disk reads |
| JVM/page-cache/Bolt internals | Coverage/source contract | Requires version-verified exporter/JMX/JFR; not synthesized from RSS, OS Cached or process thread count |
| Procedure/traversal internals | Coverage/source contract | Requires invocation spans or instrumented procedure/cursor. No visits/frontier guessed from returned rows |
| IPC/LLC/TLB, memory bandwidth, remote accesses | Coverage/source contract | Separate supported PMU/uncore/perf run; event definitions, scope and multiplexing must be recorded |
| GDS/OpenGDS projection | Coverage/source contract | Build time, persistent bytes, refresh/version and algorithm time measured separately |
| Offload transfers | Existing exported backend metrics if present; coverage/source contract | No inference from process read/write bytes; DB and KV memory allocation are separate |

Coverage status `not_collected` means unknown with null value. `sampled` means
the source has samples covering the common window, not that every metric in a
group is present. `partial` preserves incomplete evidence. `available_separate_evidence`
links other receipts such as preflight capabilities or PROFILE; it does not
claim they were collected in the throughput window. This status is not answer
quality or experimental gate admission.

## Enable and reproduce

The existing workload wrapper accepts `--graph-system-observer`, or set
`SEOCHO_GRAPH_SYSTEM_OBSERVER=1` in its environment before the session. It probes
DB capabilities before starting the workload, then collects graph-system samples
every five seconds alongside existing samplers. Default behavior stays unchanged.
Record this setting in a new source bundle/preflight and compare observer OFF/ON
before claiming overhead is negligible. Existing pinned bundles are immutable.

The capability probe uses `NEO4J_URI` (default local Bolt), `NEO4J_USER`, and
`NEO4J_PASSWORD`. Missing credentials or unsupported commands yield explicit
gaps. Supply `SEOCHO_EXPERIMENT_RUN_ID` and `SEOCHO_WORKSPACE_ID` for wrapper
provenance; missing identity stays null. To collect a standalone receipt:

```bash
PYTHONPATH=src python3 scripts/benchmarks/probe_agentic_os_graph_capabilities.py \
  --output "$PASS_ROOT/workload/graph-capabilities.json" \
  --run-id "$RUN_ID" --workspace-id "$WORKSPACE_ID"
```

This command refuses to overwrite a receipt. It does not install plugins, change
DB settings, reset caches or execute arbitrary user queries. Metadata commands
have a ten-second transaction timeout; acquisition/connect are also bounded.

New artifacts: `workload/graph-system.jsonl`,
`workload/graph-capabilities.json`. Existing essential-archive globs already
include both. Common-window factors and the characterization exporter read the
new source. Render a fresh report directory from an existing run:

```bash
PYTHONPATH=src python3 scripts/benchmarks/summarize_agentic_os_characterization.py \
  --run-root "$RUN_ROOT" --output-dir "$NEW_REPORT_DIR"
```

The HTML places the thread timeline and process summary under Runtime, capability
and PROFILE coverage under Tool, and linked demand/quality coverage under Agent.
The timestamped numeric series remain in JSON for device/NUMA follow-up plots.
Historical missing fields are displayed as uncollected, never backfilled.

## Correlation and counter rules

- Preserve workspace_id, run_id, condition/pass, task_id, llm_request_id,
  tool_call_id, trace_id/span_id and parent IDs. Shared device/cgroup samples join
  by the recorded time window only; that is correlation, not exact attribution.
- Keep source timestamp and monotonic time, interval, gaps, permission failures,
  units, observer cost, process scope and service restart identity. Never assign
  a shared sample's entire cost to each concurrent task.
- Root-thread cumulative deltas require a complete stable PID/TID identity set,
  no counter decrease, and full sample coverage (maximum gap 12.5 seconds).
  Their interval is the first/last actual sample, shown separately from the
  throughput window. Short-lived threads may be missed; do not claim exhaustive
  accounting. Disabled schedstats cannot establish zero runnable wait.
- DB page-cache faults, CPU minor/major faults, LLC/TLB misses and physical disk
  reads are different events. Document the exact source of each.
- One DB query's parallel workers, Bolt transaction workers, client connections,
  application K and available CPU cores remain separate quantities.
- Full raw query text, parameters, graph contents, process argv/environment,
  connection addresses and exception messages never enter shareable exports.
  Export bounded labels/digests; keep task/TID detail in artifacts rather than
  high-cardinality Prometheus labels. No new vendor-specific OTel dependency.

## Product-level investigations enabled by this contract

First confirm the pinned DB build, actual store format, supported procedures and
runtime. Compare index-anchored short lookups, typed/directed neighbor expansion,
multi-hop frontier growth and scans/aggregation with identical result semantics.
Measure whether concurrent independent traversals hide memory latency or instead
increase cache/TLB/bandwidth contention. Logical graph proximity is not proof of
physical page locality.

Then compare query admission limits and CPU/NUMA placement at fixed total
resources. Procedure changes can batch independent work or remove redundant
visits; they must preserve transaction/thread safety and result equivalence.
Cursor/prefetch/storage-layout changes require a separately versioned engine
build and correctness tests. Engine changes, store migrations, hardware changes,
cache allocation and paid experiments are interventions, not consequences of
enabling this observer.

## References

- [Neo4j store formats](https://neo4j.com/docs/operations-manual/current/database-internals/store-formats/)
- [Bolt worker pool](https://neo4j.com/docs/operations-manual/current/performance/bolt-thread-pool-configuration/)
- [Runtime concepts](https://neo4j.com/docs/cypher-manual/current/planning-and-tuning/runtimes/concepts/)
- [Procedure development](https://neo4j.com/docs/java-reference/current/extending-neo4j/procedures/)
- [DozerDB capabilities](https://dozerdb.org/)

Current documentation describes candidate mechanisms; it does not establish
support in the historical DozerDB 5.26.3 appliance. Performance claims still
require live services and an admitted common-window measurement.
