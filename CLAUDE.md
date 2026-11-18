# CLAUDE.md

Claude-specific execution guide for GARE2E. Use `AGENTS.md` as the primary
coding-agent guide; this file adds experiment scope and review discipline.

## Product Frame

GARE2E owns the graph-agent experiment/measurement workflow and read-only
comparison platform described in `docs/GARE2E_SCOPE.md`. Lead public docs with
workload admission, Agent/LLM/Tool evidence, query/plan inspection and reproducible
results. Existing general SDK, graph-memory, ontology and FinBench generator
features are inherited SEOCHO support; keep their attribution explicit.

New work should answer a named experiment or evidence-inspection question.
Preserve published results and distinguish implementation, offline validation
and actual service measurements. Separate local research checkouts are not a
source of published GARE2E results until reviewed and transferred.

Inherited runtime invariants remain relevant when a shared dependency is touched:

- agent runtime: OpenAI Agents SDK; preserve each report's actual runner/model
- graph backend baseline: DozerDB; preserve each report's actual DB revision
- tenancy: `workspace_id` propagated end-to-end
- tracing: vendor-neutral `none | console | jsonl | otlp`; keep metric definitions
  and source coverage with exports
- ontology governance: Owlready2 only in offline governance paths

## Read Order

For most changes:

1. `README.md` and `docs/GARE2E_SCOPE.md`
2. `AGENTS.md`
3. `docs/REPOSITORY_LAYOUT.md`
4. `docs/WORKFLOW.md`
5. `docs/decisions/DECISION_LOG.md`

For Graph-RAG, semantic retrieval, public memory answering, or multi-agent query
flow, also read `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`.

## Architecture Boundaries

GARE2E's primary surfaces are `scripts/benchmarks/`, `src/seocho/eval/`,
`runtime/experiment_platform.py`, `runtime/experiment_platform_web/` and
`docs/agentic-os/`. The following shared SDK/runtime boundaries are inherited
support; use them deliberately when the experiment path needs a dependency fix.

Control-plane surfaces:

- `runtime/agent_server.py`
- `runtime/policy.py`
- `runtime/memory_service.py`
- `.github/`
- `docs/decisions/`

Data-plane surfaces:

- `src/seocho/index/`
- `src/seocho/query/`
- `src/seocho/ontology.py`
- `src/seocho/store/`
- `extraction/` only where legacy service or compatibility behavior is still
  explicitly owned there

Keep canonical SDK behavior in `src/seocho/`. Do not put new canonical logic in
root compatibility surfaces just because imports currently allow it.

## Runtime Guardrails

- Runtime write/compute paths must preserve `workspace_id`.
- New endpoint/action paths must pass policy checks.
- Dynamic labels, relationship types, properties, and database names must be
  validated before Cypher interpolation.
- Query tools should remain read-safe unless write mode is explicitly required.
- Prefer `elementId(...)` over deprecated `id(...)` in runtime/query Cypher.
- Keep ontology reasoning and SHACL-style governance out of hot request paths.
- DozerDB procedure privileges stay scoped to `apoc.*,n10s.*,semantics.*,gds.*`
  (no wildcard unrestricted; `gds.*` serves OpenGDS for `examples/mdm/`, jar
  installed by `examples/mdm/01_install_gds.sh`). Compose config env vars must
  use the `NEO4J_`-prefixed underscore form — raw dotted keys are silently
  ignored by the image.

## Work Tracking (beads)

All coding and experiment work in this repo is tracked in the local beads
tracker (`bd` CLI, state in untracked `.beads/`). Do not start coding from
chat context alone.

- Session start: `bd ready` and `bd list --status in_progress`; pick up or
  create the issue the request belongs to before editing code.
- Before coding: `bd update <id> --claim` (or `bd create` a `task|bug|decision`
  with `--deps discovered-from:<parent>` when it emerged from other work).
- While working: `bd update <id> --append-notes "<date>: what changed, evidence
  path>"` at each meaningful step; findings that change scope become their own
  issue, not a chat aside.
- Landing: reference the bead id in the PR body; `bd close <id>` only after the
  change is merged and validated.
- Async lab requests under `.agents/lab/requests/` carry a `bead:` field; a
  request without one is not ready to execute.
- `.beads/` itself stays untracked (see Public Repo Hygiene); the rule, not the
  state, is what this file commits.

## Agent Behavior

- Start from the user request and repo contracts, not from speculative cleanup;
  bind the work to a beads issue first (see Work Tracking).
- Keep one cohesive change per PR/commit unless the user explicitly asks for a
  broader sweep.
- Surface tradeoffs when changing public SDK/runtime behavior.
- For docs-only changes, avoid pretending runtime behavior changed.
- Scope public framing to GARE2E-specific work. Keep the inherited `seocho`
  distribution/import namespace compatible and credit upstream.
- `website/` remains inherited site source. GARE2E docs changes must not dispatch
  publication to the separate SEOCHO website.
- For architecture changes, update docs and ADRs in the same change.
- Do not commit local agent/editor/tool state directories.

## Review Stance

Before landing, answer these questions from the diff:

- Did the change keep canonical SDK/engine behavior in `src/seocho/` and
  deployment-shell behavior in `runtime/`?
- Did it avoid adding new product behavior to compatibility-only extraction
  shims?
- If it changed query, retrieval, or answering behavior, did it preserve Cypher
  validation, read-safety, and Graph-RAG handoff contracts?
- If it changed runtime APIs, did it preserve `workspace_id`, policy checks, and
  typed response models?
- If it changed public repository structure, did it update layout docs and the
  root hierarchy contract?
- Is the validation proportional to the blast radius, and is every skipped
  check named explicitly?

## Testing Discipline

Use the risk of the touched surface to choose validation:

- Runtime/API/SDK behavior: `bash scripts/ci/run_basic_ci.sh`
- Docs contract: `bash scripts/ci/check-doc-contracts.sh`
- Root/public hierarchy: `scripts/ci/check-root-hierarchy-contract.sh`
- Runtime ownership: `bash scripts/ci/check-runtime-shell-contract.sh`
- Module ownership: `bash scripts/ci/check-module-ownership-contract.sh`

Report exact commands and gaps. Do not say "tested" without the command.

## Public Repo Hygiene

These are intentionally not tracked public surfaces:

- `.agents/`
- `.beads/`
- `.claude/` — **except `.claude/skills/`**, which is version-controlled
  shared project tooling (ADR-0113). Claude Code auto-loads project skills
  from there on clone; everything else under `.claude/` (settings, local
  state) stays untracked.
- `.githooks/`
- `.jules/`
- `.serena/`
- `experiments/retrieval_comparison/`
- root `setup_*.sh`
- root `dataset/`, `images/`, `ontology/`, or `seocho/`

Use `scripts/`, `examples/`, `docs/`, `.github/`, or `src/seocho/` instead.
Shareable Claude Code skills go in `.claude/skills/<name>/SKILL.md`.

## Landing

Before pushing:

1. run relevant validation
2. check `git diff --check`
3. `git pull --rebase`
4. push to `main`
5. confirm `git status --short --branch` is clean and aligned with
   `origin/main`
