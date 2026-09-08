# ADR-0224: Agentic graph retrieval trigger and evidence funnel

- Status: Accepted
- Date: 2026-08-25
- Related: ADR-0095, ADR-0189, ADR-0215, ADR-0218,
  `docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md`,
  `docs/experiments/AGENTIC_OS_GRAPH_QUERY_PROTOCOL.md`

## Context

SEOCHO's AgenticOS experiment has two graph-query lanes: GRBench/Graph-CoT
through Text2Cypher and DozerDB, and GTSQA through Text2SPARQL and Oxigraph.
The initial harness made paper-grade matched-context admission a prerequisite
for even a one-case end-to-end smoke. That conflated three different questions:

1. Does the model choose graph retrieval when only the question and tool
   descriptions are available?
2. If retrieval is selected, can the agent discover schema, generate and
   execute a useful graph query, repair failure, and answer from evidence?
3. Does SEOCHO improve that behavior at matched model, question, budget, and
   graph state?

Live diagnostic runs made the distinction concrete. In an A100 80 GB raw pilot
with Qwen3.6-27B, automatic tool choice selected LPG schema discovery on the
first turn and then attempted Cypher four times. All four statements were
rejected by the workspace/read validator, so DozerDB was never the bottleneck.
In the forced GTSQA plumbing case, the model repaired two rejected SPARQL
attempts and executed the third against Oxigraph, returning five rows, but did
not retrieve gold evidence or reserve a final-answer turn. Treating these as
generic "E2E failures" would hide successful triggering, failed query planning,
partial repair, ineffective evidence, and turn-budget exhaustion.

Raw chain-of-thought is not required to characterize this behavior. Typed
actions, tool calls, evidence outcomes, repair transitions, and final status
are sufficient and safer to retain.

## Decision

### 1. Treat retrieval triggering as an explicit stage

The canonical agentic graph-RAG funnel is:

`question -> retrieval decision -> backend/tool route -> schema discovery -> query generation -> validation -> graph execution -> evidence assessment -> bounded repair or abstention -> answer synthesis`.

The initial behavioral condition supplies the question and generic tool
descriptions only. It does not place an LPG/RDF schema, taxonomy slice, entity
IDs, predicate list, or query template in the initial conversation. Under the
behavioral baseline, native tool choice remains `auto`; choosing not to call a
tool is recorded as an outcome, not repaired by the harness.

Generic does not mean underspecified. Tool descriptions state when schema
inspection and query execution are appropriate and disclose the stable
read-safety contract. Rejections return bounded semantic codes such as
`missing_workspace_scope`, `query_policy_rejected`, or
`query_syntax_or_execution_error`; they never return raw provider errors or
silently repair the query. This keeps the raw baseline usable without granting
it SEOCHO taxonomy/schema hints.

### 2. Separate three evidence classes

- `plumbing_smoke` proves that the model endpoint, native tool parser,
  Text2Cypher/Text2SPARQL boundary, validator, graph engine, and receipt path can
  execute. It may force a native tool and is never answer-quality or agent
  behavior evidence.
- `behavioral_baseline` measures endogenous ReAct decisions with automatic tool
  choice. It may finish without retrieval and does not require token-matched
  controls.
- `main_comparable` is the paper-grade paired experiment. It requires frozen
  cohorts, identical tool surfaces, serving-token validation, fixed budgets,
  matched controls, and all admission gates.

Every run manifest and case receipt names its evidence class. Results from the
three classes must not be pooled.

### 3. Keep backend lanes and topologies explicit

- GRBench/Graph-CoT uses Text2Cypher against the fixed DozerDB LPG projection.
- GTSQA uses Text2SPARQL against case-scoped Oxigraph RDF and scores gold
  evidence triples.
- Single-agent ReAct, manager-to-specialist-as-tool, and true SDK handoff are
  separate topology treatments. In the primary multi-agent treatment the
  specialist owns graph tools, returns typed evidence rather than prose or
  hidden reasoning, and control returns to the same manager. A handoff control
  instead transfers control and is never pooled with agent-as-tool events.

Closed-book, tool-auto, and forced-direct-retrieval runs have distinct
interpretations. Reasoning on/off, topology, and SEOCHO on/off are controlled
dimensions rather than names for the same arm.

The manager-to-specialist tool is single-use per episode. Bounded query repair
belongs inside the specialist; an empty/error return causes manager abstention
instead of another specialist invocation. Tool descriptions enumerate positive
and negative routing cases and are frozen by digest. Final output is classified
as `answered`, `direct_answer`, `abstained`, `empty`, or
`unsupported_answer`; only the first two can be execution success. Errors retain
a content-free stage, semantic code, class, and fingerprint so failed nested
runs remain attributable without storing prompts, queries, or answers.

The discovery cache is bound to the exact runtime discovery-tool name,
description, and JSON schema. Main admission recomputes that digest and rejects
a cache produced before a description change. Every comparison arm also uses
the same per-case prompt-plus-completion token ceiling; a response that crosses
the ceiling is accounted for but discarded before tool execution or answer
synthesis.

### 4. Measure the trigger-to-evidence funnel

Content-free telemetry records:

- retrieval decision (`direct_answer`, `graph_tool`, `abstain`, or
  `unresolved`)
- time and LLM turns before the first graph tool
- schema-discovery and graph-query call counts
- query validation and execution outcomes
- empty/non-empty result and gold-evidence retrieval
- repair count, repair success, abstention, and terminal reason
- turns, model re-entries, handoffs, tokens, cache fields, LLM/tool/database
  latency, and CPU/GPU/process metrics

