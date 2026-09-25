# Graph optimization evidence in the existing experiment platform

This living ExecPlan follows `docs/maintainers/EXECPLAN_SPEC.md`. Bead: seocho-ix30.

## Purpose / Big Picture


An operator can follow a question from its measured timeline to each database attempt, execution plan, missing counters, optimization decision and answer-quality evidence. Extend the existing gare2e registry and viewer; do not create another benchmark framework. A query attempt includes validation failures and cache hits; a database execution counts only an actual submission.

## Progress


- [x] Reviewed gare2e main 8480676c, lab021, graph observer and platform contracts.
- [x] Reproduced plan counters becoming unknown then incorrectly summed as zero.
- [x] Recorded a fresh USD100 total ceiling; no prior experiment budget is reused.
- [x] Correct plan parsing, counter coverage, topology and cardinality diagnostics.
- [x] Add opt-in executor evidence and exact-result reuse with snapshot/session boundaries.
- [x] Extend registry and UI with query logs, plans, quality and evidence-linked insights.
- [x] Add observability export/contracts and document backend responsibilities.
- [x] Reanalyze preserved evidence, validate local UI/CI, then run a bounded live gate.
- [x] Record live results, limitations and resource teardown.
- [x] Validate the private gare2e change for landing.

## Surprises & Discoveries


GraphCoT-hop calls fixed parameterized templates. Text2Cypher generates queries. They need distinct baselines. GRBench physical labels/types are GRBenchNode/GRBENCH_REL with semantic types stored in properties. Existing plan totals convert missing counters to zero. Existing platform already imports 30 historical conditions; no need to rebuild it.

## Decision Log


2026-09-24: Preserve vendor-neutral OTel IDs and private JSONL as the evidence contract. Prometheus is for bounded aggregate metrics; Tempo stores traces; Grafana joins operational views; LangSmith is an optional trace/evaluation destination. No backend is required to inspect local evidence and no payload is automatically uploaded. Root AGENTS tracing rules supersede stale Opik wording in ExecPlan supplements.

2026-09-24: First intervention is bounded session-local exact-result reuse on an explicitly immutable snapshot. General Cypher rewriting without a proof contract is not enabled. Ontology candidates require a verified physical mapping and result parity. No claim that implemented-only modes have been measured.

## Outcomes & Retrospective


Local implementation, browser and actual Collector/Tempo/Prometheus/Grafana integration passed. Final focused tests:83 passed; basic CI:1112 passed /3 skipped. Browser48conditions, live LLM-server/query waterfall and logs:0JSerrors. Actual recorded pilot import:298 client/agent spans,119 server spans and167 query logs; Tempo lookup passed.

Live pilot2 completed16 diagnostic/gate/comparison cells. Fixed reuse avoided19/20 submissions with identical results; agent24comparison sessions had0cache hits. Answer text matched the RAW controls, but correctness is unscored. Request joins119/122; missing3 remain unknown. Historical lab021317plans/2309operators retained its complete counters and zero-miss observation.

Instance52552894 destruction/absence verified; watchdog stopped. Hourly estimate$0.991837 plus unverified transfer charges. Essential122files checksum verified; skipped bulk retains partial-artifact lifecycle exit2 despite workload exit0. [Result](../agentic-os/reports/lab027-graph-evidence/README.md). No additional paid run scheduled.

## Context and Orientation


`src/seocho/eval/grbench_runtime.py` owns database execution. `plan_audit.py` parses diagnostic PROFILE trees. PROFILE actually executes a query; replay runs outside measured throughput windows. `experiment_platform.py`, `runtime/experiment_platform.py` and `runtime/experiment_platform_web/` own immutable imports, API and UI. Extend these paths and the existing direct/hop runners. A trace is a tree of timed operations; a structured log is an event tied to an attempt; an insight names evidence and limits, not an inferred cause.

## SEOCHO Evidence Contract


Preserve workspace, snapshot, session, tool and query identity. Store private query/parameters separately from shareable receipts. Unknown counters remain null with coverage. Record rule preconditions, fallbacks, rows/bytes, reused-query identity and truncation. Quality evaluations must name evaluator/version and evidence hashes; absence is unscored. No hidden reasoning is collected. Ontology hints are not proof of persisted graph constraints.

## SEOCHO Review Panel


Professor lens: distinguish fixed-query parity from live-agent quality and never infer GPU benefit from DB time alone. Engineer lens: reuse executor and registry, reject orphan/duplicate evidence, preserve nullable counter contracts. Systems lens: separate diagnostic replay from timed work and retain sampler/observer overhead. Reject a candidate if result parity fails or instrumentation cannot distinguish missing values from zero.

## Cost, Latency, and Provider Policy


New live work has a user-authorized total ceiling of USD100. Use the Vast lifecycle runner, independent watchdog, readiness/one-case gates and verified teardown. Use a small first allocation; do not spend the ceiling automatically. No SaaS upload or new provider subscription. Record costs of rejected/failed runs. Existing hosted/self-hosted results remain separate.

## Plan of Work


First repair nullable PROFILE parsing and tree retention with actual-driver-shaped fixtures. Then add opt-in query evidence to the existing executor and capture failures/reuse distinctly. Extend the immutable platform import contract to accept reviewed content-free query JSONL joined by pass/task, and expose logs/plans/quality through existing task inspection. Add deterministic insights, backend mapping and export validation. Reanalyze original receipts and use narrow live gates before broadening workload/cache/runtime factors.

## Concrete Steps


From the gare2e checkout:

    PYTHONPATH=src:. uv run pytest tests/seocho/test_plan_audit.py tests/seocho/test_grbench_runtime.py tests/seocho/test_experiment_platform.py -q
    node --check runtime/experiment_platform_web/app.js
    bash scripts/ci/run_basic_ci.sh

Import preserved characterization into a new registry run ID with the existing `scripts/benchmarks/experiment_platform.py` CLI; run its `serve` subcommand on loopback and inspect a task. Add exact evidence commands and live receipts as implementation proceeds.

## Validation and Acceptance


Missing counters never render as zero. Every opted-in query attempt has a unique ID, outcome, elapsed interval and scope. Complete cache results are reusable only within the same immutable snapshot/session/parameters; failures and capped results are not cached. UI distinguishes imported historical measurements, diagnostic replay and unmeasured links. API workspace/auth/path tests pass. No performance claim is promoted from fixtures. Live continuation requires actual DozerDB/vLLM readiness and trace admission.

## Idempotence and Recovery


Use an isolated branch on private gare2e. Do not touch unrelated SEOCHO work. Registry imports remain immutable and idempotent; new evidence gets a new run ID. Query logs append with restrictive permissions. Cache state is ephemeral. On live failure collect original receipts before teardown; do not replace an unresolved instance.

## Artifacts and Notes


Closest evidence: `docs/agentic-os/lab/requests/021-agent-graphdb-interface.md`, `GRAPH_SYSTEM_OBSERVABILITY.md`, `EXPERIMENT_PLATFORM.md`. Prior results remain historical and unchanged. Store new raw evidence under ignored `.seocho/` or an operator-selected private directory.

## Interfaces and Dependencies


Retain `GRBenchDozerExecutor`, `audit_profile`, `Registry`, `import_characterization`, existing FastAPI app and vanilla browser assets. Add small eval modules for query evidence/contracts and observability serialization. No new agent orchestration framework or storage-engine modification.
