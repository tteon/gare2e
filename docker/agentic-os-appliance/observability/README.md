# Local observability companion

Vast GPU appliance의 원격 OTLP와 workload 대시보드가 필요하면
[중앙 관측용 Compose](vast/README.md)를 사용한다. 이 문서의 Linux host-network
registry companion과는 별도 project이며, 기존 설정은 그대로 유지한다.

Reuses the gare2e platform with a Linux host-network, loopback-only Collector,
Prometheus, Tempo and Grafana. Versions are pinned for reproducibility, not a
claim that these are the newest releases. The stack is optional. LangSmith is
an operator-configured external evaluation/OTel destination, not a dependency.

Start the existing platform on port 18775. Create a private data directory with
`tempo`, `logs`, `prometheus`, `grafana` subdirectories owned by the current user.
Set `GARE2E_OBSERVER_UID`, `GARE2E_OBSERVER_GID`, `GARE2E_OBSERVER_DATA`, and a
secret `GARE2E_GRAFANA_PASSWORD` in a mode-0600 environment file. Then run:

```bash
docker compose --env-file "$OBSERVER_ENV_FILE" -p gare2e-observer \
  -f docker/agentic-os-appliance/observability/compose.yaml up -d
```

Endpoints: Grafana `http://127.0.0.1:13000`, Prometheus `:19090`, Tempo `:13200`,
Collector OTLP gRPC `:14317` and HTTP `:14318`. Grafana permits anonymous Viewer
access on loopback. Administration uses the generated secret. No public listener,
SaaS export, or production auth/RBAC is configured by this local companion.

The dashboard displays registry inventory, not workload throughput. Service
metrics must be collected on the actual experiment host before adding workload
panels. Export existing recorded spans using the harness OTLP exporter to the
Collector, and reviewed OTLP log JSON with HTTP POST `/v1/logs`. Tempo receives
traces; logs are retained separately as bounded rotating JSONL files. There is
no Loki UI in this first companion. Query logs remain searchable in gare2e.

Prometheus scrapes the loopback viewer, Collector and Tempo every five seconds.
If the viewer requires a token, configure `authorization.credentials_file` in
its scrape job and mount that private file; never put a token in a URL or Git.
The registry's high-cardinality IDs are in logs/traces, not metric labels.

For an anonymous loopback viewer, validate actual signal transport and service
readiness with a fresh receipt path:

```bash
python3 scripts/benchmarks/check_graph_observability.py \
  --receipt-out "$OBSERVER_DATA_DIR/integration-receipt.json"
```

This sends one timed viewer request and its correlated span/log through the
Collector, looks up the trace in Tempo, and checks Prometheus/Grafana. It is a
backend integration check; it does not run the graph benchmark. The helper does
not currently support an authenticated viewer; use your authenticated exporter
for that deployment.

Stop the named project with the same compose/env arguments and `down`. Data
remains in the private directory; no remote experiment is affected. The app's
trace/evidence contract is documented in
`docs/agentic-os/GRAPH_OPTIMIZATION_OBSERVABILITY.md`.
