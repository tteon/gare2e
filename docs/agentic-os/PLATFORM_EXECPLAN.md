# Shared experiment comparison and trace inspection

This living plan follows `docs/maintainers/EXECPLAN_SPEC.md`. Bead: seocho-rn3p.

## Purpose / Big Picture


Contributors register immutable, attributed experiment results. Readers start at
comparison charts, drill into a condition and task timeline, and inspect evidence
behind bounded insights. Perfetto-compatible downloads expose actual measured
events. Missing GPU kernel instrumentation remains missing.

## Progress


- [x] Existing characterization, CSV and neutral trace contracts inspected.
- [x] Deployment preference requested; local executable implementation proceeds.
- [x] Immutable registry, import adapter, metric-extension contract and API.
- [x] Comparison charts, individual results, timeline and evidence insights.
- [x] Real artifact import, local HTTP/browser validation, tests, documentation.
- [x] Checksummed Nsight SQLite/report attachment and authenticated download.

## Surprises & Discoveries


Characterization JSON includes representative examples while CSV.gz contains all
exported tasks. A platform must disclose coverage and allow all-task inspection
when CSV is supplied. Historical conditions can lack exact server joins; request
duration alone cannot locate prefill/decode boundaries.

## Decision Log


2026-09-22: use an immutable filesystem registry and a read-only FastAPI viewer.
Contributors import via CLI or reviewed automation; browser writes and arbitrary
plugin execution are excluded from this first implementation. This supports
shared results without creating a new experiment scheduler or credential store.
An external authentication proxy is the team deployment boundary; default server
binds to loopback. Preference/hosting details remain pending.

## Outcomes & Retrospective


Implemented with 30 historical conditions and 10,821 imported tasks. Local browser
checks exercise chart selection, Tool metrics, task zoom, event details and insight
evidence without JavaScript exceptions. Focused platform/graph tests: 12 passed.
Full basic CI after the download implementation: 1,112 passed, 3 skipped, plus
repository contracts. ADR index validation also passed. No new inference or
cloud allocation occurred.
Real Nsight captures, cross-clock profiler alignment and authenticated team
deployment remain follow-ups; fixture attachment tests are not profiler evidence.

## Context and Orientation


`scripts/benchmarks/summarize_agentic_os_characterization.py` produces condition
summaries and tasks/requests/tools CSV. A condition is one experimental setting;
a task is one question execution. `src/seocho/eval/experiment_platform.py` will
normalize these into versioned result records. Runtime-facing read-only routes
belong under `runtime/`; the standalone launcher belongs under scripts.

## SEOCHO Evidence Contract


Every run carries owner, workspace, source digest, source commit (unknown allowed),
comparison contract, metric definitions, condition factors and observation gaps.
Normalized task records exclude raw prompts, answers, Cypher and profiler
arguments. Optional raw profiler attachments require explicit contributor content
review; they are copied byte-for-byte, not redacted. Numeric trace events preserve
timing provenance. Declared compatibility is not verified parity;
missing or conflicting comparison fields suppress ranked speedup claims.

## SEOCHO Review Panel


Professor lens: a task completion is not answer accuracy; independent repeats and
quality checks remain separate. Software lens: additive immutable imports,
bounded IDs and read-only routes avoid artifact traversal and partial publication.
Systems lens: overlap is displayed on lanes, not summed as exclusive GPU time;
profiler diagnostics remain distinct from throughput windows. These are written
review lenses, not delegated agents.

## Cost, Latency, and Provider Policy


No paid providers, LLM synthesis, cloud launch or DB workload. Insights are
deterministic observations with evidence and limitations. Raw Nsight binaries
are registered as reviewed downloads, not parsed by guessing undocumented formats.
SQLite recognition reads only schema. Merging profiler events into the E2E timeline
requires a documented clock-aligned adapter later.

## Plan of Work


Implement registry/import/trace conversion and metric definitions first, then a
read-only API and self-contained HTML/JS frontend. Import existing published
results to exercise charts and full task drilldown. Add a contributor guide,
comparison/evidence rules and a minimal deployment recipe.

## Concrete Steps


    PYTHONPATH=src python scripts/benchmarks/experiment_platform.py --help
    PYTHONPATH=src python -m pytest tests/seocho/test_experiment_platform.py -q
    bash scripts/ci/run_basic_ci.sh

The CLI will provide import, list and serve subcommands; documented examples will
use repository-owned result directories and a fresh ignored registry directory.

## Validation and Acceptance


Two attributed imports must appear in a local browser with comparable metrics,
condition/task selection and a downloadable Chrome JSON trace. Tests cover
duplicate imports, mismatched comparison contracts, unknown metrics, paths and
workspace boundaries, trace overlap, privacy and exact timestamp handling.
Real historical artifact import proves adapter compatibility, not new performance.

## Idempotence and Recovery


Imports publish a fully written temporary directory under an exclusive registry
lock. Repeating identical imports succeeds; a changed payload under an existing
ID is rejected. Existing raw artifacts and historical reports are never changed.
No arbitrary filesystem paths are accepted through the HTTP API.

## Artifacts and Notes


The local registry is ignored/private. Source hashes and validation receipts can
be shared; credentials and raw workload payloads cannot. Deployment is not claimed
until a reachable authenticated team endpoint has actually been validated.

## Interfaces and Dependencies


Python standard library for normalization/registry/Chrome trace export, existing
FastAPI and uvicorn for the viewer, browser-native SVG/JS for charts/timeline.
Metric extensions are JSON metadata with numeric source paths, units, direction
and category; no executable code is loaded from imported results.
