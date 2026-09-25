# Graph optimization evidence and observability

This extension reuses the gare2e executor, trace export, result registry and web
viewer. It does not create a second agent framework or modify the storage engine.
Implementation and live measurements are separate gates. Bead: seocho-ix30.

## What the operator sees

Select a condition, then a task. The existing Agent/LLM/Tool waterfall is followed
by query-attempt logs, actual DB submissions, cache reuse links, two plan tables,
versioned quality evaluations and evidence-linked insight cards. Search logs by
query ID, error or outcome; filter timed, diagnostic and auxiliary executions.
Download content-free JSONL or OTLP Logs JSON. Every insight is labeled observed,
missing, limitation or hypothesis. Missing DB counters stay null, including in
legacy imported plans; no claim is made from a previously synthesized zero.

The query log does not contain raw Cypher, parameters, answers or hidden
reasoning. For exact local replay, operators can enable a separate mode-0600
private input file. Those files never enter the shared registry automatically.

## Backend responsibilities

| System | Role in this design | Boundary |
| --- | --- | --- |
| OpenTelemetry | Common trace/span identity, request propagation and typed logs; existing harness emits trace spans | Instrumentation/transport contract, not a database or UI |
| Prometheus | Low-cardinality operational counters, histograms, CPU/GPU/DB/serving gauges; exemplars link to a trace | Never label series with question/query/trace IDs or raw Cypher |
| Tempo | Store OTel traces and search by trace ID/TraceQL | A trace is not CPU scheduling, traversal or GPU kernel evidence |
| Grafana | Navigate metric → exemplar → trace; correlate logs when a log backend is configured | Dashboards do not establish treatment comparability or answer quality |
| LangSmith | Optional LLM run/dataset/evaluator view and versioned evaluation feedback; OTel ingest is supported | No mandatory SDK coupling, automatic payload export, or implicit quality score |
| gare2e platform | Frozen experiment contracts, query/plan/evidence comparison, admission and missing coverage | Offline registry results are not a live service-monitoring time series |

The built-in `/metrics` endpoint describes registry inventory as gauges. It is
not vLLM or DB throughput. Workload Prometheus scrapes remain with existing
appliance samplers and service exporters. Scrape the viewer only with the same
Authorization boundary as its API. No high-cardinality labels are generated.

OTLP log downloads use the measured UNIX timestamp and recorded trace ID. Missing
span IDs remain absent. Exported logs are observations of attempts, not invented
DB spans. Existing OTel spans remain the canonical waterfall. No backend gets
uploaded data by importing or browsing a run. Operators may send reviewed OTLP
records through their own Collector to Tempo/LangSmith-compatible destinations;
logs require a log backend such as Loki, not Tempo. External backend deployments
and credentials are separate from local viewer availability.

