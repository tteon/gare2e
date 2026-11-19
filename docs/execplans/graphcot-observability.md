# GraphCoT experiment observability

## Purpose


Connect experiment calls to existing SEOCHO OpenTelemetry. A span is one timed
operation; a trace connects operations for a case. Keep exact bodies in private
S3, linked by native trace IDs. Provide a repeatable Collector launch/shutdown
gate without changing GPU ownership or generation settings.

## Progress


- [x] Audit existing timers, tracing, observability stack and appliance.
- [x] Correct native IDs, final metadata and redacted experiment errors.
- [x] Connect direct/SDK calls, HTTP retries, tools and private storage.
- [x] Add GRBench capture with unverified training provenance rejected.
- [x] Add persistent Collector queue, vLLM scrape, wrapper and dashboard.
- [x] Verify actual OTel SDK and actual Collector endpoint recovery.
- [ ] Verify full Docker stack and actual GPU/DB case before deployment claims.

## Decisions and discoveries


Reuse tracing.py, experiment_observability.py and private_trace_store.py. Do not
add a tracing framework or another uploader. ADR-0172 supersedes old Opik
references. Explicit experiment mode requires safe project/campaign/experiment/run
IDs. The wrapper sends to a local SSH tunnel and never rents/deletes instances.
Existing timers keep their original measurement boundaries.

The first live test exposed a premature drain decision: queue size can be zero
while export is in flight or before the first metric scrape. Readiness now waits
for received metrics; drain compares accepted and sent counts as well as queues.

## Steps and acceptance


Install the package's existing otel extra and test dependencies, then run:

    python -m pytest -q tests/seocho/test_graphcot_observability.py tests/seocho/test_private_trace_store.py tests/seocho/test_agentic_graph_agents.py
    SEOCHO_TEST_OTELCOL=/path/to/otelcol-contrib python -m pytest -q tests/seocho/test_graphcot_collector_live.py
    bash scripts/ci/run_basic_ci.sh

The binary must report version 0.119.0. Contracts verify native IDs, concurrent
context isolation, actual HTTP retry bodies and storage separation. The live
test uses the actual Collector with test OTLP/metrics endpoints; it does not
establish vLLM, Tempo or GPU performance. Follow docs/GRAPHCOT_OBSERVABILITY.md
for deployment and one-case Tempo/private-storage checks. Measure off/on
instrumentation overhead before performance comparisons.

## Recovery and risks


Never overwrite trial directories. Restart the same Collector with the original
otel-queue directory after export failure. Resume the existing private uploader
after its writer stops. Instance deletion loses undelivered disk data; lifecycle
remains with the master registry's resource owner. Existing frozen trials remain
unchanged. Missing server prefill/decode/verification timing is unavailable.

Docker Desktop service startup hangs on this host even after restart. The
four-service live gate remains open. The initial basic CI address gate flagged real-host-shaped SSH fixtures. They
now use the reserved .example domain without changing parser assertions.

## Revision notes


2026-09-13: User authorized implementation here, AGENTS adoption after tests and
update/drop of existing PR. Retain the PR because full private capture remains
necessary and extend it with native OTel linkage and experiment operation.
