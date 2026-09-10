#!/usr/bin/env bash
# Start a single-host AgenticOS appliance and run the workload supplied after
# the image command.  No remote metrics scrape is performed on a case path.
set -euo pipefail
umask 077

if [ "$#" -eq 0 ]; then
  echo "usage: agentic-os-appliance <workload command...>" >&2
  exit 2
fi

: "${VLLM_MODEL:?Set VLLM_MODEL, for example Qwen/Qwen3.6-27B}"
: "${VLLM_MODEL_REVISION:?Set VLLM_MODEL_REVISION to an immutable model commit}"
: "${NEO4J_PASSWORD:?Set NEO4J_PASSWORD}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
# The upstream DozerDB/Neo4j entrypoint consumes NEO4J_AUTH, whereas the
# benchmark configuration intentionally keeps only the password as an input.
# Materialize the upstream variable locally without printing it.
export NEO4J_AUTH="${NEO4J_AUTH:-$NEO4J_USER/$NEO4J_PASSWORD}"
export JAVA_HOME="${JAVA_HOME:-/opt/java/openjdk}"
export NEO4J_HOME="${NEO4J_HOME:-/var/lib/neo4j}"
export NEO4J_EDITION="${NEO4J_EDITION:-community}"
export PATH="$NEO4J_HOME/bin:$JAVA_HOME/bin:$PATH"
SEOCHO_REPO_ROOT="${SEOCHO_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export SEOCHO_REPO_ROOT
ARTIFACTS="${SEOCHO_APPLIANCE_ARTIFACTS:-/artifacts}"
DOZERDB_HEAP_INITIAL_SIZE="${DOZERDB_HEAP_INITIAL_SIZE:-2g}"
DOZERDB_HEAP_MAX_SIZE="${DOZERDB_HEAP_MAX_SIZE:-2g}"
DOZERDB_PAGECACHE_SIZE="${DOZERDB_PAGECACHE_SIZE:-4g}"
SEOCHO_MIN_HOST_RAM_GIB="${SEOCHO_MIN_HOST_RAM_GIB:-48}"
mkdir -p "$ARTIFACTS" /data /logs "$NEO4J_HOME/plugins" "$NEO4J_HOME/run"

APPLIANCE_PHASE="init"
APPLIANCE_PHASE_LOG="$ARTIFACTS/appliance-phases.private.tsv"
record_phase() {
  APPLIANCE_PHASE="$1"
  printf '%s\tphase\t%s\t0\n' "$(date -Is)" "$APPLIANCE_PHASE" >> "$APPLIANCE_PHASE_LOG"
}
appliance_error() {
  local status=$?
  local line="$1"
  trap - ERR
  printf '%s\terror\t%s\t%s\tline=%s\n' \
    "$(date -Is)" "$APPLIANCE_PHASE" "$status" "$line" >> "$APPLIANCE_PHASE_LOG"
  printf 'appliance failed: phase=%s line=%s exit=%s\n' \
    "$APPLIANCE_PHASE" "$line" "$status" >&2
  exit "$status"
}
trap 'appliance_error "$LINENO"' ERR

appliance_fatal() {
  local status="$1"
  shift
  trap - ERR
  printf '%s\terror\t%s\t%s\texplicit\n' \
    "$(date -Is)" "$APPLIANCE_PHASE" "$status" >> "$APPLIANCE_PHASE_LOG"
  printf '%s\n' "$*" >&2
  exit "$status"
}

record_phase "binary_preflight"
for required_command in \
  curl date nvidia-smi pgrep python3 taskset vllm \
  neo4j neo4j-admin cypher-shell java; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    appliance_fatal 127 "missing appliance command: $required_command"
  fi
done

record_phase "host_memory_admission"
host_mem_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
required_mem_kib="$((SEOCHO_MIN_HOST_RAM_GIB * 1024 * 1024))"
if [ -z "$host_mem_kib" ] || [ "$host_mem_kib" -lt "$required_mem_kib" ]; then
  appliance_fatal 2 \
    "host memory admission failed: require at least ${SEOCHO_MIN_HOST_RAM_GIB} GiB"