Sources checked 2026-09-24:
[OTel signals](https://opentelemetry.io/docs/concepts/signals/),
[Prometheus instrumentation](https://prometheus.io/docs/practices/instrumentation/),
[Tempo TraceQL](https://grafana.com/docs/tempo/latest/traceql/),
[Grafana Tempo datasource](https://grafana.com/docs/grafana/latest/datasources/tempo/),
[LangSmith OTel](https://docs.langchain.com/langsmith/trace-with-opentelemetry).
These describe capabilities; they are not evidence of a configured connection.

## Enable query evidence in existing runners

Set `GARE2E_QUERY_EVIDENCE_DIR` to a private artifact directory for either
`run_direct_react_text2cypher.py` or `run_grbench_graphcot_react.py`.
`GARE2E_GRAPH_SNAPSHOT` must name the frozen import/snapshot hash when enabling
`GARE2E_QUERY_MODE=exact_result_reuse`; the default mode is `raw`.
Set `GARE2E_PRIVATE_REPLAY=1` only when private exact query/parameter replay inputs
are required. Register these settings in the experiment measurement contract.
Every worker creates its own file. Session boundaries clear reuse state.

Exact reuse keys include snapshot, workspace, database, session, statement,
parameters, output caps and result mode. Validation still runs on a cache hit.
Errors, capped results, zero-row diagnostic hints and volatile queries do not
populate the cache. Bounds are 64 entries and 1 MB per session by default.
A cache hit avoids DB submission, not the agent tool call or LLM turn.

Parameterized PROFILE uses the same enforced workspace and timeout. Keep PROFILE
outside all timed workloads: a replay after one task can interfere with another
concurrent task. Never enable `--profile-query-plans` in throughput passes.
Diagnostic replay needs the exact original parameter and snapshot identities.
The result of PROFILE is evidence for that replay, not the original query cache.

## Import evidence

Put `queries.jsonl` alongside the existing `characterization.json` and CSVs.
Rows use `gare2e.query_attempt.v1`, recorded timestamps, query identity, outcome,
execution scope, receipt and optional normalized plan. The existing import CLI
recognizes the file automatically, hashes it and includes it in immutable run
identity. Join by exact `trace_id`, or explicit `pass` and the platform `task_id`.
Ambiguous, orphan and duplicate records fail import; no fuzzy time/case join.
Only allowlisted numeric fields and bounded identifiers reach the registry.

Optional `quality.jsonl` uses the same join and requires `evaluator_revision`.
Scores in [0,1] are `answer_correctness`, `evidence_recall`, `evidence_precision`,
`faithfulness`, `required_entity_coverage`, `required_relationship_coverage`.
`evidence_digest` identifies the evaluated evidence. Absent dimensions remain
unknown. Store evaluator prompts, gold inputs and detailed rationales privately.

## Research contract and stages

Text2Cypher RAW preserves generated read-safe Cypher. GraphCoT-hop RAW preserves
fixed parameterized hop templates. Compare treatments within a lane. GRBench
semantic types reside in node/relationship properties, so ontology label/type
rewrites require a verified physical mapping and snapshot invariant audit.
Do not silently add new labels/indexes to an otherwise fixed graph experiment.

Use named interventions; historical A/B/C/D/E already mean other things. The
currently executable first intervention is exact-result reuse. QUERY_OPT,
ONTOLOGY_OPT, broader MULTITURN_OPT and LOCALITY_OPT remain staged treatments,
not aliases for no-op implementations. Every proposed rewrite needs preconditions,
unchanged result contracts and a fallback reason. Dependent Q2 cannot be merged
before seeing Q1 without explicitly introducing speculation.

First audit preserved plans/counters and tag W1–W8 as overlapping workload tags.
Then run fixed-query result parity plus observer OFF/ON diagnostics. Compare raw
versus exact reuse with paired ordering and explicit cold/warm protocol. Only
then add proof-backed ontology predicates, batching and budgeted prefetch.
Live-agent quality is separate from replay parity. Future-intent replay is an
oracle diagnostic, not a deployable online policy.

Freeze questions, graph/model revisions, prompts, tool contracts, limits,
evaluator, hardware, concurrency and warmup. Separate DB page cache, OS cache,
query/JIT cache and LLM prefix cache. Runtime comparison needs capability and
actual-runtime evidence, not merely a requested `parallel` setting.

Acceptance: complete query-attempt identity/outcome joins, no fabricated zeros,
semantic parity for equivalent-query comparisons, quality noninferiority margin
registered before a main run, optimizer and prefetch cost included, paired
repetitions and uncertainty. A pilot does not establish a p99 or a general GPU
throughput benefit. DB Hits, pages, physical reads, CPU stalls and GPU supply gaps
are separate claims. Preserve failed and negative results.

## Local backend validation

The optional [Linux loopback companion](../../docker/agentic-os-appliance/observability/README.md)
provides pinned Collector, Tempo, Prometheus and Grafana containers. A real viewer
HTTP request was timed and exported as a span plus correlated log on 2026-09-25.
Collector accepted both signals, Tempo returned the trace by ID, the log file
contained the event, Prometheus scraped 317 imported query attempts, and Grafana
reported a healthy database. This validates those local integration paths only;
it is not a DozerDB/GPU performance result or LangSmith connectivity claim.

The lab021 audit used 317 original profiles containing 2,309 operators. All
operators had DB-hit and page-miss counters, so the historical zero-miss result
was not caused by the parser's missing-to-zero bug. The new parser protects
other payload shapes and incomplete captures. Parent topology was not retained
in those old receipts; it remains unknown. Cardinality error can be recomputed
from the preserved estimated and actual row counts.


## First live pilot

[Lab027 result](reports/lab027-graph-evidence/README.md) records the measured
fixed-query reuse and absence of reuse across24 agent comparison sessions. The
existing platform contains all16 new cells with167 attempts. Query intervals
have their own waterfall lane; diagnostic PROFILE and cached results have
separate lanes. Exact server trace joins cover119/122requests, never a guessed
join for the missing3.

To reanalyze existing runner receipts, use:

```bash
PYTHONPATH=src:. python3 scripts/benchmarks/export_graph_query_evidence.py \
  --run-root "$PILOT_ROOT" --output-dir "$REVIEWED_EXPORT" \
  --server-spans "$ORIGINAL_COLLECTOR_SPANS"
```

Use a fresh output directory. Omit `--server-spans` when it was not collected.
Then import with the existing experiment platform CLI and reviewed measurement
metadata. An observation archive is not a throughput window; the adapter leaves
throughput unknown. Query-record workspace and registry workspace remain distinct.