Resource attribution uses an exclusive agent-side critical path (`LLM HTTP`,
`graph-tool callback`, and `middleware orchestration residual`) plus separate
owner counters for the LLM, database, and middleware. Database server timers
remain auxiliary observations inside the tool callback and are not added to or
subtracted from wall time a second time. DozerDB CPU is process-attributable;
embedded Oxigraph CPU is labelled as a shared process and split only through
explicit stage/thread timers. Per-case GPU attribution is allowed only for a
serialized same-clock sampling window.

Primary derived measures include tool-use rate, effective-retrieval rate,
query-validity rate, empty-result rate, evidence P/R/F1, turns-to-evidence,
repair success, Accuracy/R/AR, and evidence-conditioned accuracy. A correct
answer without correct evidence remains visible as a possible guess.

vLLM `/metrics` is the ordinary serving evidence layer. Prefix-cache
effectiveness is calculated from query/hit counter deltas over a controlled
window, while queue, TTFT, TPOT, prefill, decode, E2E latency, token, request,
KV-cache occupancy/residency, and serving-configuration metrics are retained
when the pinned vLLM version exposes them. Server histograms are not assigned to
individual concurrent requests without a serialized window or request-level
correlation.

Nsight Systems is a separate, small frozen diagnostic replay for CUDA/NVTX/OS
runtime/NCCL causal analysis. Nsight Compute is used only for selected kernels.
Profiler-on numbers are labelled as perturbed and excluded from the primary
latency, throughput, and energy estimands. Agent and graph-database CPU work is
measured separately; Nsight does not replace middleware/database spans or
process counters.

Neo4j's documented native metric families are treated as a capability catalog,
not as proof that the pinned DozerDB Community-based image exposes them. Every
live run probes the actual Prometheus/JMX/CSV surfaces. The portable DozerDB
minimum is Bolt/client/server timing, process/container resource windows, and a
separate bounded read-only plan replay; missing native families stay explicit.

### 5. Evaluate SEOCHO at the stage it changes

SEOCHO OFF uses generic schema discovery, direct graph clients, and neutral
harness receipts. SEOCHO ON may add a bounded taxonomy/schema slice, semantic
route hints, workspace and policy enforcement, budget/abstention rules,
connection/runtime management, typed handoff, and immutable lifecycle digests.

The study attributes an effect to the first changed funnel stage. For example,
more graph-tool selection is a trigger effect; fewer invalid predicates is a
query-planning effect; fewer repeated failures is a repair effect; lower client
latency under prepared replay is a transport effect. S4 package results are not
decomposed additively without a factorial experiment.

## Consequences

Positive consequences:

- a liveness smoke can run without pretending to be a fair ablation;
- tool avoidance, invalid query, empty evidence, failed repair, and unsupported
  synthesis become distinguishable outcomes;
- ontology utility is tested primarily as relation/predicate resolution and
  query-planning support in QID-based GTSQA, not as an unsupported general
  entity-resolution claim;
- SEOCHO's Agentic OS claim can be tied to observable context, routing,
  governance, runtime, and recovery mechanisms.

Tradeoffs:

- receipts and analysis gain more states and require explicit evidence-class
  filtering;
- a behavioral baseline can score poorly because the model chooses not to use
  a tool; the harness must not silently rescue it;
- a plumbing smoke may prove execution while returning no useful evidence;
  that is not a successful retrieval result;
- token-matched C- construction remains necessary for the main context-effect
  estimand but no longer blocks ordinary E2E diagnostics.

## Rejected alternatives

- Always force Text2Cypher/Text2SPARQL. This measures query generation after a
  routing decision but cannot measure whether an agent chooses retrieval.
- Put the full LPG/RDF schema in the initial prompt. This removes schema
  discovery and biases the context treatment.
- Infer behavior from hidden chain-of-thought. This is provider-dependent,
  privacy-sensitive, and unnecessary when actions and evidence are observable.
- Report one aggregate E2E success bit. This hides the trigger, query, evidence,
  repair, and synthesis stages that SEOCHO is intended to manage.
- Enable Nsight for every benchmark repetition. Profiler overhead would change
  the latency distribution being estimated; controlled replay is sufficient
  for kernel-level causal diagnosis.
- Introduce LangGraph for the bounded manager/specialist call. The Agents SDK
  already provides the required agent-as-tool return-to-manager semantics and
  bounded turns. Reconsider only for durable branching/resume, multi-specialist
  scheduling, or explicit per-state cyclic repair that cannot be enforced by
  the SDK.

## Validation

The decision is implemented only when focused tests prove content-free trigger
receipts and evidence-class separation, both benchmark runners emit the funnel,
the catalog reports the new measures, and one live run demonstrates all three
outcomes: no-tool behavioral finalization, forced plumbing execution, and a
tool-auto graph-query attempt. Formal claims still require the admission gates
in `docs/experiments/AGENTIC_OS_GRAPH_QUERY_PROTOCOL.md`.

Model scaling is a staged extension rather than a pooled factor. First use a
same-family capacity ladder (approximately 7--9B, 27--32B, and 70--72B dense-
equivalent tiers) with identical prompts, tools, cases, budgets, precision, and
serving version. Mixture-of-experts models report total and active parameters
and form a separate external-validity panel. Hardware and tensor parallelism
are recorded as treatment dimensions; results from one-GPU and multi-GPU
serving are not attributed to model size alone. Each tier must pass a shared
ten-case diagnostic gate before pilot or main-cohort expansion.
