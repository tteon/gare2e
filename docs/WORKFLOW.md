# GARE2E Workflow

This is the workflow for graph-agent experiments, measurement/evidence changes
and the read-only comparison platform. Start with [project scope](GARE2E_SCOPE.md),
[Quickstart](../QUICKSTART.md) and [the harness](agentic-os/HARNESS.md).

## Workflow map

| Goal | Contract |
|---|---|
| Read existing results | [Published index](agentic-os/README.md) and original report limits |
| Reproduce analysis | [Harness](agentic-os/HARNESS.md); new output directory and preserved inputs |
| Add or compare results | [Platform](agentic-os/EXPERIMENT_PLATFORM.md); immutable import and compatible contracts |
| Change query/plan/quality capture | [Graph evidence](agentic-os/GRAPH_OPTIMIZATION_OBSERVABILITY.md) |
| Run a new live experiment | [Experiment agent guide](agentic-os/AGENTS.md); explicit resource scope and lifecycle gates |
| Change source and land it | [Contributing](../CONTRIBUTING.md) and validation below |

## 1. Define the comparison

Name the unresolved question and closest existing condition. Reuse published
receipts before scheduling another run. Distinguish a GARE2E experiment change
from a shared SDK dependency fix; unrelated SDK product features belong upstream.

Freeze the source/data/model/DB revisions, prompt/tool contracts, resource and
concurrency limits, warmup, measurement window, observer configuration, result
contract and repetition count. The current maintainer-designated tracker owns
work state; GitHub review carries public change context. Keep private tracker
state out of commits. See [ISSUE_TASK_SYSTEM.md](ISSUE_TASK_SYSTEM.md).

## 2. Preserve and validate evidence

Keep successful, failed, stopped and ambiguous assignments in their original
denominators. Unknown counters remain unknown. Join server requests by recorded
identity; time proximity does not establish a trace relationship. Keep shared
and exclusive resource time, output demand and output speed distinct.

Use reviewed numeric exports, manifests and hashes for public analysis. Preserve
raw query/parameter/result payloads, model/provider content, connection addresses,
credentials and unreviewed profiler files privately. Diagnostic PROFILE or kernel
captures require separate windows and explicit overhead/clock scope.

Mocks and offline tests validate contracts. Performance or answer-quality claims
need their actual workload, services and evaluation receipts. A source change,
chart or normal protocol stop alone is not a result.

## 3. Choose execution or postprocessing

Offline chart reproduction and immutable registry import do not launch model,
DB or cloud workloads. Write derived reports to a fresh output directory.

For a new live run, obtain the named resource authorization and private inputs.
Use admission, verified staging, readiness, bounded scheduling, collection and
teardown/absence checks from the existing lifecycle runner. Historical spending
approvals do not create a new budget. Update the experiment request, handoff and
cost ledger only when their actual state changes.

## 4. Review and validate source changes

| Surface | Check |
|---|---|
| Docs and agent orientation | `bash scripts/ci/check-doc-contracts.sh`, `scripts/pm/lint-agent-docs.sh` |
| Public root/layout | `bash scripts/ci/check-root-hierarchy-contract.sh` |
| Doc links and layout | `python3 scripts/ci/check_doc_structure.py`, `python3 scripts/ci/check_adr_index.py` |
| Shared source, packaging or CI | `bash scripts/ci/run_basic_ci.sh` plus focused tests |
| Platform/query evidence | `PYTHONPATH=src:. uv run pytest tests/seocho/test_experiment_platform.py tests/seocho/test_query_evidence.py -q` |
| Browser source | `node --check runtime/experiment_platform_web/app.js` |

The docs CI entrypoint is `.github/workflows/docs-consistency.yml`. Report exact
commands and remaining gaps. Keep compatible imports, `workspace_id`, policy and
read-only validation when touching inherited SDK/runtime dependencies. Existing
module ownership contracts still apply.

## 5. Land the reviewed change

Stage explicit paths from an isolated checkout. Confirm this repository's remote
is `tteon/gare2e`; never infer permission to publish in `tteon/seocho` or its
website. Preserve published results and upstream license/history.

Run `git diff --check`, pull/rebase, push the authorized change to `main`, and
confirm a clean checkout aligned with `origin/main`. If branch protection needs
a PR, keep the diff and validation reviewable and follow its required checks.
Describe the concrete behavior, evidence, validation and limits in the review.

## Inherited SDK and website tooling

The `seocho` package and CLI remain compatible support for existing experiment
imports. General application instructions live in `docs/RUNTIME_DEPLOYMENT.md`
and other inherited references linked from [the docs index](README.md). Shared
orchestration seams remain documented in `docs/INTERNAL_CLASS_DESIGN.md`.

`website/` is the inherited SEOCHO site source. Its
`website/scripts/generate-docs.mjs` generates mirrors; edit source docs rather
than generated files. `npm run check:docs` is that site's documentation check,
not a graph-agent benchmark.

The inherited `.github/workflows/docs-site-quality.yml` site jobs,
`.github/workflows/docs-site-deploy.yml`, and
`.github/workflows/docs-website-sync-dispatch.yml` run only in `tteon/seocho`.
GARE2E does not dispatch another site's mirror updates via
`SEOCHO_BLOG_SYNC_TOKEN`. GitHub source docs and the local result viewer are
GARE2E's current public entrypoints; no GARE2E site deployment is implied.
