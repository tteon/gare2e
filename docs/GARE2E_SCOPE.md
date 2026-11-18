# GARE2E scope and provenance

Scope clarified 2026-11-18. Existing report inputs and results are preserved.

GARE2E develops controlled graph-agent experiments and the evidence needed to
inspect them. Its contribution is the experiment/measurement path and the
read-only comparison platform, built on existing agent and graph components.

## Repository-specific work

| Area | Tracked implementation | Evidence / contract |
|---|---|---|
| Co-located agent/LLM/graph experiments | `scripts/benchmarks/run_agentic_os_colocated_workload.py`, `docker/agentic-os-appliance/` | [Harness](agentic-os/HARNESS.md) |
| Frozen cohorts, schedules and bounded lifecycle | `scripts/benchmarks/build_agentic_os_cohort_schedule.py`, `prepare_agentic_os_throughput.py`, `vast_colocated_agentic_os_run.py` under `scripts/benchmarks/` | [Experiment agent rules](agentic-os/AGENTS.md) |
| Agent / Runtime / Tool exports and chart reproduction | `src/seocho/eval/agentic_os_characterization.py`, `scripts/benchmarks/summarize_agentic_os_characterization.py`, `render_agentic_os_characterization_figures.py` under `scripts/benchmarks/` | [Metric definitions](agentic-os/OBSERVABILITY_FACTORS.md), [chartbook](agentic-os/reports/characterization-charts/README.md) |
| Exact request joins and graph-system coverage | `src/seocho/eval/agentic_os_trace.py`, `src/seocho/eval/agentic_os_graph_system.py` | [Graph-system contract](agentic-os/GRAPH_SYSTEM_OBSERVABILITY.md) |
| Shared immutable result registry and task viewer | `src/seocho/eval/experiment_platform.py`, `runtime/experiment_platform.py`, `runtime/experiment_platform_web/` | [Platform](agentic-os/EXPERIMENT_PLATFORM.md) |
| Query attempts, plan/quality records and bounded exact reuse | `src/seocho/eval/query_evidence.py`, `src/seocho/eval/experiment_evidence.py`, `scripts/benchmarks/export_graph_query_evidence.py` | [Evidence contract](agentic-os/GRAPH_OPTIMIZATION_OBSERVABILITY.md), [pilot](agentic-os/reports/lab027-graph-evidence/README.md) |
| Native-agent workload/data-source adapters | `scripts/benchmarks/pi_native/`, `scripts/benchmarks/load_benchmark_tasks.py` | [Adapter guide](../scripts/benchmarks/pi_native/README.md), [task loader](BENCHMARK_TASK_LOADER.md) |

The paths above identify additions made along this experiment repository's
history. Existing components and algorithms retain their original attribution;
a new adapter or measurement does not establish a new underlying algorithm.

## Historical boundary

The graph-agent experiment work starts after shared SEOCHO parent
[`c3a1871`](https://github.com/tteon/seocho/commit/c3a1871cfe83f89b0f203a01603c00e7ef1126cb).
That parent already contains the general SDK and FinBench-style generator.

| Repository milestone | Record |
|---|---|
| 2026-09-08: co-located graph-agent lane, gates and telemetry | [`5422522`](https://github.com/tteon/gare2e/commit/542252267f78a0e5018848dbef1cd2aaa6355399) |
| 2026-09-15: portable experiment and agent guides | [`72ab088`](https://github.com/tteon/gare2e/commit/72ab0889f826d8f15139730aab47680047549026) |
| 2026-09-22: shared results, comparison and trace inspection | [ADR-0227](decisions/ADR-0227-shared-experiment-results-and-trace-inspection.md) |
| 2026-09-25: graph-query evidence and observability platform | [`f3ad215`](https://github.com/tteon/gare2e/commit/f3ad215b1c9ac8d1df50ac9fb7ba94d150720cca), [ADR-0228](decisions/ADR-0228-graph-query-evidence-and-observability.md) |
| 2026-10-20: native-agent data-source / Poisson adapters merged | [`07d7453`](https://github.com/tteon/gare2e/commit/07d7453c1be7da2b814d4590cd5d7a64e7b7d24b) |

This is a source provenance boundary, not proof of exclusive authorship or a
claim that every inherited file changed after that date is new GARE2E work.

## Inherited support

| Component | Attribution and current role |
|---|---|
| General ontology, indexing, query and client SDK | SEOCHO support code, primarily under `src/seocho/`; standalone product work belongs to [tteon/seocho](https://github.com/tteon/seocho) |
| FinBench-style graph generator and original financial cases | Existing `scripts/finbench/` and `examples/finbench/` assets; benchmark inputs/support, not introduced by GARE2E |
| General application/runtime and extraction shims | Existing shared runtime behavior; the experiment viewer has its own server entrypoint |
| `seocho` package name, CLI and older release history | Compatibility and upstream history; the inherited version is not a new GARE2E release |
| Original SDK website, tutorials and architecture references | Retained reference material; SEOCHO-site deployment and sync run only upstream |

Keep compatible imports and artifact schema names when editing experiment code.
Removing the inherited runtime or renaming the distribution would require a
separate dependency and migration review. A documentation identity change does
not perform that separation.

## Claim boundaries

- Link claims to tracked source and published receipts. Implementation, offline
  contract validation and actual service measurements are different states.
- Preserve workload/model/DB revisions, resource limits, time windows, warmup,
  observer configuration, repetition count and failed outcomes.
- Distinguish generated-token demand from generation speed, and protocol
  termination from independently judged answer correctness.
- DB hits, page-cache events, physical I/O, CPU waits and GPU kernel occupancy
  are separate metrics. Keep unavailable coverage unknown.
- Exact-result reuse is an implemented treatment with a published pilot. Broader
  query/ontology optimization, batching, prefetch and storage-engine changes need
  their own implementation and evidence.
- Code and results in separate local research checkouts, including later
  `experiments/vast_finbench_*` work, are not published GARE2E contributions merely
  because they use the same graph stack. Transfer and review them explicitly.

## Public entrypoints

[README](../README.md) and [Quickstart](../QUICKSTART.md) lead with GARE2E's
experiment and result-inspection workflow. [Documentation](README.md),
[repository layout](REPOSITORY_LAYOUT.md), [workflow](WORKFLOW.md) and the root
agent guides use the same boundary. Shared package metadata points to this
repository while crediting the upstream SDK. No runtime package rename or new
performance claim accompanies this scope clarification.