fi
{
  printf 'python3\t%s\n' "$(python3 --version 2>&1)"
  printf 'vllm\t%s\n' "$(python3 -c 'import importlib.metadata as m; print(m.version("vllm"))')"
  printf 'neo4j\t%s\n' "$(neo4j version 2>&1)"
  printf 'cypher-shell\t%s\n' "$(cypher-shell --version 2>&1)"
  printf 'java\t%s\n' "$(java -version 2>&1 | head -n 1)"
  printf 'host_mem_kib\t%s\n' "$host_mem_kib"
} > "$ARTIFACTS/runtime-versions.private.tsv"

service_pids=()
cleanup() {
  status=$?
  for pid in "${service_pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in $(seq 1 15); do
    alive=0
    for pid in "${service_pids[@]:-}"; do
      if kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 1
  done
  for pid in "${service_pids[@]:-}"; do
    kill -9 "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  if [ -d /logs ]; then
    tar -czf "$ARTIFACTS/dozerdb-logs.private.tar.gz" -C /logs . 2>/dev/null || true
    chmod 0600 "$ARTIFACTS/dozerdb-logs.private.tar.gz" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

run_with_optional_cpuset() {
  local cpuset="$1"
  shift
  if [ -n "$cpuset" ]; then
    taskset -c "$cpuset" "$@"
  else
    "$@"
  fi
}

record_phase "dozer_permissions"
chown -R 0:0 "$NEO4J_HOME/conf" "$NEO4J_HOME/plugins" "$NEO4J_HOME/run" /data /logs
chmod 0750 "$NEO4J_HOME/conf" "$NEO4J_HOME/plugins" "$NEO4J_HOME/run" /data /logs
find "$NEO4J_HOME/conf" -type f -exec chmod 0600 {} +

record_phase "dozer_memory_config"
cat >> "$NEO4J_HOME/conf/neo4j.conf" <<EOF

# Frozen SEOCHO AgenticOS appliance resource envelope.
server.memory.heap.initial_size=$DOZERDB_HEAP_INITIAL_SIZE
server.memory.heap.max_size=$DOZERDB_HEAP_MAX_SIZE
server.memory.pagecache.size=$DOZERDB_PAGECACHE_SIZE
EOF
neo4j-admin server validate-config \
  > "$ARTIFACTS/dozerdb-config-validation.private.log" 2>&1

# Optional offline bulk import.  The run-time Bolt projection of MAPLE/Physics
# (67.3M relationships) exceeded the paid remote budget (incident V13), so the
# graph is exported once locally and imported here before DozerDB starts.  The
# receipt is content-free: wall time, file digests, row counts.
if [ -n "${SEOCHO_GRBENCH_IMPORT_DIR:-}" ]; then
  record_phase "dozer_bulk_import"
  import_nodes="$SEOCHO_GRBENCH_IMPORT_DIR/grbench-nodes.csv.gz"
  import_rels="$SEOCHO_GRBENCH_IMPORT_DIR/grbench-rels.csv.gz"
  import_manifest="$SEOCHO_GRBENCH_IMPORT_DIR/grbench-import-manifest.json"
  for required_import in "$import_nodes" "$import_rels" "$import_manifest"; do
    if [ ! -f "$required_import" ]; then
      appliance_fatal 2 "bulk import input missing: $required_import"
    fi
  done
  import_started_s="$(date +%s.%N)"
  neo4j-admin database import full neo4j \
    --overwrite-destination=true \
    --multiline-fields=true \
    --nodes="$import_nodes" \
    --relationships="$import_rels" \
    --report-file="$ARTIFACTS/dozerdb-bulk-import-report.private.txt" \
    > "$ARTIFACTS/dozerdb-bulk-import.private.log" 2>&1
  import_finished_s="$(date +%s.%N)"
  mkdir -p "$ARTIFACTS/e2e"
  python3 - "$import_manifest" "$import_started_s" "$import_finished_s" \
    "$ARTIFACTS/e2e/grbench-bulk-import-receipt.json" <<'PY'
import hashlib, json, sys
manifest_path, started, finished, out = sys.argv[1:5]
manifest = json.load(open(manifest_path, encoding="utf-8"))
json.dump(
    {
        "schema_version": "seocho.grbench_bulk_import_receipt.v1",
        "surface": "neo4j_admin_bulk_import",
        "manifest_sha256": hashlib.sha256(open(manifest_path, "rb").read()).hexdigest(),
        "workspace_id": manifest.get("workspace_id"),
        "files": manifest.get("files"),
        "wall_s": round(float(finished) - float(started), 3),
    },
    open(out, "w", encoding="utf-8"),
    indent=2,
    sort_keys=True,
)
PY
fi

record_phase "dozer_initial_password"
if [ ! -f /data/dbms/auth.ini ]; then
  neo4j-admin dbms set-initial-password \
    --require-password-change=false "$NEO4J_PASSWORD" >/dev/null
fi

# Persistent-lab sessions (skill §4b) re-enter the appliance over SSH after
# this entrypoint has started the services; they need the appliance-only
# DozerDB secret without it ever appearing in a receipt or shell history.
record_phase "appliance_secret_file"
SEOCHO_APPLIANCE_SECRET_FILE="${SEOCHO_APPLIANCE_SECRET_FILE:-/workspace/appliance.secret}"
( umask 077; printf '%s' "$NEO4J_PASSWORD" > "$SEOCHO_APPLIANCE_SECRET_FILE" )
chmod 0600 "$SEOCHO_APPLIANCE_SECRET_FILE"

record_phase "service_start"
run_with_optional_cpuset "${DOZERDB_CPUSET:-}" neo4j console \
  >"$ARTIFACTS/dozerdb.stdout.private.log" 2>"$ARTIFACTS/dozerdb.stderr.private.log" &
dozer_launcher_pid=$!
service_pids+=("$dozer_launcher_pid")

# vLLM exposes the benchmark-only prefix reset endpoint in development mode.
# It remains bound to loopback and is used only between serialized cases.
export VLLM_SERVER_DEV_MODE=1
vllm_command=(
  vllm serve "$VLLM_MODEL"
  --revision "$VLLM_MODEL_REVISION"
  --tokenizer-revision "$VLLM_MODEL_REVISION"
  --host 127.0.0.1 --port "${VLLM_PORT:-8000}"
  --served-model-name "$VLLM_MODEL"
  --enable-prefix-caching
  --enable-auto-tool-choice
  --tool-call-parser "${VLLM_TOOL_CALL_PARSER:-qwen3_coder}"
  --reasoning-parser "${VLLM_REASONING_PARSER:-qwen3}"
  --tensor-parallel-size "${VLLM_TENSOR_PARALLEL_SIZE:-1}"
  --dtype "${VLLM_DTYPE:-bfloat16}"
  --max-model-len "${VLLM_MAX_MODEL_LEN:-32768}"
  --max-num-seqs "${VLLM_MAX_NUM_SEQS:-4}"
  --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.92}"
  --kv-cache-metrics
  --kv-cache-metrics-sample "${VLLM_KV_CACHE_METRICS_SAMPLE:-1.0}"
)
if [ "${VLLM_LANGUAGE_MODEL_ONLY:-1}" = "1" ]; then
  vllm_command+=(--language-model-only)
