# Contributing to GARE2E

GARE2E contributions improve graph-agent experiments, measurement coverage,
query evidence, reproducible analysis, or the shared result viewer. Start with
[the project scope](docs/GARE2E_SCOPE.md) to distinguish these surfaces from
inherited SEOCHO SDK and application code.

## Start here

1. Read [README](README.md) and [Quickstart](QUICKSTART.md).
2. Read [the repository layout](docs/REPOSITORY_LAYOUT.md) before choosing a file.
3. Read [the experiment guide](docs/agentic-os/AGENTS.md) and
   [harness](docs/agentic-os/HARNESS.md) for workload or measurement changes.
4. Follow [the workflow](docs/WORKFLOW.md), public review contract in
   [ISSUE_TASK_SYSTEM.md](docs/ISSUE_TASK_SYSTEM.md), and relevant ADRs.
5. Coding agents also follow [AGENTS.md](AGENTS.md) and [CLAUDE.md](CLAUDE.md).

## Local setup and checks

```bash
git clone https://github.com/tteon/gare2e.git
cd gare2e
uv sync --extra ci
PYTHONPATH=src:. uv run pytest tests/seocho/test_experiment_platform.py tests/seocho/test_query_evidence.py -q
```

Use the narrow checks for your change first. Docs changes use
`bash scripts/ci/check-doc-contracts.sh`; root/agent documentation also uses
`scripts/pm/lint-agent-docs.sh` and
`bash scripts/ci/check-root-hierarchy-contract.sh`. Changes to shared runtime,
SDK, CI or packaging use `bash scripts/ci/run_basic_ci.sh` as well as their
focused tests. Report exact commands and remaining gaps.

## Where to work

| Goal | Start here |
|---|---|
| Workload preparation, schedules, lifecycle or analysis | `scripts/benchmarks/` |
| Measurement, query receipts and immutable registry | `src/seocho/eval/` |
| Viewer HTTP API and UI | `runtime/experiment_platform.py`, `runtime/experiment_platform_web/` |
| Co-located serving and observation | `docker/agentic-os-appliance/` |
| Reviewed import examples and metadata | `examples/experiment-platform/` |
| Experiment methods, evidence and handoff | `docs/agentic-os/` |
| Inherited SDK behavior required by an experiment | Read `docs/MODULE_OWNERSHIP_MAP.md`; preserve existing `src/seocho/` ownership |

Changes to unrelated ontology, ingestion, memory or general SDK capabilities
belong in the upstream SEOCHO project. When an experiment needs a shared fix,
state the dependency, preserve compatibility, and identify any upstream work.

## Evidence and review

Keep one named question or behavior change per PR. Include the source paths,
changed contract, intended effect, exact validation and known limitations.
Performance claims require actual service receipts with dataset, versions,
resources, concurrency, warmup, time window and repetition count. Mock tests
validate contracts and failure handling; they do not establish speed or quality.

Preserve failed attempts and published results. Write new analysis to a fresh
output directory, keep unobserved counters unknown, and distinguish query parity
from live-agent answer quality. A live run needs explicit resource authorization
and the harness admission/collection/teardown gates. Publishing documentation or
running offline tests provides no new spending authority.

Raw prompts, answers, query payloads, provider responses, connection addresses,
credentials and unreviewed profiler files remain private. Share reviewed
allowlisted exports and checksums. Do not commit local `.agents/`, `.beads/`,
`.seocho/`, logs or generated data; `.claude/skills/` is the existing shared-skill
exception. Preserve upstream license attribution and historical records.

## Automation

[GitHub automation](docs/GITHUB_AUTOMATION.md) provides CI and bounded draft-only
maintenance/review. It should follow GARE2E's experiment scope. SEOCHO-site
publication is restricted to the upstream repository. Comment merge remains
maintainer-triggered, exact `/go`, clean non-draft PR and squash merge.
