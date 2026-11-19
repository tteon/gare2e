# Experiment harness: read, reproduce, resume

For shared comparison charts, task inspection, evidence insights and Perfetto /
Nsight artifact downloads, see [the experiment platform](EXPERIMENT_PLATFORM.md).
Its importer reuses characterization exports; it does not launch experiments.

## Inspect a private SF1 handoff

Use [the SF1 handoff guide](SF1_HANDOFF.md) for the offline ZIP validator, query
hash meanings, and historical versus receiving model conditions. Static bundle
validation does not launch a model, load a graph, or establish live readiness.

## View existing evidence

Clone `tteon/gare2e` with your repository access and open
`docs/agentic-os/reports/characterization-charts/index.html` locally. The
[entry document](README.md) also embeds SVGs that render directly on GitHub.
The chartbook contains30 conditions and3 categories per condition, numerical
data in `chart-data.json`, and hashes of all12 source export files.

## Reproduce charts without inference

Run from the repository root with Python3.10+ and Matplotlib/NumPy installed in
your chosen virtual environment. Existing CSV.gz exports are sufficient; this
command does not call an LLM, DB, AWS, or Vast. Use a fresh output directory.

```bash
python3 scripts/benchmarks/render_agentic_os_characterization_figures.py \
  --source docs/agentic-os/reports/lab024-characterization \
  --source docs/agentic-os/reports/lab024-continuation \
  --source docs/agentic-os/reports/lab025-replicas \
  --output /tmp/gare2e-charts-reproduced
```

The numerical aggregation tests require pytest but do not import Matplotlib:

```bash
python3 -m pytest tests/seocho/test_agentic_os_characterization_figures.py -q
```

For the full repository test environment, use the existing `pyproject.toml`/lock
and CI setup. Router contract tests additionally require aiohttp and permission
to open local loopback sockets; their mocked backends are not performance evidence.

## Harness components

For the opt-in DB/thread/CPU/NUMA/I/O collector, capability probe, coverage
statuses and remaining engine/PMU instrumentation gaps, see
[Graph-system observability](GRAPH_SYSTEM_OBSERVABILITY.md). Enable with
`SEOCHO_GRAPH_SYSTEM_OBSERVER=1` for a newly validated source bundle. Historical
bundles and results remain unchanged.

Paths below are relative to `scripts/benchmarks/` unless stated otherwise.

| Component | Entry point | Responsibility |
| --- | --- | --- |
| Throughput schedule and persistent runner | `prepare_agentic_os_throughput.py`, `run_agentic_os_throughput_session.py` | Warmup, dynamic task supply, bounded dispatch/drain, per-setting gates |
| Appliance bootstrap | `bootstrap_agentic_os_appliance.sh`, `docker/agentic-os-appliance/` at repo root | Pinned serving/DB setup, readiness, OTLP file receiver, replica router |
| Paid lifecycle | `vast_colocated_agentic_os_run.py` | Admission, staging checksums, independent watchdog, collection, teardown verification |
| Registered launcher | `lab/launch_lab_s22.py` | Original matrix, `--continuation`, or `--replica-audit` mode |
| Exact trace admission | `audit_agentic_os_trace_gate.py` | Unique W3C parent joins, queue/TTFT/prefill/decode, router coverage |
| Backend sampling | `sample_agentic_os_replica_metrics.py` | Individual running/waiting/KV samples and window coverage |
| Stage and throughput summaries | `summarize_agentic_os_otel_stages.py`, `summarize_agentic_os_factors.py` | Request stages, common-window counters, metric provenance |
| Linked Agent/Runtime/Tool exports | `summarize_agentic_os_characterization.py` | tasks/requests/tools CSV, summary JSON, per-task timeline |
| Chart renderer | `render_agentic_os_characterization_figures.py` | Three-category SVG/HTML and input hashes |
| Shared resume guide | `.claude/skills/lab-resume/SKILL.md` at repo root | Portable recovery order for an agent |

With the repository's benchmark dependencies installed, inspect the lab025 plan
without starting any service or provider request:

```bash
PYTHONPATH=src python3 scripts/benchmarks/run_agentic_os_throughput_session.py \
  --replica-audit --print-plan
```

The six entries are TP2 gate/control, sticky replica gate/control, task-rr replica
gate/control. The launcher also offers `--help`/`--dry-run`; its dry-run still
validates local input files and the preflight receipt. Legacy `launch_lab_sNN.sh`
files preserve historical run parameters, not portable defaults for a new run.

