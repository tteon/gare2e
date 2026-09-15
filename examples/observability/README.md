# Local observability stack (ADR-0144)

A lightweight, local OpenTelemetry stack for SEOCHO traces. SEOCHO exports OTLP
to an operator-selected backend; this stack is the supported local development
profile.

Four containers:

| Service | Image | Host port | Role |
|---|---|---|---|
| `otel-collector` | `otel/opentelemetry-collector-contrib:0.119.0` | 4317 / 4318 / 8889 | receives OTLP, fans out |
| `tempo` | `grafana/tempo:2.7.2` | 3200 | trace store |
| `prometheus` | `prom/prometheus:v2.55.1` | 9091 | metrics store |
| `grafana` | `grafana/grafana:11.5.1` | 3000 | dashboards / Explore |

Flow: `SEOCHO → OTLP gRPC → Collector → Tempo (traces) + Prometheus (metrics) → Grafana`.

> Pattern borrowed from [LMCache's `examples/observability`](https://github.com/LMCache/LMCache/tree/dev/examples/observability),
> **not** a copy — SEOCHO services push to the collector by container name, so no
> `host.docker.internal` is needed. LMCache's KV-cache metrics don't apply here
> (SEOCHO calls external LLM APIs; no local vLLM+LMCache serving).

## Run

This stack is a first-class **`observability` profile** of the repo's root
`compose.yaml` (pulled in via `include:`), so it shares the core stack's
`seocho-net` network and project. It is OFF by default — nothing here starts on a
plain `docker compose up`. Start it either way:

```bash
make observability-up                          # the four services only
# or, directly, from the repo root:
docker compose --profile observability up -d tempo prometheus otel-collector grafana
```

Stopping removes only the four observability containers (the core stack and the
named data volumes are left alone):

```bash
make observability-down
```

The definition and dashboards still live in this directory; only the entry point
moved to the root compose. Bringing the core stack up first is no longer required
— the profile creates/attaches `seocho-net` on its own.

Point SEOCHO at the collector:

```bash
# app running inside the compose network:
export SEOCHO_TRACE_BACKEND=otlp
export SEOCHO_TRACE_OTLP_ENDPOINT=http://otel-collector:4317
# app running on the host: the default http://localhost:4317 already works
export SEOCHO_TRACE_BACKEND=otlp
```

Install the exporter deps the backend needs: `pip install 'seocho[otel]'`.

Open Grafana at <http://localhost:3000> (anonymous admin) → **Explore → Tempo**:

```
{ name = "rag.ask" }
{ name = "db.query" && span.db.duration_hydrate_ms > 50 }   # pure-python codec fallback
{ span.workspace_id = "<workspace>" }
```

## Caveats (dev-grade)

- Anonymous-admin Grafana, login disabled, local-disk storage, 7-day retention.
  Fine for local dev; **harden (auth + durable backends) before any shared/prod
  use.** Image tags are pinned — bump deliberately.
- Full prompt/Cypher bodies are only captured with `SEOCHO_TRACE_CAPTURE_CONTENT=1`
  (off by default). Attributes (ids, hashes, counts, timings) always flow.
- Port `9091` avoids common host Prometheus ports.

## Live acceptance

Running containers are not sufficient evidence. A validated profile must show:

1. Prometheus reports the Collector target as `up`.
2. Grafana provisions both Tempo and Prometheus datasources.
3. Tempo returns the expected root workflow span.
4. Required retrieval, context, model, and governance children are present.

Use stable container names (`seocho-tempo`, `seocho-otel-collector`, and
`seocho-prometheus`) when launching an ad-hoc profile outside this compose
file; Docker resolves actual names unless explicit aliases are configured.

## Critical-scenario dashboard

The compose profile mounts `grafana/provisioning/` and automatically exposes
the seven production/evaluation dashboards in the SEOCHO folder. The
**SEOCHO Evaluation** dashboard retains
`uid=seocho-critical-agent-memory` for compatibility. It shows:

- critical-scenario pass ratio and current support state;
- authoritative PostgreSQL sequence versus DozerDB projection watermark;
- silent-stale answers and ontology disclosure violations;
- per-scenario, per-stage p95 latency;
- a link to the matching `seocho-agent-memory` Tempo traces.

Only bounded identifiers such as `scenario_id`, `stage`, and `support_status`
are metric labels. Prompts, query text, wallet identifiers, and transaction
payloads must remain out of metrics and trace attributes.

## Dependency profile

The dependency overlay enables PostgreSQL 18 server metrics through the
official `postgres_exporter` v0.19.1 image and scrapes etcd's native `/metrics`
endpoint. Create a local secret file without committing it:

```bash
mkdir -p examples/observability/secrets
printf '%s' "$POSTGRES_PASSWORD" > \
  examples/observability/secrets/postgres_exporter_password
chmod 600 examples/observability/secrets/postgres_exporter_password

docker compose \
  -f examples/observability/docker-compose.observability.yml \
  -f examples/observability/docker-compose.dependencies.yml \
  --profile observability --profile dependencies up -d
```

The validated local defaults expect PostgreSQL on host port `55432` and etcd
on `52379`; PostgreSQL host/port/database/user/SSL mode are configurable with
the `POSTGRES_EXPORTER_*` variables. Production must replace the etcd static
target with member service discovery.

DozerDB Community 5.26.3.0 did not expose a supported native Prometheus or
dynamic TLS-reload endpoint in the live capability probe. SEOCHO therefore
uses standard `db.client.operation.duration` at the application boundary and
reports native server/cluster/TLS signals as unsupported. Enable those scrapes
only after verifying the exact deployed edition and authenticated endpoint.

## AgenticOS experiment traces (receipts → OTLP)

The graph-agent e2e (`scripts/benchmarks/run_agentic_os_colocated_e2e.sh` and the
Vast appliance) does not trace live: runners write content-free receipts and an
exporter turns them into OpenTelemetry span trees afterwards, so the hot path
stays free of tracing overhead and no prompt, query or answer text can leak.

One benchmark case is one trace. The trace id is derived from `(run_id, case_id)`
(`seocho.eval.agentic_os_trace`); every chat-completion request carries a fresh
`traceparent`, and its span id is stored in the receipt, so spans that vLLM
exports for the same request (`--otlp-traces-endpoint`) nest under the client
span without a shared live tracer.

```
agentic_os.case                     seocho.run_id / case_id / lane / arm / answer_status / retrieval.*
├── chat <model>        CLIENT      gen_ai.operation.name=chat, gen_ai.request.model, gen_ai.usage.input_tokens/output_tokens
├── execute_tool <name> INTERNAL    gen_ai.operation.name=execute_tool, gen_ai.tool.name, seocho.tool.outcome/error_code
│   └── neo4j|oxigraph query CLIENT db.system.name, db.operation.name=query, seocho.query_digest, seocho.db.rows
└── invoke_agent <specialist>       gen_ai.operation.name=invoke_agent, gen_ai.agent.name   (multi-agent only)
```

Export after a run (JSON lines are always safe to keep with the artifacts; the
OTLP push needs `seocho[otel]` and a collector):

```bash
python3 scripts/benchmarks/export_agentic_os_otel.py <run-root>/e2e/*-tool-* \
  --jsonl <run-root>/otel/spans.jsonl \
  --otlp-endpoint http://127.0.0.1:4317 --service-name seocho-agentic-os
```

On the appliance, `run_lab_pass.sh` writes `run-<pass>/otel/spans.jsonl` for every
pass and, when `SEOCHO_OTLP_ENDPOINT` is set, pushes the same spans over OTLP and
starts vLLM with `--otlp-traces-endpoint` so server-side request spans join the
trace. Timestamps are anchored on the coordinator's per-case start; single-agent
GRBench tool spans are placed sequentially (`seocho.timing=reconstructed`).