fi
if [ -n "${SEOCHO_OTLP_ENDPOINT:-}" ]; then
  # vLLM exports one server span per request, honouring the traceparent header
  # the runners send, so it nests under the client span in the same trace.
  vllm_command+=(--otlp-traces-endpoint "$SEOCHO_OTLP_ENDPOINT")
fi
if [ -n "${VLLM_EXTRA_ARGS:-}" ]; then
  # Operator-provided flags are intentionally split only from this explicit
  # environment value; workload argv itself is always passed without eval.
  read -r -a extra_args <<<"$VLLM_EXTRA_ARGS"
  vllm_command+=("${extra_args[@]}")
fi
printf '%s\n' "${vllm_command[@]}" > "$ARTIFACTS/vllm-effective-argv.private.txt"
# NCCL settings are benchmark configuration resolved from GPU topology, not
# tuning: V14 hung forever right after "vLLM is using nccl" on a 2x A100 PCIe
# host whose GPU P2P path was unusable, while the readiness loop waited 40 paid
# minutes; lab-s4 showed that freezing NCCL_P2P_DISABLE=1 on an NVLink host
# makes TP=2 skip NVLink.  nccl_env.sh enables P2P only when every GPU pair is
# an NV* link (SEOCHO_NCCL_P2P_MODE=auto|disable|enable; explicit
# NCCL_P2P_DISABLE wins), and WARN-level NCCL logging leaves evidence when it
# still fails.
# shellcheck source=docker/agentic-os-appliance/nccl_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/nccl_env.sh"
seocho_resolve_nccl_env
{
  printf 'NCCL_DEBUG=%s\nNCCL_P2P_DISABLE=%s\nSEOCHO_NCCL_P2P_MODE=%s\nSEOCHO_NCCL_P2P_DECISION=%s\n' \
    "$NCCL_DEBUG" "$NCCL_P2P_DISABLE" "${SEOCHO_NCCL_P2P_MODE:-auto}" "$SEOCHO_NCCL_P2P_DECISION"
  printf -- '--- /dev/shm\n'; df -h /dev/shm 2>&1 || true
  printf -- '--- nvidia-smi topo -m\n'; nvidia-smi topo -m 2>&1 || true
} > "$ARTIFACTS/gpu-topology.private.txt" 2>&1 || true
if [ "${SEOCHO_OTEL_FILE_RECEIVER:-0}" = "1" ] && [ -n "${SEOCHO_OTLP_ENDPOINT:-}" ]; then
  # In-instance OTLP sink (lab 010): vLLM and the receipt exporter push to
  # 127.0.0.1:4317 and the spans leave with the artifacts.
  record_phase "otlp_file_receiver"
  mkdir -p "$ARTIFACTS/otel"
  nohup python3 "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/otlp_file_receiver.py" \
    --listen "${SEOCHO_OTLP_ENDPOINT#http://}" --output "$ARTIFACTS/otel/collector-spans.jsonl" \
    > "$ARTIFACTS/otel/receiver.stdout.private.log" 2> "$ARTIFACTS/otel/receiver.stderr.private.log" &
  service_pids+=($!)
  sleep 2
