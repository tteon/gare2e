# Shared experiment platform

The platform connects **comparison chart → condition/task results → evidence
insights**. It is a runnable read-only web application with an immutable result
registry. Contributors add experiments using the import CLI and reviewed source
changes. It does not allocate GPUs or run workloads.

## Start with existing results

From the repository checkout with its normal Python environment (FastAPI,
uvicorn and the SDK installed):

```bash
PYTHONPATH=src python examples/experiment-platform/import_published.py \
  --registry .seocho/experiment-platform/registry --workspace-id gare2e
PYTHONPATH=src python scripts/benchmarks/experiment_platform.py \
  --registry .seocho/experiment-platform/registry --workspace-id gare2e \
  serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. Select owner, Runtime/Agent/Tool and a metric. Click
a condition's bar, browse its tasks (wall-time descending, searchable and paged),
then click a task. Its timeline supports zoom, horizontal scroll and event
inspection. Insight cards open the actual supporting metric values.

The historical importer deliberately leaves comparison contracts and admission
unverified. It preserves measured values while suppressing improvement percentages
until a contributor supplies the reviewed compatibility and gate evidence. Raw
question text, answers and query payloads are not needed.

## Register your experiment

Generate `characterization.json` using the existing harness; include
`tasks.csv[.gz]`, `requests.csv[.gz]`, `tools.csv[.gz]` for full task drilldown.
Without all three CSVs the platform explicitly exposes representative examples
only. The manifest displays imported versus reported task counts.

Copy `examples/experiment-platform/metadata.example.json` to your local work
directory. Fill the exact source commit, repetition count and fixed comparison
contract: dataset/model revisions, hardware identity/budget, physical GPU count,
DB build, request protocol and measurement policy (window/warmup/timeout/
observer configuration). Record each condition's admission as `passed`, `failed`
or `unverified` in the `admission` map keyed by its original pass name. These are
contributor assertions and remain labeled as such; a pass must have real gate
evidence before being declared passed.

If one run contains different protocols, CPU budgets or measurement settings,
override the affected fixed fields in `condition_contracts`, keyed by pass name.
Those overrides are displayed and used by comparison. A single run ID alone
never establishes comparable conditions.

```bash
PYTHONPATH=src python scripts/benchmarks/experiment_platform.py \
  --registry .seocho/experiment-platform/registry --workspace-id gare2e \
  import --source "$REPORT_DIR" --run-id my-feature-r1 --owner my-name \
  --metadata "$METADATA_JSON" \
  --metrics examples/experiment-platform/metrics.example.json
