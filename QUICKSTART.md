# GARE2E Quickstart

Start with existing experiment evidence. The steps below do not call an LLM,
query a graph database, or provision a cloud instance.

## 1. Read the published results

```bash
git clone https://github.com/tteon/gare2e.git
cd gare2e
```

Open `docs/agentic-os/reports/characterization-charts/index.html` in your browser,
or read the [result index](docs/agentic-os/README.md) directly on GitHub. GitHub
renders the linked SVG figures but does not execute the HTML chartbook as an app.

Each condition has Agent, Runtime and Tool views. Check its workload, units,
measurement window, source hashes and missing coverage before comparing values.
A completed task is not automatically a correct answer.

## 2. Start the local comparison platform

Use Python 3.10+ and a virtual environment. The editable package is still named
`seocho` because the experiment modules use that inherited import namespace.
This installs the checked-out GARE2E source and viewer dependencies.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e . fastapi uvicorn
PYTHONPATH=src .venv/bin/python examples/experiment-platform/import_published.py \
  --registry .seocho/experiment-platform/registry --workspace-id gare2e
PYTHONPATH=src .venv/bin/python scripts/benchmarks/experiment_platform.py \
  --registry .seocho/experiment-platform/registry --workspace-id gare2e \
  serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`. Select a category and metric, choose a condition,
and inspect its tasks and timeline. Stop the local server with Ctrl-C.

The importer labels historical admission and compatibility contracts as
unverified. It displays measured values while withholding improvement
percentages. Importing a run does not certify its comparisons or evaluate its
answers. The registry is local, immutable by run ID, and excluded from Git.

For your own reviewed results, comparison metadata, quality/query evidence,
team access and profiler attachments, use the
[platform guide](docs/agentic-os/EXPERIMENT_PLATFORM.md).

## 3. Reproduce the chartbook

The tracked numeric exports are enough; the original model and graph services
are unnecessary for this step. Choose a fresh output directory.

```bash
.venv/bin/python -m pip install matplotlib numpy
.venv/bin/python scripts/benchmarks/render_agentic_os_characterization_figures.py \
  --source docs/agentic-os/reports/lab024-characterization \
  --source docs/agentic-os/reports/lab024-continuation \
  --source docs/agentic-os/reports/lab025-replicas \
  --output /tmp/gare2e-charts-reproduced
```

The output includes SVG/HTML, numerical chart data and source-file hashes.
[HARNESS.md](docs/agentic-os/HARNESS.md) describes the analysis entrypoints and
required private inputs for a separately authorized live rerun.

## Next steps

| Goal | Guide |
|---|---|
| Understand the repository's contribution boundary | [GARE2E scope](docs/GARE2E_SCOPE.md) |
| Read query, plan and quality evidence | [Graph optimization observability](docs/agentic-os/GRAPH_OPTIMIZATION_OBSERVABILITY.md) |
| Check measurement definitions | [Observability factors](docs/agentic-os/OBSERVABILITY_FACTORS.md) |
| Resume or register an experiment | [Experiment agent guide](docs/agentic-os/AGENTS.md), [handoff](docs/agentic-os/HANDOFF.md) |
| Contribute source changes | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Use the standalone ontology/graph-memory SDK | [Upstream SEOCHO quickstart](https://github.com/tteon/seocho/blob/main/QUICKSTART.md) |

The inherited full application stack is documented in
[docs/RUNTIME_DEPLOYMENT.md](docs/RUNTIME_DEPLOYMENT.md). It is a different user
path from viewing GARE2E's experiment results.