fi
run_with_optional_cpuset "${VLLM_CPUSET:-}" "${vllm_command[@]}" \
  >"$ARTIFACTS/vllm.stdout.private.log" 2>"$ARTIFACTS/vllm.stderr.private.log" &
vllm_pid=$!
service_pids+=("$vllm_pid")

wait_for_vllm() {
  local last_error="$ARTIFACTS/vllm-readiness-last-error.private.log"
  # Startup stall detector.  A hang (V14: NCCL init) leaves stdout, the
  # Hugging Face cache and GPU memory all frozen; a healthy start-up moves at
  # least one of them.  The weight download alone is silent on stdout for
  # 13+ minutes (V13: 803 s, V15 false positive), so cache and GPU growth
  # count as progress and the default limit is 900 s.
  local stall_limit="${VLLM_STALL_TIMEOUT_S:-900}"
  local hf_cache="${HF_HOME:-${HOME:-/root}/.cache/huggingface}"
  local last_progress="" last_growth_s
  last_growth_s="$(date +%s)"
  vllm_progress_signature() {
    local log_size cache_size gpu_mem
    # The EngineCore prints a "No available shared memory broadcast block"
    # heartbeat every 60 s while idle; lab-s1 hung for 14+ minutes after CUDA
    # graph capture with only that line growing, so it must not count as progress.
    log_size="$(grep -v 'shm_broadcast' "$ARTIFACTS/vllm.stdout.private.log" 2>/dev/null | wc -c || echo 0)"
    cache_size="$(du -sb "$hf_cache" 2>/dev/null | cut -f1 || echo 0)"
    gpu_mem="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | tr '\n' ',' || echo na)"
    printf '%s|%s|%s' "$log_size" "$cache_size" "$gpu_mem"
  }
  for _ in $(seq 1 "${VLLM_READY_ATTEMPTS:-1200}"); do
    if ! kill -0 "$vllm_pid" 2>/dev/null; then
      tail -100 "$ARTIFACTS/vllm.stderr.private.log" > "$last_error" 2>/dev/null || true
      appliance_fatal 3 "vLLM exited before readiness"
    fi
    local progress
    progress="$(vllm_progress_signature)"
    if [ "$progress" != "$last_progress" ]; then
      last_progress="$progress"
      last_growth_s="$(date +%s)"
    elif [ $(( $(date +%s) - last_growth_s )) -ge "$stall_limit" ]; then
      {
        printf 'progress signature (log_bytes|hf_cache_bytes|gpu_mem_mib): %s\n' "$progress"
        tail -40 "$ARTIFACTS/vllm.stdout.private.log"
      } > "$last_error" 2>/dev/null || true
      appliance_fatal 3 "vLLM startup stalled: no log, cache, or GPU-memory progress for ${stall_limit}s before readiness"
    fi
    if curl --fail --silent --show-error \
      "http://127.0.0.1:${VLLM_PORT:-8000}/v1/models" >/dev/null 2>"$last_error" \
      && curl --fail --silent --show-error \
      "http://127.0.0.1:${VLLM_PORT:-8000}/metrics" >/dev/null 2>>"$last_error"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_dozer() {
  local last_error="$ARTIFACTS/dozerdb-readiness-last-error.private.log"
  for _ in $(seq 1 "${DOZERDB_READY_ATTEMPTS:-90}"); do
    if ! kill -0 "$dozer_launcher_pid" 2>/dev/null; then
      tail -100 "$ARTIFACTS/dozerdb.stderr.private.log" > "$last_error" 2>/dev/null || true
      appliance_fatal 3 "DozerDB exited before readiness"
    fi
    if cypher-shell -a "bolt://127.0.0.1:${NEO4J_BOLT_PORT:-7687}" \
      -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" "RETURN 1 AS ready" \
      >/dev/null 2>"$last_error"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_vllm
record_phase "vllm_ready"
wait_for_dozer
record_phase "dozer_ready"

record_phase "dozer_effective_config"
cypher-shell -a "bolt://127.0.0.1:${NEO4J_BOLT_PORT:-7687}" \
  -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" --format plain \
  "SHOW DATABASES YIELD name,currentStatus WHERE name = 'neo4j' RETURN name,currentStatus" \
  > "$ARTIFACTS/dozerdb-databases.private.tsv"
if ! grep -Fqx '"neo4j", "online"' "$ARTIFACTS/dozerdb-databases.private.tsv"; then
  appliance_fatal 3 "DozerDB neo4j database is not ONLINE"
fi
cypher-shell -a "bolt://127.0.0.1:${NEO4J_BOLT_PORT:-7687}" \
  -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" --format plain \
  "SHOW SETTINGS YIELD name,value WHERE name STARTS WITH 'server.memory' OR name STARTS WITH 'server.directories' RETURN name,value ORDER BY name" \
  > "$ARTIFACTS/dozerdb-settings.private.tsv"
for required_setting in \
  server.memory.heap.initial_size \
  server.memory.heap.max_size \
  server.memory.pagecache.size; do
  if ! grep -Fq "\"$required_setting\"" "$ARTIFACTS/dozerdb-settings.private.tsv"; then
    appliance_fatal 3 "DozerDB effective setting missing: $required_setting"
  fi
done
{
  printf 'server.memory.heap.initial_size\t%s\n' "$DOZERDB_HEAP_INITIAL_SIZE"
  printf 'server.memory.heap.max_size\t%s\n' "$DOZERDB_HEAP_MAX_SIZE"
  printf 'server.memory.pagecache.size\t%s\n' "$DOZERDB_PAGECACHE_SIZE"
} > "$ARTIFACTS/dozerdb-settings-expected.private.tsv"

# DozerDB's upstream entrypoint is a shell/tini launcher.  Attribute the JVM,
# not its tiny init process, while retaining the launcher log in artifacts.
dozer_java_pid="$(pgrep -o -f 'org\.neo4j|neo4j\.server' || true)"
if [ -z "$dozer_java_pid" ]; then
  appliance_fatal 3 "DozerDB Java PID was not discoverable after readiness"
fi
export DOZERDB_PID="$dozer_java_pid"

wrapper=(
  python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/run_agentic_os_colocated_workload.py"
  --vllm-metrics-url "http://127.0.0.1:${VLLM_PORT:-8000}/metrics"
  --role-pid "vllm=$vllm_pid"
  --role-pid "dozerdb=$dozer_java_pid"
  --interval-s "${SEOCHO_SAMPLE_INTERVAL_S:-1}"
  --max-gap-s "${SEOCHO_SAMPLE_MAX_GAP_S:-2.5}"
)
if [ "${SEOCHO_STRICT_TELEMETRY:-0}" = "1" ]; then
  wrapper+=(--strict-telemetry)   # opt back into "telemetry gap == pass failure"
fi
if [ "${SEOCHO_DISABLE_GPU_SAMPLER:-0}" = "1" ]; then
  wrapper+=(--no-gpu)
fi
run_colocated_phase() {
  local phase="$1"
  local metrics_root="$2"
  shift 2
  "${wrapper[@]}" --artifacts "$metrics_root" --workload "$@"
}

join_phase() {
  local prefix="$1"
  local metrics_root="$2"
  local output="$3"
  local run_roots=("$ARTIFACTS/e2e"/"$prefix"*)
  if [ ! -d "${run_roots[0]}" ]; then
    appliance_fatal 2 "no case roots found for telemetry join prefix: $prefix"
  fi
  python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/join_agentic_os_gpu_samples.py" \
    "${run_roots[@]}" \
    --vllm-samples "$metrics_root/vllm-metrics.jsonl" \
    --gpu-samples "$metrics_root/gpu.csv" \
    --max-gap-s "${SEOCHO_SAMPLE_MAX_GAP_S:-2.5}" \
    --output "$output"
}

accept_phase() {
  local stage="$1"
  local metrics_root="$2"
  local telemetry_join="$3"
  local output="$4"
  python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/validate_agentic_os_e2e_acceptance.py" \
    --stage "$stage" \
    --service-readiness "$ARTIFACTS/e2e/service-readiness.json" \
    --vllm-preflight "$ARTIFACTS/e2e/vllm-preflight.json" \
    --one-case-gate "$ARTIFACTS/e2e/one-case-gate.json" \
    --workload-receipt "$metrics_root/colocated-workload-receipt.json" \
    --telemetry-join "$telemetry_join" \
    --receipt-out "$output"
}

if [ "${SEOCHO_SPLIT_GATE_MAIN:-1}" = "1" ]; then
  record_phase "one_case_gate"
  run_colocated_phase gate "$ARTIFACTS/gate-workload" \
    env SEOCHO_E2E_PHASE=gate "$@"
  record_phase "one_case_telemetry_join"
  join_phase gate- "$ARTIFACTS/gate-workload" "$ARTIFACTS/e2e/gate-case-telemetry.json"
  record_phase "pre_main_acceptance"
  accept_phase pre_main_matrix "$ARTIFACTS/gate-workload" \
    "$ARTIFACTS/e2e/gate-case-telemetry.json" "$ARTIFACTS/e2e/pre-main-acceptance.json"

  record_phase "raw_main_matrix"
  run_colocated_phase main "$ARTIFACTS/workload" \
    env SEOCHO_E2E_PHASE=main "$@"
  record_phase "raw_main_telemetry_join"
  join_phase raw- "$ARTIFACTS/workload" "$ARTIFACTS/e2e/raw-case-telemetry.json"
  record_phase "final_raw_acceptance"
  accept_phase final_raw_matrix "$ARTIFACTS/workload" \
    "$ARTIFACTS/e2e/raw-case-telemetry.json" "$ARTIFACTS/e2e/final-raw-acceptance.json"
else
  record_phase "workload"
  run_colocated_phase all "$ARTIFACTS/workload" "$@"
fi
record_phase "workload_complete"