```

An import records source hashes, owner, workspace, source commit, definitions and
coverage. Publication uses a registry lock and atomic directory rename. Identical
imports are idempotent; changed evidence under an existing ID is rejected. Use a
new run ID for new evidence. No browser write/delete endpoint exists. Team writers
use a shared registry volume or a serialized import job; readers use the API.

## Add a feature or metric

Implement the treatment in its owning SDK/runtime/harness module, preserving the
baseline. Add a numeric metric definition with a unique ID, label, unit, view,
direction (`higher`, `lower`, `descriptive`) and dotted path into a condition's
characterization JSON. The sample extension adds LLM client p95. Missing values
stay unknown. Existing primary definitions cannot be replaced by an extension.
No Python or JavaScript is loaded from experiment artifacts.

New source formats should add a reviewed adapter that emits the same run,
condition, task and event contracts. Add a deterministic insight rule only when
its evidence fields, missing-data behavior and interpretation limits are tested.
The platform core lives in `src/seocho/eval/experiment_platform.py`, HTTP routes
in `runtime/experiment_platform.py`, browser assets in
`runtime/experiment_platform_web/`, CLI in `scripts/benchmarks/experiment_platform.py`.

## Comparability and insights

Output tok/s/GPU remains primary, accompanied by task/s and per-task input/output
tokens. Tool's largest family p95 is explicitly a maximum of group quantiles,
not the overall tool p95. Completed tasks are not judged answers.

The comparison endpoint only calculates percentages when fixed contracts match,
neither contract has unknown fields, both conditions are declared admitted, and
metric definitions agree. K and treatment factors can vary intentionally and
remain visible; matched declarations do not prove causal equivalence. Different
host/CPU/timeout/observer settings must be included in the contract rather than
hidden behind an identical hardware model name. No statistical significance is
claimed from a single run.

Insights currently identify missing admission, incomplete server joins, the most
expensive observed tool-family p95, and the demand-versus-speed distinction.
They are observations and navigation aids, not LLM-generated causal conclusions.

## Perfetto and Nsight roles

The normal E2E timeline represents actual Agent, LLM client, exactly joined LLM
server and graph tool intervals. Per-task downloads use Chrome JSON microsecond
timestamps and non-overlapping tracks, supported by
[Perfetto](https://perfetto.dev/docs/getting-started/other-formats/). Open the
downloaded file locally in a compatible viewer. Nothing is auto-uploaded to an
external trace service.

Request duration distributions do not establish exact prefill/decode boundaries;
the platform retains those durations as event evidence instead of inventing
stage slices. OTel spans alone do not expose GPU kernels or OS scheduling.

[Nsight Systems](https://docs.nvidia.com/nsight-systems/UserGuide/index.html)
diagnoses CUDA activity, transfers and synchronization in a separate focused run;
[Nsight Compute](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)
examines individual kernel efficiency. Reviewed `.nsys-rep` files and exported
SQLite databases can be registered as authenticated downloads. Automatic profiling
and native report parsing are not implemented. A reviewed adapter with explicit
clock alignment is required before combining profiler events with E2E task traces.
The platform's lane-style display must never be labeled Nsight measurement without
that evidence.

### Register Nsight downloads

Export a single-file SQLite database using the installed Nsight Systems version's
`nsys export` command; check `nsys export --help` for its supported options. Put
the export or original report inside the characterization source directory. Add
a JSON manifest such as the following, replacing version, checksum and window
with actual capture metadata:

```json
[
  {
    "id": "gpu-profile",
    "condition": "base-k8",
    "type": "nsight_sqlite",
    "path": "profiles/gpu.sqlite",
    "sha256": "REPLACE_WITH_FILE_SHA256",
    "profiler_version": "REPLACE_WITH_ACTUAL_VERSION",
    "scope": "separate_diagnostic",
    "capture_window": {
      "start_s": 0,
      "end_s": 10,
      "clock_domain": "profiler_relative"
    },
    "content_reviewed": true
  }
]
```

Pass `--diagnostics path/to/manifest.json` to the import command. Use
`nsight_report` for an original `.nsys-rep` attachment. Scope is either
`separate_diagnostic` or `throughput_capture`; the latter discloses profiler
overhead in the measurement window and must also be reflected in comparison
contracts. `content_reviewed` is the contributor's assertion that the raw file
is appropriate for sharing, not automatic redaction. Profiler files can contain
command lines, paths and annotation strings.

The importer verifies directory boundaries and file checksums, reads only SQLite
schema to recognize diagnostic tables, and copies the original bytes. It never
executes supplied SQL or creates a synthetic Nsight database from OTel spans.
Unrecognized SQLite schemas need a reviewed version-specific adapter. Original
report binaries are attachments, not parsed or certified reports.

Each condition displays registered downloads with size, checksum, version and
capture scope/window. Missing files produce no download button. Supporting
browsers can stream downloads to a selected file; the fallback uses browser
memory, so large reports are better downloaded through an authenticated client.

## Team deployment

Loopback is the default. For a shared server, set `GARE2E_PLATFORM_TOKEN` through
the team's secret mechanism and bind to the intended interface. Non-loopback
binding requires the token. Terminate HTTPS at the team's proxy; the app does not
implement TLS. Enter the token in the page; it stays in page memory and travels
in an Authorization header, never a URL. API responses are not cached. The
workspace is fixed by the server and other workspace runs are excluded.

This shared read token is a first-release access boundary, not per-user SSO/RBAC.
Owner names are import provenance, not authenticated user identities. A team
SSO proxy and an authenticated import job can provide those deployment controls.
No external endpoint is deployed merely by running local validation. Keep the
registry private and mount it read-only in the viewer deployment.

## Validation

```bash
PYTHONPATH=src:. python -m pytest tests/seocho/test_experiment_platform.py -q
node --check runtime/experiment_platform_web/app.js
bash scripts/ci/run_basic_ci.sh
```

Tests cover immutable imports, privacy allowlists, unit conversion, overlapping
interval tracks, metric extensions, incompatible comparisons, authentication,
workspace isolation and absent artifacts. Historical imports and live local
HTTP/browser checks validate the platform, not a new DB/GPU experiment.

## Query, plan and quality evidence

The existing viewer now accepts query-attempt JSONL and versioned quality
records. Task inspection adds searchable structured logs, paired plan tables,
nullable counter coverage and evidence-linked insight cards. See
[Graph optimization observability](GRAPH_OPTIMIZATION_OBSERVABILITY.md) for the
capture/import contract and OTel, Prometheus, Grafana, Tempo and LangSmith roles.
