# Implement the agentic graph retrieval-trigger benchmark

This ExecPlan is a living document. The sections `Progress`, `Surprises &
Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to
date as work proceeds.

This plan follows `docs/maintainers/EXECPLAN_SPEC.md`.

## Purpose / Big Picture

After this work, a researcher can give an agent a benchmark question without
preloading the graph schema and observe the complete path by which it either
answers directly, abstains, or selects graph retrieval and reaches
Text2Cypher/DozerDB or Text2SPARQL/Oxigraph. The resulting artifact identifies
whether a miss came from retrieval choice, routing, schema discovery, query
generation, validation, graph execution, empty evidence, repair, or synthesis.

The same harness then compares SEOCHO OFF and ON without changing the question,
model, graph, or action budget. This makes the AgenticOS paper claim
falsifiable: SEOCHO must improve a named stage or reduce its operational cost;
merely producing extra ontology metadata is not a benefit.

## Progress

- 2026-08-31: The paid Gate-10 run exposed three pre-main operational gates:
  live Vast cost must include disk allocation; hybrid GPU samples must be
  collected on the remote vLLM host and joined to attempt wall-clock windows;
  and vLLM native tools require both `--enable-auto-tool-choice` and the
  model-specific tool parser. Qwen3.6 startup also separates download, weight
  load, compile/warmup, CUDA graph capture, and HTTP readiness. Run-specific
  evidence is kept in the ignored Gate-10 artifact directory.

- 2026-08-31: The raw16k-v6 split-host pilot completed 120 episodes. It is
  preserved as a diagnostic topology, not pooled primary evidence: same-case
  remote Prometheus scraping still cost about 2.25--2.46 seconds, and the
  reasoning configuration exhausted the 512-token per-request action budget.
  ADR-0226 moves the primary systems run to a co-located Vast appliance with
  local background sampling, artifact checksum/copy and hard teardown.

- [x] (2026-08-25) Separate plumbing, behavioral, and main-comparable evidence
  classes in ADR-0224.
- [x] (2026-08-25) Run a live question-only GTSQA diagnostic where Qwen3.6-27B
  finalized without a tool call.
- [x] (2026-08-25) Run a forced native-tool plumbing diagnostic that reached
  Oxigraph but returned zero rows.
- [x] (2026-08-25) Add content-free retrieval-trigger and evidence-funnel fields to the
  canonical experiment receipt.
- [x] (2026-08-25) Connect both GRBench Text2Cypher and GTSQA Text2SPARQL runners to the
  trigger contract.
- [x] (2026-08-25) Extend the result catalog with trigger, query, evidence, and repair
  metrics.
- [x] (2026-08-28) Add manager-to-specialist-as-tool topology for both backends
  with typed evidence return and keep true SDK handoff as a separate control.
- [x] (2026-08-28) Define a neutral three-layer LLM/database/middleware
  attribution contract, add per-case process counter windows, GPU
  utilization/power/energy sampling, and embedded Oxigraph thread-CPU stages.
- [x] (2026-08-28) Fail closed when model output contains textual tool markup
  that the vLLM parser did not expose as a native tool call.
- [x] (2026-08-28) Implement shared `closed_book`, `tool_auto`, and
  `forced_plumbing` conditions in both raw runners, plus a bounded pilot
  coordinator and paired condition analysis that excludes plumbing evidence.
- [x] (2026-08-28) Pass live local graph-runtime gates: PyOxigraph returned
  three rows for GTSQA case 102 and DozerDB returned one workspace-scoped row
  for the GRBench projection.
- [x] (2026-08-28) Run an A100 80 GB/Qwen3.6-27B raw preflight and one-case
  forced/automatic diagnostics; capture vLLM prefix-cache, GPU, process, and
  graph-stage evidence; checksum outputs; destroy and verify the Vast instance.
- [x] (2026-08-28) Make graph query failures repairable through content-free
  semantic error codes and document the neutral tool-trigger contract.
- [x] (2026-08-28) Expand vLLM observability to token/cache/request histograms,
  bounded cache configuration metadata, histogram-window deltas, and a separate
  Nsight diagnostic-arm contract.
- [x] (2026-08-28) Attribute repair and token usage by agent role, record
  agent-tool/evidence codec boundaries, split Bolt hydration/encoding/consume,
  and add optional read-only DozerDB PROFILE replay outside E2E timing.
- [x] (2026-08-29) Make trigger/tool descriptions explicit and digest-bound,
  enforce one shared fail-closed token ceiling across comparison arms, connect
  single/multi schedule lanes, and split impossible cross-backend admission
  into GRBench and GTSQA lane gates.
- [x] (2026-08-31) Promote query retries, empty-result retries, codec,
  validation, client/pool, submit, first-result, hydration/iteration, and
  serialization stages into the result catalog. Define a trace-driven vLLM
  co-design matrix covering prefix layout, KV residency, role-aware scheduling,
  structured decoding, and tool-pause utilization without conflating engine,
  middleware, and retrieval-quality effects.
- [x] (2026-08-31) Measure synchronous vLLM `/metrics` scrape cost, move the
  scrape boundary from every ReAct turn to once per serialized case, and emit
  observed wall, observer overhead, and observer-adjusted workload wall as
  separate fields.
- [x] (2026-08-31) Run the raw16k-v6 120-episode split-host diagnostic matrix,
  copy/checksum remote logs, and let the hard Vast destruction watchdog remove
  the instance. Its results remain topology-specific pilot evidence.
- [x] (2026-08-31) Implement co-located appliance-mode background vLLM metrics
  sampling and workload wrapper so `/metrics` is not called on the case path.
- [x] (2026-08-31) Record failed co-located appliance attempts V8--V12 as
  deployment evidence: Vast rsync `CLOSE_WAIT`, circular archived-bootstrap
  invocation, provider-running/SSH-readiness skew, and the vLLM image exposing
  `python3` without `python`. Add standalone bootstrap upload, remote input
  checksums, private stdout/stderr, lifecycle checkpoints, SSH readiness, and
  bounded teardown retries.
- [x] (2026-08-31) Add the local `$vast-agentic-e2e` recovery skill and freeze
  a no-new-instance rule until image/runtime preflight, service readiness,
  one-case graph-tool gates, and metric completeness pass fail-closed.
- 2026-09-02/03: Co-located attempt V13 reached vLLM and DozerDB readiness
  and passed the vLLM native-tool preflight, but the run-time Bolt projection
  of GRBench MAPLE/Physics (2.76M nodes, 67.3M relationships) consumed the
  whole remote budget; no gate case ran. The controlling session exited and
  took the local runner and cost watchdog with it, so the instance idled for
  8.8 h before a manual collect/destroy. Corrections: offline
  `neo4j-admin database import` from `export_grbench_import_csv.py` output
  before DozerDB starts, verify-only counting in the e2e, a bounded Bolt
  fallback, `vastai destroy ... -y`, scp without a `/.` suffix, and a
  `systemd-run --user` launch so the lifecycle outlives the session.
- 2026-09-03: Audit of raw16k-v6 receipts found every GRBench Text2Cypher
  attempt queried `_workspace_id = grbench:maple:physics` while the store
  was loaded as `grbench-maple-physics`. The 0-row/abstain outcome of the
  v6 GRBench lanes is therefore a scope mismatch, not model behaviour; the
  coordinator now pins the workspace id and the loader/exporter share it.
  GRBench rows in the v6 catalog remain plumbing evidence only.

- 2026-09-03: Gate-only attempt V14 validated the new path up to services:
  bulk import of 67.3M relationships took 129 s and DozerDB started with its
  indexes online. vLLM (TP=2) then hung after NCCL initialisation on a 2x A100
  PCIe host and the readiness loop consumed the remaining budget; the runner
  destroyed and verified the instance on its own. The appliance now pins
  `NCCL_P2P_DISABLE=1`, logs GPU topology, and fails fast on a stalled vLLM
  start-up.
- 2026-09-03: Gate-only attempt V16 reached all eight one-case cells. GTSQA
  cells failed in about two seconds because the appliance bundle omitted the
  repo-root `runtime` package the Text2SPARQL runners import, and the
  coordinator had preserved only a digest of runner stderr. GRBench cells
  all triggered schema discovery and issued Cypher; the single-agent
  non-reasoning cell validated and executed five queries with non-empty rows,
  while the other three cells were rejected by the read-only/workspace-scope
  validator (`missing_workspace_scope`, `query_policy_rejected`), which is
  the raw-baseline behaviour the SEOCHO scope-injection treatment targets.
  Corrections: bundle ships `runtime/`, bootstrap import preflight before
  vLLM start-up, private runner stream capture, volatile-log tolerance in
  artifact verification.
- 2026-09-03: Gate-only attempt V17 completed every infrastructure state
  and executed all eight one-case cells (bulk import 139 s, vLLM ready in
  4.5 min, teardown verified). The gate itself failed closed: raw B0 cells
  triggered the graph tool and discovered schema 8/8, but only 3/8 produced a
  validator-accepted query (GRBench single/non-reasoning with non-empty rows;
  both GTSQA multi-agent cells with empty results). Rejections were
  `missing_workspace_scope` and `query_policy_rejected`; GTSQA
  single/non-reasoning exhausted its token budget without a query. The
  gate's B0 admission criterion (validated vs attempted) is now the open
  decision before a paid full matrix.
- 2026-09-05: Attempt-window analysis of V17 (join of GPU, process, and vLLM
  samples to case windows; per-cell decomposition and timelines) showed the
  multi-agent runners attributing the whole case wall to the middleware
  residual: their `llm_ms` came from a per-case vLLM `/metrics` window that
  appliance mode disables. Both Agents SDK runners now time every
  chat-completion request on the client (httpx hooks) with the same boundary
  as the single-agent runner and record the time source; the telemetry join
  also accepts process-window schema v2 and integer case ids. First
  co-location reading: the one cell that reached DozerDB shows GPU busy share
  0.53 while database CPU ran 12 s; every other cell is decode-bound at
  0.90–1.00 busy share.
- [x] (2026-09-03) Replace run-time GRBench Bolt projection with offline
  bulk import on the appliance; fix runner teardown/collection defects;
  pin the GRBench workspace id end-to-end.
- [ ] Build and admit the co-located appliance image with DozerDB, vLLM,
  agent/embedded-Oxigraph workload, CPU placement and artifact lifecycle.
- [ ] Run the closed-book, tool-auto, and forced-plumbing pilot matrix for
  reasoning on/off.
- [ ] Run SEOCHO OFF/ON paired arms only after main admission passes.
- [ ] Freeze MARA judging, statistical analysis, and paper tables.

## Surprises & Discoveries

- Observation: V8--V12 never reached the agent workload. V8 stalled in the
  provider rsync-daemon transport; V9/V10 invoked a bootstrap script before
  extracting its archive; V11 treated provider `running` as SSH-ready; V12
  assumed a `python` alias that the vLLM image did not provide. Consequence:
  provider, transport, archive, bootstrap, service, agent, and telemetry gates
  are separate states with checkpointed evidence. Exit codes without preserved
  stderr are not an acceptable diagnosis.
- Observation: the exported DozerDB runtime deliberately contains only
  `opt/`, `startup/`, and `var/`, so it does not overwrite the vLLM/CUDA base
  system. It does, however, depend on Docker image environment such as
  `JAVA_HOME`, `NEO4J_HOME`, and PATH, and its upstream entrypoint assumes a
  `neo4j` user plus `su-exec`. Consequence: restore required environment and
  validate or replace Docker-entrypoint-only assumptions before Vast launch.

- Observation: a one-case E2E smoke had been blocked by exact token matching
  intended only for the C- paper control. Consequence: liveness and causal
  comparison must use separate evidence classes.
- Observation: Qwen3.6-27B with native tools available used no tool in the
  question-only GTSQA case. Consequence: retrieval triggering is an outcome,
  not an assumed precondition.
- Observation: forcing schema discovery and SPARQL execution proved the tool
  boundary and Oxigraph wrapper but returned zero rows. Consequence: tool
  invocation, query execution, and effective retrieval need separate metrics.
- Observation: Qwen3.6-27B loaded about 51.1 GiB of weights. A100 80GB was
  sufficient; H200 was not required. The first A100 host failed because its
  container exposed only 40GB disk, not because of GPU memory.
- Observation: A100 used FlashAttention 2/Triton and spent materially longer in
  cold compile/warmup than H200's FlashAttention 3/FlashInfer path. Preserve
  backend and cold-start measurements in the systems results.
- Observation: the earlier GRBench one-case receipt marked a textual
  `<tool_call>` response as a successful final answer even though vLLM emitted
  no native `tool_calls` object. Consequence: parser conformance must fail
  closed before an execution can count as an E2E success.
- Observation: the GRBench automatic run selected schema discovery at the
  first turn and then produced four validator-rejected queries. Triggering was
  not the miss; opaque validator feedback prevented useful repair.
  Consequence: disclose stable tool constraints and semantic rejection codes
  in both OFF and ON conditions.
- Observation: GTSQA repaired two malformed SPARQL calls and executed the third
  in about 0.76 ms, while model calls consumed about 27.76 s. The returned five
  triples had zero gold recall and no final-answer turn remained. Consequence:
  query execution, evidence relevance, and budget allocation are distinct
  optimization targets; database speed alone cannot fix this case.
- Observation: vLLM's OpenAI usage payload did not expose `cached_tokens`, but
  Prometheus did expose prefix-cache query/hit counters and per-request
  prefill/decode timers. Consequence: provider usage and server metrics remain
  separate evidence sources rather than treating a missing usage field as no
  cache reuse.
- Observation: the running DozerDB 5.26.3.0 container has no native metrics
  exporter configured. Neo4j's current native metric catalog is therefore a
  capability list, not evidence for this image; Bolt summaries, process
  counters and plan replay remain the portable baseline until a live exporter
  probe succeeds.
- Observation: the first schedule-derived `run_id` covered only the case/arm
  entry, so reasoning and condition treatments collided when catalogs were
  combined. Consequence: run identity now includes a digest of lane, model,
  reasoning, condition, budgets, schedule, dataset, and discovery artifact.
- Observation: the initial GRBench manager/specialist episodes completed model
  work but failed while writing private predictions through the content-free
  receipt writer. Consequence: private answer/evidence/error JSONL has a
  separate named writer and retains the same correlation fields; artifact
  commit failures are not classified as agent reasoning failures.
- Observation: fetching and parsing the remote vLLM Prometheus payload took
  about 1.2 seconds per scrape. Per-turn before/after snapshots inserted pauses
  into the ReAct trajectory and made observer time look like middleware
  residual. A live closed-book diagnostic measured 5,302.8 ms observed wall,
  2,565.7 ms observer overhead, 2,737.1 ms adjusted workload wall, and only
  0.141 ms adjusted middleware residual. Consequence: ordinary runs take one
  before/after snapshot per serialized case; profiler and high-frequency
  sampling stay separate diagnostic treatments.
- Observation: the paid Vast vLLM environment is itself a Docker container and
  exposes neither a Docker daemon nor Compose. The local GRBench DozerDB
  database contains about 2.76 million nodes and 67.34 million relationships;
  its database plus transaction log is about 13.1 GB, and a 384 MB upload
  probe did not finish in 139 seconds. Consequence: moving only one backend
  during the paid matrix would change the Text2Cypher/Text2SPARQL topology and
  consume the remaining GPU budget. The main raw matrix retains the declared
  local-agent/local-graph/remote-vLLM topology; co-location is a separate
  topology ablation.
- Observation: restarting the local DozerDB container changed its host PID.
  The first v4 cases still had valid LLM, query, and answer traces but their
  configured database process window pointed at the former PID and returned
  null CPU/RSS/I/O. Consequence: preserve v4 as a diagnostic, resolve the
  running database process at matrix start, fail closed when it is invalid,
  and use fresh v6 results for the paper matrix.
- Observation: Docker's container state PID is the `tini` init process, not the
  DozerDB JVM. A v5 admission receipt showed about 0.9 MB RSS and one thread,
  proving the wrong process was observed. Consequence: resolve the child whose
  command is `java` from `docker top`, confirm `/proc` identifies it as Java,
  and reject the run otherwise. A live follow-up measured the JVM at about
  2.36 GB RSS, 103--104 threads, and 4.14 CPU seconds for one Cypher episode;
  fresh v6 is the first admissible process-attribution root.
- Observation: IBM vLLM-Hook's upstream requirements cap vLLM at 0.21, while
  the paid baseline runs vLLM 0.27.1. Its serve path changes worker startup and
  uses eager execution in the documented examples. Consequence: do not patch
  the primary server mid-matrix. Record the compatibility gate as failed and,
  if internal-state evidence remains useful, run Hook only in a separately
  versioned frozen-trace diagnostic excluded from primary performance tables.
- Observation: GRBench/Graph-CoT provides question-answer targets without a
  gold prose rationale. GTSQA provides gold answers, SPARQL, and answer
  subgraphs/triples but likewise no gold prose rationale. Consequence: the
  GraphRAG-Bench R/AR rubric cannot be copied into these lanes; report those
  fields as not applicable and use blinded answer Accuracy plus GTSQA gold-
  triple precision/recall/F1.
- Observation: the selected `vllm/vllm-openai:v0.27.1` Vast image contains
  neither `nsys` nor `ncu`. Consequence: retain Prometheus, GPU/process samples,
  and server logs for this gate; run kernel-level profiling only in a separate
  image-qualified frozen-trace replay instead of mutating the paid matrix.
- Observation: GTSQA single-agent wrapped vLLM metrics outside an episode
  function whose `wall_ms` already excluded those scrapes, then subtracted the
  observer overhead again. The impossible invariant `adjusted wall < summed
  LLM HTTP` exposed the error. Consequence: measure the outer observed wall at
  the same boundary as the before/after snapshots, preserve the inner episode
  duration only as a diagnostic, and fresh-rerun the two affected non-reasoning
  GTSQA single-agent cells.
- Observation: treatment/configuration digests alone did not distinguish a
  corrected harness from its predecessor. Consequence: execution identity now
  includes a digest of the coordinator, lane runner, agent loop, and
  observability sources so code changes produce fresh immutable run IDs.

## Decision Log

- Decision: start the behavioral baseline with the question and generic tool
  descriptions only. Do not put the LPG/RDF schema or taxonomy in the initial
  prompt. Rationale: schema discovery and context selection are experimental
  stages.
- Decision: keep `tool_choice=auto` for behavioral evidence. A forced native
  tool is permitted only in `plumbing_smoke`. Rationale: forced retrieval cannot
  measure the trigger.
- Decision: do not retain raw chain-of-thought. Use provider reasoning-token
  counts when available and typed action/evidence traces. Rationale: actions
  are sufficient, portable, and privacy-safe.
- Decision: GTSQA ontology utility is primarily predicate/relation resolution,
  route selection, and repair reduction. Do not claim general entity-resolution
  value from a benchmark that already supplies QIDs.
- Decision: use A100 80GB for Qwen3.6-27B pilots when real writable disk is at
  least 70GB. Reserve H200 multi-GPU capacity for large-model treatments such
  as MiniMax-M2.7.
- Decision: keep single-agent ReAct, manager-to-specialist-as-tool and true SDK
  handoff as different topology treatments. Agent-as-tool is primary because
  the same manager resumes after typed evidence; handoff transfers control.
- Decision: retain the Agents SDK for this bounded topology. Reconsider
  LangGraph only for durable branching/resume, multi-specialist scheduling or
  explicit per-state cyclic repair that the SDK cannot enforce.
- Decision: separate exclusive critical-path time from resource ownership.
  `LLM HTTP + graph-tool callback + middleware residual` is additive;
  database server timings are overlapping observations and are not added again.
  Rationale: a three-way percentage based on overlapping clocks would
  over-count time and make an unsupported systems claim.
- Decision: the first paid raw pilot is ten cases per lane, reasoning on/off,
  one repetition. Rationale: it is an admission/failure-shape slice; larger
  cohorts would waste GPU time before native-tool and non-empty retrieval rates
  are known.
- Decision: scale models in two panels: a same-family 7--9B/27--32B/70--72B
  capacity ladder for a scale estimand, and a separate Qwen/Gemma/MoE external-
  validity panel. GPU count, tensor parallelism, precision, and interconnect are
  explicit serving factors rather than hidden model-size effects.
- Decision: collect vLLM `/metrics` in ordinary runs and run Nsight Systems only
  on a small frozen replay. Profiler-on latency/throughput/energy is perturbed
  diagnostic evidence and is excluded from primary tables; Nsight Compute is
  kernel-selective only.
- Decision: treat Neo4j native metrics as optional DozerDB capabilities. Store
  the actual exposed metric names/version and leave unsupported families null;
  never substitute host/container counters under native database names.
- Decision: use frozen raw E2E request traces as the input to later vLLM engine
  ablations. Hold prompts and generated queries fixed for engine experiments,
  and hold the engine fixed for middleware/prompt experiments. Rationale: a
  higher prefix-hit rate or GPU utilization is not an engine contribution if it
  came from changing semantic context, nor is it useful if query/evidence
  quality regresses.
- Decision: exclude the collided reasoning slice and pre-fix GRBench
  multi-agent slice from performance tables. Preserve them as harness-failure
  diagnostics, then rerun only the affected cells under fresh execution IDs.
  Rationale: correcting metadata in place would weaken provenance, while
  rerunning unaffected cells would waste paid compute.
- Decision: treat observability as a measured participant in the system, not
  free instrumentation. Ordinary case receipts subtract synchronous metrics
  collection from the workload wall while retaining the observed E2E wall.
  Rationale: otherwise a slower Prometheus path can be mistaken for slower
  agent orchestration and can causally perturb prefix-cache residence.
- Decision: keep the raw paid matrix on one frozen distributed deployment:
  agent, DozerDB, and embedded Oxigraph on the local CPU host; vLLM and GPU
  samplers on Vast. Do not call it co-located. Compare loopback/co-located,
  Docker bridge, and remote Bolt/HTTP as a later topology ablation with the
  same frozen request traces. Rationale: topology is an experimental factor,
  not an implementation detail to change midway through a matrix.
- Decision: official vLLM Prometheus metrics plus a small Nsight replay are the
  primary engine observability path. vLLM-Hook is exploratory and version-
  isolated until it supports the production engine version. Rationale: an
  instrumentation plugin that changes vLLM version and eager/CUDA-graph mode
  cannot establish low-overhead behavior for the baseline engine.
- Decision: never synthesize a gold rationale with the same judge used to score
  it. R/AR enter this study only through a separately versioned human-reviewed
  rationale annotation. Rationale: otherwise the metric measures judge self-
  consistency rather than benchmark reasoning quality.
- Decision: admit latency only when `observed_wall >= observer_overhead` and
  `adjusted_wall >= llm_http + graph_tool_callback` within timer precision.
  Rationale: these algebraic invariants catch boundary mismatches that schema
  validation and unit tests alone cannot detect.

## Outcomes & Retrospective

The architectural decision and raw diagnostics are complete. Native tool calls
and server-side prefix-cache counters work. The latest behavioral LPG case did
trigger schema discovery and Cypher generation but exhausted five turns on
workspace/read validation. The RDF plumbing case crossed Oxigraph after two
repairs, returned non-empty but irrelevant evidence, and exhausted its answer
budget. These are baseline failure shapes, not SEOCHO results. They identify
query-contract repair, evidence relevance, and turn allocation as the next
gates before expanding the paid matrix.

Update this section after the pilot matrix with trigger rate, effective
retrieval, evidence P/R/F1, blinded answer Accuracy, repair behavior, latency,
and cost. R/AR remain not applicable for the current source datasets.

## Context and Orientation

SEOCHO is ontology-aligned middleware between agents and graph databases. The
canonical evaluation code lives under `src/seocho/eval/`; benchmark entry
points live under `scripts/benchmarks/`; public experiment contracts live under
`docs/experiments/`; generated corpora, predictions, traces, and receipts stay
under ignored `.seocho/` paths.

The LPG lane uses GRBench/Graph-CoT questions and a fixed projection into
DozerDB. The agent writes read-only Cypher. GRBench has gold answers but no gold
Cypher or gold evidence subgraphs, so it cannot support golden-query claims.

The RDF lane uses GTSQA. Each case contains a question-scoped RDF graph and gold
evidence triples. The agent writes SELECT-only SPARQL against PyOxigraph.
Retrieval precision, recall, and F1 are directly measurable.

A retrieval trigger is the agent decision to enter a graph tool path. Effective
retrieval means that the path returns useful evidence, not merely that a tool
was called. A typed evidence bundle is a structured payload containing selected
triples, required slots, filled and missing slots, provenance, confidence, and
execution status. Insufficiency is a named missing or weak part of that bundle
that remains visible through synthesis.

Relevant files include:

- `src/seocho/eval/agent_execution.py` for content-free turn/tool receipts;
- `src/seocho/eval/agentic_graph_agents.py` for native ReAct and Agents SDK
  graph-query behavior;
- `src/seocho/eval/agentic_os_catalog.py` for normalized metrics;
- `scripts/benchmarks/run_direct_react_text2cypher.py` for the LPG baseline;
- `scripts/benchmarks/run_gtsqa_text2sparql_react.py` for the RDF baseline;
- `scripts/benchmarks/run_gtsqa_agents_sdk_handoff.py` for the compatibility-
  named manager/specialist-as-tool prototype;
- `docs/experiments/AGENTIC_OS_GRAPH_QUERY_PROTOCOL.md` for formal admission.

## Plan of Work

First, extend `AgentExecutionRecorder` with one monotonic event ordinal and
elapsed timestamp per LLM/tool event. Derive a content-free trigger block that
states whether a graph tool was selected, how many turns and milliseconds
preceded it, and which schema/query tools were called. Runner-specific code
then enriches the block with query validation, execution, non-empty evidence,
gold evidence, repair, and terminal status.

Second, make the GRBench and GTSQA entry points emit an explicit evidence class.
`plumbing_smoke` may force native tools and is excluded from behavior/quality
tables. `behavioral_baseline` keeps native tool choice automatic and does not
require matched C- controls. `main_comparable` requires all current frozen
artifact and serving-token gates.

Third, normalize the funnel into the result catalog. Keep all attempts in the
denominator. Report trigger rate separately from effective retrieval, and
separate query validity, execution success, non-empty results, gold evidence,
answer correctness, rationale correctness, and abstention.

Fourth, implement equivalent specialist topology coverage. The user-facing
manager receives only the question and generic specialist description. The
Text2Cypher/Text2SPARQL specialist alone owns graph tools. It returns a bounded
typed evidence packet as an agent-tool result, after which the same manager
resumes. True SDK handoff remains a separate control. Total action and output-
token budgets remain fixed across topology comparisons.

Finally, run the pilot matrix and only then promote to main paired arms. The
pilot is diagnostic. Main results require frozen cohorts, repeated runtime
trials, serving-token matched context controls, blinded MARA judgments, and
paired confidence intervals.

## Concrete Steps

From the repository root, implement and validate the receipt contract:

    UV_CACHE_DIR=/tmp/seocho-uv-cache uv run pytest -q \
      tests/seocho/test_agent_execution.py \
      tests/seocho/test_agentic_graph_agents.py \
      tests/seocho/test_agentic_os_catalog.py

Run one question-only behavioral case and inspect that a no-tool finalization
is preserved as `direct_answer`, not silently converted to retrieval. Run one
plumbing case and inspect that it contains schema and query tool events but is
tagged `plumbing_smoke`. Store outputs under
`.seocho/benchmarks/agentic-os/`.

Before a paid model run, execute the local admission script. Provision the
smallest GPU that fits the selected model only after admission. For
Qwen3.6-27B BF16, require one 80GB GPU and verify container disk before model
download. Copy/checksum artifacts, destroy the instance, and verify absence.

After focused tests, run:

    bash scripts/ci/run_basic_ci.sh
    bash scripts/ci/check-doc-contracts.sh
    python3 scripts/ci/check_adr_index.py
    git diff --check

## Validation and Acceptance

The implementation is accepted when all of the following are observable:

1. A no-tool automatic ReAct run records `direct_answer`, zero graph tools, and
   remains in the behavioral denominator.
2. A forced plumbing run records its forced status, crosses schema discovery
   and graph execution, and is excluded from answer-quality comparisons.
3. A tool-auto run that executes a graph query distinguishes zero rows from
   gold-evidence retrieval.
4. GRBench and GTSQA receipts use the same trigger vocabulary while preserving
   lane-specific correctness metrics.
5. The catalog reports trigger rate, effective retrieval, query and empty-result
   rates, evidence quality, repair, answer quality, and cost independently.
6. SEOCHO ON/OFF runs preserve model, question, graph, seed, budget, and
   topology, and differ only in the declared treatment.
7. No prompt, query text, evidence value, answer, or hidden reasoning appears in
   default telemetry.

A live service claim must name model/container/database versions, graph digest,
hardware, disk, concurrency, warmup, cache phase, and skipped components.
Mocks validate contracts only.

## Idempotence and Recovery

All generated runs use fresh output directories and immutable manifests. A
failed attempt remains recorded and is not overwritten. Frozen artifacts are
recreated under a new filename/digest. Do not weaken an admission gate to
resume a failed run.

Vast instances are created only immediately before a validated GPU step. On any
failure, copy available content-free logs, destroy the exact instance ID, and
confirm that `vastai show instance ID --raw` reports absence. A host whose GPU,
disk, or SSH contract differs from the offer is rejected before downloading a
model.

## Artifacts and Notes

Public architecture and protocol decisions belong in this ADR, the experiment
protocol, and `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`. Raw questions, answers,
queries, evidence, model output, provider credentials, and generated benchmark
results remain in `.seocho/`. Local work status is mirrored into Beads but
`.beads/` is never committed.

The two existing diagnostic directories are:

- `.seocho/benchmarks/agentic-os/e2e-gtsqa-q102-smoke2/`;
- `.seocho/benchmarks/agentic-os/e2e-gtsqa-q102-plumbing-forced/`.

The 2026-08-28 A100 raw pilot and copied remote telemetry are under:

- `.seocho/benchmarks/agentic-os/raw-baseline/20260828-a100-qwen/`.

They are smoke evidence only and must not appear in the main aggregate.

## Interfaces and Dependencies

The public experiment contract uses OpenAI-compatible native tool calls and the
OpenAI Agents SDK for the specialist topology. DozerDB remains the LPG backend;
PyOxigraph remains the RDF read engine. Tracing is vendor-neutral JSONL/OTLP.
MARA is the preferred blinded LLM-as-judge provider. Raw chain-of-thought is
never an artifact.

The receipt must remain a JSON-serializable, content-free mapping. New fields
are additive during the experiment phase. `workspace_id`, run/case/arm/seed,
topology, evidence class, graph/taxonomy/tool-schema digests, and terminal
status are mandatory correlation dimensions for main-comparable evidence.

## SEOCHO Evidence Contract

Every formal graph answer carries an internal typed evidence bundle containing
intent, route, required slots and relations, selected triples or LPG records,
slot fills, missing slots, provenance, confidence, query/execution digests, and
insufficiency. The answerer may use only this bundle for graph-grounded claims.
Model prior knowledge is not evidence. A correct answer with no gold evidence
is scored separately as a possible guess.

## SEOCHO Review Panel

The `professor_agent` lens accepts the design because it separates endogenous
tool selection from forced query generation and avoids claiming ontology-driven
entity resolution from QID-resolved GTSQA. It would reject a paper table that
pools plumbing and behavioral results.

The `software_engineer_agent` lens requires one shared typed funnel vocabulary,
additive receipt fields, fail-closed main admission, fresh outputs, and tests
for no-tool, empty-result, repair, and handoff states. It would reject hidden
runner-specific meanings for the same metric name.

The `computer_systems_agent` lens requires model re-entry, cache, CPU/GPU,
database, network/client, tool, and handoff costs to remain separable. It also
requires disk admission and automatic instance teardown. It would reject an OS
claim based only on answer accuracy or a mock latency number.

The panel decision is to proceed with the staged funnel and preserve negative
results. The decision changes if a pilot shows the new stages cannot be
identified reliably without content capture, or if instrumentation overhead
materially changes agent behavior.

## Cost, Latency, and Provider Policy

Use local deterministic tests first. For Qwen3.6-27B BF16, prefer a verified
A100 80GB or equivalent rather than H100/H200; record actual writable disk and
backend. Reserve H200 multi-GPU runs for large-model treatments whose weights or
KV budget require them. Model startup/download/compile is reported separately
from per-case inference.

Provider credentials stay in environment variables and never enter manifests
or traces. MARA judging reads private predictions in a separate blinded job.
No paid run begins when local admission fails, and no idle instance remains
running between implementation slices.
