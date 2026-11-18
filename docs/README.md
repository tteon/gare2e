# GARE2E Documentation

GARE2E studies and inspects the end-to-end path from an agent's LLM requests to
its graph tools. Begin with published evidence, then the measurement contracts
and the harness that produced it.

## Start here

| Goal | Read |
|---|---|
| Understand GARE2E and its inherited boundary | [Project overview](../README.md), [scope and provenance](GARE2E_SCOPE.md) |
| View results without model/DB credentials | [Quickstart](../QUICKSTART.md), [published result index](agentic-os/README.md) |
| Compare conditions and inspect a task | [Experiment platform](agentic-os/EXPERIMENT_PLATFORM.md) |
| Read Agent / Runtime / Tool metric definitions | [Observability factors](agentic-os/OBSERVABILITY_FACTORS.md) |
| Inspect query attempts, plans and quality records | [Graph optimization evidence](agentic-os/GRAPH_OPTIMIZATION_OBSERVABILITY.md) |
| Inspect thread, storage and hardware coverage | [Graph-system observability](agentic-os/GRAPH_SYSTEM_OBSERVABILITY.md) |
| Reproduce analysis or prepare a controlled run | [Harness](agentic-os/HARNESS.md) |
| Resume with another operator or coding agent | [Experiment AGENTS](agentic-os/AGENTS.md), [handoff](agentic-os/HANDOFF.md) |

## Published evidence

- [Characterization chartbook](agentic-os/reports/characterization-charts/README.md): serving demand, tool activity and agent behavior.
- [Replica / affinity results](agentic-os/reports/lab025-replicas/README.md): recorded conditions, gates and source coverage.
- [Runtime recovery](agentic-os/reports/lab026-worker-recovery/README.md): valid windows and retained startup failures.
- [Query-evidence pilot](agentic-os/reports/lab027-graph-evidence/README.md): fixed-query reuse and live-agent observations.
- [Report catalogue](agentic-os/reports/README.md): other published reports and their scopes.

A graph, chart or successful protocol termination does not certify answer
correctness. Keep original conditions, missing values, failed attempts and
measurement limits with any result you reuse.

## Contribute and operate

| Topic | Reference |
|---|---|
| Source ownership and inherited code | [Repository layout](REPOSITORY_LAYOUT.md) |
| Change and validation workflow | [Workflow](WORKFLOW.md), [contributing](../CONTRIBUTING.md) |
| Public review contract | [Issue/task system](ISSUE_TASK_SYSTEM.md) |
| Architecture decisions | [Decision log](decisions/DECISION_LOG.md) |
| Result import, comparison and download API | [Platform guide](agentic-os/EXPERIMENT_PLATFORM.md) |
| Artifact privacy and trace sharing | [Trace sharing](agentic-os/TRACE_SHARING.md) |

## Inherited SEOCHO reference

The following documents describe SDK or application behavior inherited from
SEOCHO. Their presence does not make them GARE2E-specific contributions.

| Topic | Reference |
|---|---|
| Standalone SDK onboarding | [Upstream SEOCHO](https://github.com/tteon/seocho) |
| Python interfaces used by existing code | [SDK interface](PYTHON_INTERFACE_QUICKSTART.md) |
| Ontology/indexing/query architecture | [Architecture](ARCHITECTURE.md), [module ownership](MODULE_OWNERSHIP_MAP.md) |
| SDK orchestration seams | [Internal class design](INTERNAL_CLASS_DESIGN.md) |
| Full inherited application deployment | [docs/RUNTIME_DEPLOYMENT.md](RUNTIME_DEPLOYMENT.md) |
| Historical design and operations | [Archive](archive/README.md), [maintainer docs](maintainers/README.md) |

`website/` is the inherited SEOCHO documentation-site source. Its
`website/scripts/generate-docs.mjs` generates mirrors from source documents;
those mirrors should not be edited directly. GARE2E's current entrypoints are
this GitHub documentation and its local experiment viewer. SEOCHO-site deployment
and mirror dispatch are restricted to the upstream repository.
