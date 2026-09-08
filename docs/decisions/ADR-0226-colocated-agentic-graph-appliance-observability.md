# ADR-0226: Co-located Agentic Graph appliance observability

- Status: Accepted
- Date: 2026-08-31
- Related: ADR-0224, ADR-0225, `docs/experiments/AGENTIC_OS_GRAPH_QUERY_PROTOCOL.md`

## Context

The first raw Qwen3.6-27B matrix used a split topology: vLLM ran on a Vast A100
host while the agent, DozerDB and Oxigraph ran locally. The raw matrix completed
120 episodes, but fetching vLLM's Prometheus endpoint over SSH cost roughly
2.25--2.46 seconds per case even after reducing the observer from per-turn to
per-case snapshots. This was reported and excluded from adjusted workload wall,
but it remains an avoidable measurement perturbation and makes host-resource
attribution depend on clock alignment across machines.

The Vast execution environment is itself a container and does not expose a
Docker daemon or Compose. Running a nested copy of the public deployment
compose is therefore neither a portable admission assumption nor a faithful
single-node measurement surface.

## Decision

The paper's primary systems topology is a versioned, single-host experimental
appliance on one Vast instance. It starts vLLM, DozerDB, the agent workload and
content-free samplers in one container namespace. Oxigraph remains an embedded
RDF engine in the GTSQA agent process until a separate-server ablation is
explicitly introduced.

The appliance has these rules:

1. It is a dedicated image/entrypoint, not Docker-in-Docker or a claim about
   SEOCHO's production Compose deployment. Parent vLLM and DozerDB image
   digests, model, graph dump, workload command digest, CPU placement and
   hardware receipt are retained.
2. A same-host background sampler persists vLLM Prometheus counters,
   host/process counters and GPU utilization/memory/power. Agent cases carry
   correlation markers only; they do not synchronously scrape `/metrics`.
3. DozerDB is attributed through its JVM PID, Bolt/client stage timers and a
   separate bounded plan replay. Oxigraph's embedded work remains explicitly
   labelled shared-process until it is isolated.
4. The split-host run remains an immutable diagnostic topology. It is never
   pooled with co-located latency, CPU, energy or cache estimates.
5. A run copies artifacts once, verifies SHA256 digests, then destroys the
   exact paid instance and verifies absence. The billing ceiling applies to
   startup, workload and artifact-copy time.

## Consequences

The co-located topology enables causal CPU contention and GPU-feeding analysis
without SSH observer pauses. It also changes the experimental environment:
DozerDB, agent and vLLM frontend now share CPU/memory resources. Therefore it
is a named topology factor, not a transparent optimization or an SEOCHO-on
treatment.

Prometheus-style vLLM data becomes a run-level time series; per-case cache/GPU
attribution still requires serialized workloads or request correlation. Raw
attention/KV tensors and hidden reasoning are not retained. MoE routing is
only an applicable metric in a separately declared MoE model panel.