## Live rerun inputs and limits

Live reruns need provisioned GPU/DB services and private inputs that are
intentionally absent from Git. See [appliance contract](../../docker/agentic-os-appliance/README.md)
and [request025](lab/requests/025-replica-trace-and-affinity.md).

| Required input | Preparation / validation |
| --- | --- |
| Source archive and standalone bootstrap | Build from the chosen reviewed commit; verify bundle/remote hashes and exact-image preflight |
| Dozer runtime bundle | Match the version/hash recorded in the report or register a changed version |
| GRBench graph import | `export_grbench_import_csv.py`; nodes/rels CSV.gz and import manifest |
| Questions and schedule/schema/discovery inputs | Obtain the authorized dataset/artifacts; keep paths configurable with `--workspace` and compare hashes/cohort |
| Provider/model/DB access | Operator-managed credentials; never copy another operator's secrets into the repository |
| Fresh run root, budget, ownership | `--run-root`, rate/time bounds, owned-instance receipt; preserve peers and prior spend |

For raw artifacts you already have, derive a new report directory:

```bash
PYTHONPATH=src python3 scripts/benchmarks/summarize_agentic_os_characterization.py \
  --run-root "$RUN_ROOT" --output-dir "$NEW_REPORT_DIR"
```

The raw run includes `run-*/pass-receipt.json`, `otel/spans.jsonl`,
`otel/trace-admission.json`, metric samples and root `otel/collector-spans.jsonl`.
In lab025 a setting's router receipt is under its **gate** directory's
`vllm-restart/`, shared by that gate and its timed comparison. Do not assume one
router process per pass. Essential transfer verification and lifecycle receipts
remain private; publish their reviewed counts/hashes, not entire provider payloads.

Missing private inputs limit live reproduction, not access to the published
measurements. Follow [AGENTS.md](AGENTS.md) and the latest [HANDOFF](HANDOFF.md)
before modifying or launching another experiment.


## Sequential engine and pressure validation (lab026)

`run_agentic_os_throughput_session.py --runtime-audit --print-plan` shows the
observer OFF/ON, separate kernel profile, grammar probe and two baseline/MTP
K sweeps. After those gates pass, `run_agentic_os_pressure_session.py` runs
actual graph-corpus8K/24K prefix diagnostics. The launcher uses a new owned
run root and explicit12600s/$12.60 operator bound under request026 authorization.

The opt-in pinned-image `observed_scheduler.py` preserves AsyncScheduler and
records content-free JSONL under each server restart receipt. Engine summaries
use the existing common throughput windows. `summarize_agentic_os_kernel_profile.py`
reads private torch trace originals and publishes only interval-union statistics
and digests. Never publish the raw profiler files; they contain process metadata.

After teardown, `summarize_agentic_os_runtime_pressure.py --run-root ... --output ...`
exports numerical conditions and standalone saturation/context figures. Keep
fixed-corpus diagnostics separate from the Agent/Tool/Runtime agent chartbook: use
`--exclude-fixed-corpus` with `summarize_agentic_os_characterization.py`. The
export records excluded pass names; their runtime receipts remain in the
separate pressure report.
Use the same preserved originals to regenerate results; no paid calls are needed
for postprocessing. A partial run produces only the conditions actually observed.

### Recovering high-concurrency client admission

The lab026 continuation uses `--runtime-recovery` to skip the already measured
instrumentation stage. `--saturation-repetitions 1` selects one paired baseline/MTP
sweep; the default remains two sweeps. Inspect the plan without paid work:

```bash
PYTHONPATH=src python3 scripts/benchmarks/run_agentic_os_throughput_session.py \
  --runtime-recovery --saturation-repetitions 1 --print-plan
```

Recovery workers set native-library thread counts to one in their child process
environment. Persistent process creation is serialized to bound transient pipe
file descriptors; task execution remains concurrent. A coordinator startup error
records its numeric errno and file-descriptor limits. Cgroup PID limits/events
and role thread counts distinguish client resource limits from engine admission.
A failed startup without a valid common window is not a throughput measurement.

The essential private transfer includes explicitly named worker stderr files.
They can contain model output and must never be promoted to the shared reports.
Only reviewed numeric receipts, counts, hashes and aggregate charts are published.
See request026 for per-run cost bounds; `--runtime-completion` is the registered
7500-second continuation after the first two attempts, not a reusable allocation
of their already spent budget.
