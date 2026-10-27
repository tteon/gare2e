#!/usr/bin/env bash
set -euo pipefail
umask 077
B=/opt/gare2e-pilot
N="$B/neo4j-community-5.26.0"
SECRET="${NEO4J_PASSWORD_FILE:-/run/secrets/neo4j_password}"
test -s "$SECRET" || { echo 'Missing nonempty NEO4J_PASSWORD_FILE' >&2; exit 64; }
if [[ "${GARE2E_DB_ONLY:-0}" != 1 ]]; then
  python "$B/source/docker/agentic-os-appliance/observability/vast/export_private_run.py" --preflight --s3-uri "s3://$GARE2E_S3_BUCKET/$GARE2E_S3_PREFIX"
  test -f "${MODEL_PATH:-/models/qwen3.8-27b}/config.json" || { echo 'Model snapshot is not mounted; run download-model.py first' >&2; exit 64; }
fi
mkdir -p /data/neo4j/data /data/neo4j/transactions /artifacts/neo4j
cat > "$N/conf/neo4j.conf" <<EOF
server.default_listen_address=${LISTEN_HOST:-127.0.0.1}
server.directories.data=/data/neo4j/data
server.directories.transaction.logs.root=/data/neo4j/transactions
server.directories.logs=/artifacts/neo4j
server.memory.heap.initial_size=512m
server.memory.heap.max_size=2g
server.memory.pagecache.size=2g
server.bolt.enabled=true
server.http.enabled=true
EOF
# A reused data volume owns its existing credentials; do not reset them.
if [[ ! -d /data/neo4j/data/databases/system && ! -f /data/neo4j/data/dbms/auth.ini ]]; then
  "$N/bin/neo4j-admin" dbms set-initial-password "$(cat "$SECRET")" >/dev/null
fi
if [[ "${GARE2E_DB_ONLY:-0}" == 1 ]]; then
  exec "$N/bin/neo4j" console
fi
export PYTHONPATH="$B/source/src:$B/source/docker/agentic-os-appliance/vast-runtime:${PYTHONPATH:-}"
export SEOCHO_OBSERVABILITY_ENDPOINT="${SEOCHO_OBSERVABILITY_ENDPOINT:-otel-collector:4317}"
export SEOCHO_VLLM_METRICS_TARGET=127.0.0.1:8000
export SEOCHO_OTEL_QUEUE_DIR=/data/observability/otel-queue
mkdir -p "$SEOCHO_OTEL_QUEUE_DIR" /artifacts/observability
export SEOCHO_RESOURCE_ARTIFACTS="/artifacts/observability/resources-$(date -u +%Y%m%dT%H%M%SZ)-$$"
children=()
cleanup() {
  trap - EXIT TERM INT
  for pid in "${children[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${children[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT TERM INT
"$N/bin/neo4j" console & children+=("$!")
otelcol-contrib --config="$B/source/docker/agentic-os-appliance/vast-runtime/collector.yaml" > /artifacts/observability/collector.log 2>&1 & children+=("$!")
"$B/serve.sh" & children+=("$!")
python "$B/source/docker/agentic-os-appliance/vast-runtime/resource_metrics.py" > /artifacts/observability/resource-metrics.log 2>&1 & children+=("$!")
set +e
wait -n "${children[@]}"
status=$?
exit "$status"
