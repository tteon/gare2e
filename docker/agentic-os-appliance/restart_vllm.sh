#!/usr/bin/env bash
# Restart the appliance's vLLM with a different serving configuration and wait
# for readiness.  Used by persistent-lab sessions (skill §4b) to sweep serving
# knobs (prefix caching, chunked prefill, CUDA-graph mode, KV dtype,
# speculative decoding) on one instance without re-bootstrapping.
#
# Inputs (env): VLLM_MODEL, VLLM_MODEL_REVISION, VLLM_TENSOR_PARALLEL_SIZE,
# VLLM_EXTRA_ARGS (the configuration under test), VLLM_PORT (default 8000),
# SEOCHO_VLLM_RECEIPT_DIR (where logs + config receipt go).  The base argv
# mirrors docker/agentic-os-appliance/entrypoint.sh; a shell test keeps the
# two lists aligned.
#
# Replica mode (session B, lab 008): VLLM_REPLICAS=N (>1) starts N TP=1
# replicas pinned to CUDA_VISIBLE_DEVICES=0..N-1 on ports VLLM_PORT+1.., and
# vllm_router.py on VLLM_PORT with SEOCHO_ROUTER_POLICY=rr|sticky, so the
# workload keeps one endpoint and /metrics stays a single merged scrape.
# LMCACHE_SHARED=1 additionally starts one lmcache_server (host-memory KV
# store) and attaches every replica through LMCacheConnectorV1, so prefixes
# computed on one replica can be reused by the other (the "P2P KV sharing"
# arm on a homogeneous box).
set -euo pipefail
: "${VLLM_MODEL:?}" "${VLLM_MODEL_REVISION:?}"
: "${VLLM_TENSOR_PARALLEL_SIZE:=2}" "${VLLM_PORT:=8000}"
: "${SEOCHO_VLLM_RECEIPT_DIR:=/artifacts/vllm-restart}"
: "${VLLM_READY_ATTEMPTS:=900}"
mkdir -p "$SEOCHO_VLLM_RECEIPT_DIR"
umask 077

pkill -TERM -f 'vllm_router.py' 2>/dev/null || true
pkill -TERM -f 'lmcache_server|lmcache.v1.server' 2>/dev/null || true
old_pids="$(pgrep -f 'vllm serve' || true)"
if [ -n "$old_pids" ]; then
  kill -TERM $old_pids 2>/dev/null || true
  for _ in $(seq 1 60); do pgrep -f 'vllm serve' >/dev/null 2>&1 || break; sleep 1; done
  pkill -KILL -f 'vllm serve' 2>/dev/null || true
  # EngineCore / worker processes outlive a killed API server on some images.
  pkill -KILL -f 'VLLM::EngineCore|vllm.v1.engine.core|from multiprocessing.spawn' 2>/dev/null || true
  sleep 3
fi

export VLLM_SERVER_DEV_MODE=1
# Same topology-gated NCCL policy as entrypoint.sh (nccl_env.sh).
# shellcheck source=docker/agentic-os-appliance/nccl_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/nccl_env.sh"
seocho_resolve_nccl_env
printf 'NCCL_P2P_DISABLE=%s\nSEOCHO_NCCL_P2P_DECISION=%s\n' "$NCCL_P2P_DISABLE" "$SEOCHO_NCCL_P2P_DECISION" \
  > "$SEOCHO_VLLM_RECEIPT_DIR/nccl-env.private.txt"
vllm_command=(
  vllm serve "$VLLM_MODEL"
  --revision "$VLLM_MODEL_REVISION"
  --tokenizer-revision "$VLLM_MODEL_REVISION"
  --host 127.0.0.1 --port "$VLLM_PORT"
  --served-model-name "$VLLM_MODEL"
  --enable-prefix-caching
  --enable-auto-tool-choice
  --tool-call-parser "${VLLM_TOOL_CALL_PARSER:-qwen3_coder}"
  --reasoning-parser "${VLLM_REASONING_PARSER:-qwen3}"
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE"
  --dtype "${VLLM_DTYPE:-bfloat16}"
  --max-model-len "${VLLM_MAX_MODEL_LEN:-32768}"
  --max-num-seqs "${VLLM_MAX_NUM_SEQS:-4}"
  --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTILIZATION:-0.92}"
  --kv-cache-metrics
  --kv-cache-metrics-sample "${VLLM_KV_CACHE_METRICS_SAMPLE:-1.0}"
  --enable-prompt-tokens-details
)
if [ "${VLLM_LANGUAGE_MODEL_ONLY:-1}" = "1" ]; then
  vllm_command+=(--language-model-only)
fi
if [ -n "${SEOCHO_OTLP_ENDPOINT:-}" ]; then
  # vLLM exports one server span per request, honouring the traceparent header
  # the runners send, so it nests under the client span in the same trace.
  vllm_command+=(--otlp-traces-endpoint "$SEOCHO_OTLP_ENDPOINT")
  export OTEL_SERVICE_NAME="${SEOCHO_VLLM_OTEL_SERVICE_NAME:-vllm}"   # else spans read unknown_service (lab-s8)
  # Per-request stage timings (queue, prefill, decode, model forward/execute)
  # for the vLLM engineer's read; costs a little per request, so only with OTLP.
  if [ "${SEOCHO_VLLM_DETAILED_TRACES:-all}" != "off" ]; then
    vllm_command+=(--collect-detailed-traces "${SEOCHO_VLLM_DETAILED_TRACES:-all}")
  fi
fi
# The pass queue is an env-only channel that cannot quote spaces, so commas
# are accepted as argument separators (JSON values must then avoid commas
# inside tokens — the speculative-config JSON is split and re-joined below).
extra=()
if [ -n "${VLLM_EXTRA_ARGS:-}" ]; then
  IFS=',' read -r -a raw_extra <<<"${VLLM_EXTRA_ARGS//,/,}"
  # re-join JSON fragments: a token starting with '{' is glued with following tokens until one ends with '}'
  buf=""
  for tok in "${raw_extra[@]}"; do
    if [ -n "$buf" ]; then buf="$buf,$tok"; case "$tok" in *\}) extra+=("$buf"); buf="";; esac; continue; fi
    case "$tok" in \{*\}) extra+=("$tok");; \{*) buf="$tok";; *) for w in $tok; do extra+=("$w"); done;; esac
  done
  [ -n "$buf" ] && extra+=("$buf")
fi
# An override of a flag the base argv already carries replaces it (vLLM warns
# on duplicate keys and the receipt should show one value).
if [ "${#extra[@]}" -gt 0 ]; then
  deduped=(); skip=0
  for tok in "${vllm_command[@]}"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    drop=0
    case "$tok" in --*) for e in "${extra[@]}"; do [ "$e" = "$tok" ] && drop=1; done;; esac
    if [ "$drop" = 1 ]; then skip=1; continue; fi
    deduped+=("$tok")
  done
  vllm_command=("${deduped[@]}")
fi
vllm_command+=("${extra[@]}")
if [ "${SEOCHO_ENGINE_OBSERVER:-0}" = "1" ]; then
  export SEOCHO_ENGINE_OBSERVER_DIR="$SEOCHO_VLLM_RECEIPT_DIR/engine-observer"
  export PYTHONPATH="$(dirname "${BASH_SOURCE[0]}"):${PYTHONPATH:-}"
  vllm_command+=(--scheduler-cls observed_scheduler.ObservedAsyncScheduler)
fi
if [ "${SEOCHO_ENGINE_PROFILER:-0}" = "1" ]; then
  vllm_command+=(--profiler-config.profiler torch
    --profiler-config.torch_profiler_dir "$SEOCHO_VLLM_RECEIPT_DIR/profiles.private"
    --profiler-config.torch_profiler_with_stack false
    --profiler-config.ignore_frontend true
    --profiler-config.max_iterations 32)
fi
# Experimental CPU-affinity axis; scope only this server and its descendants.
export SEOCHO_VLLM_CPU_LIST=""
if [ -n "${SEOCHO_VLLM_CPU_COUNT:-}" ]; then
  SEOCHO_VLLM_CPU_LIST="$(python3 -c 'import os,sys; n=int(sys.argv[1]); allowed=sorted(os.sched_getaffinity(0)); assert 0<n<=len(allowed); print(",".join(map(str,allowed[:n])))' "$SEOCHO_VLLM_CPU_COUNT")"
  vllm_command=(taskset -c "$SEOCHO_VLLM_CPU_LIST" "${vllm_command[@]}")
fi
: "${VLLM_REPLICAS:=1}" "${SEOCHO_ROUTER_POLICY:=sticky}" "${LMCACHE_SHARED:=0}" "${LMCACHE_PORT:=65432}"
started_s="$(date +%s.%N)"
wait_ready() { # url attempts -> 0 when /v1/models and /metrics answer
  local url="$1" attempts="$2" pid="$3"
  for _ in $(seq 1 "$attempts"); do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then echo "process $pid exited before readiness ($url)" >&2; return 3; fi
    if curl --fail --silent "$url/v1/models" >/dev/null 2>&1 && curl --fail --silent "$url/metrics" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "not ready: $url" >&2; return 3
}
write_receipt() { # pid
  python3 - "$SEOCHO_VLLM_RECEIPT_DIR" "$started_s" "$(date +%s.%N)" "$1" "${VLLM_EXTRA_ARGS:-}" "$VLLM_REPLICAS" "$SEOCHO_ROUTER_POLICY" "$LMCACHE_SHARED" <<'PY'
import hashlib, json, os, sys
out, started, ready, pid, extra, replicas, policy, lmc = sys.argv[1:9]
json.dump({
    "schema_version": "seocho.agentic_os.vllm_restart_receipt.v2",
    "vllm_pid": int(pid),
    "extra_args": extra.split(),
    "extra_args_sha256": hashlib.sha256(extra.encode()).hexdigest(),
    "ready_after_s": round(float(ready) - float(started), 3),
    "replicas": int(replicas),
    "router_policy": policy if int(replicas) > 1 else None,
    "lmcache_shared": lmc == "1",
    "cpu_affinity": os.environ.get("SEOCHO_VLLM_CPU_LIST") or None,
}, open(f"{out}/vllm-restart-receipt.json", "w"), indent=2, sort_keys=True)
PY
}

if [ "$VLLM_REPLICAS" -le 1 ]; then
  printf '%s\n' "${vllm_command[@]}" > "$SEOCHO_VLLM_RECEIPT_DIR/vllm-effective-argv.private.txt"
  nohup "${vllm_command[@]}" > "$SEOCHO_VLLM_RECEIPT_DIR/vllm.stdout.private.log" 2> "$SEOCHO_VLLM_RECEIPT_DIR/vllm.stderr.private.log" &
  vllm_pid=$!
  wait_ready "http://127.0.0.1:${VLLM_PORT}" "$VLLM_READY_ATTEMPTS" "$vllm_pid" || { echo "vllm did not become ready" >&2; exit 3; }
  write_receipt "$vllm_pid"
  echo "$vllm_pid"
  exit 0
fi

# --- replica mode -----------------------------------------------------------
lmcache_args=()
if [ "$LMCACHE_SHARED" = "1" ]; then
  cat > "$SEOCHO_VLLM_RECEIPT_DIR/lmcache.yaml" <<EOF2
chunk_size: ${LMCACHE_CHUNK_SIZE:-256}
local_cpu: true
max_local_cpu_size: ${LMCACHE_LOCAL_CPU_GB:-5}
remote_url: "lm://127.0.0.1:${LMCACHE_PORT}"
remote_serde: "naive"
EOF2
  if command -v lmcache_server >/dev/null 2>&1; then
    nohup lmcache_server 127.0.0.1 "$LMCACHE_PORT" > "$SEOCHO_VLLM_RECEIPT_DIR/lmcache-server.stdout.private.log" 2> "$SEOCHO_VLLM_RECEIPT_DIR/lmcache-server.stderr.private.log" &
  else
    nohup python3 -m lmcache.v1.server 127.0.0.1 "$LMCACHE_PORT" > "$SEOCHO_VLLM_RECEIPT_DIR/lmcache-server.stdout.private.log" 2> "$SEOCHO_VLLM_RECEIPT_DIR/lmcache-server.stderr.private.log" &
  fi
  lmcache_pid=$!
  sleep 3
  kill -0 "$lmcache_pid" 2>/dev/null || { echo "lmcache_server exited at start" >&2; exit 3; }
  export LMCACHE_CONFIG_FILE="$SEOCHO_VLLM_RECEIPT_DIR/lmcache.yaml"
  lmcache_args=(--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}')
fi
replica_pids=()
backends=()
for i in $(seq 0 $((VLLM_REPLICAS-1))); do
  port=$((VLLM_PORT+1+i))
  cmd=()
  skip=0
  for tok in "${vllm_command[@]}"; do            # rewrite --port and --tensor-parallel-size per replica
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case "$tok" in
      --port) cmd+=(--port "$port"); skip=1;;
      --tensor-parallel-size) cmd+=(--tensor-parallel-size 1); skip=1;;
      *) cmd+=("$tok");;
    esac
  done
  cmd+=("${lmcache_args[@]}")
  printf '%s\n' "${cmd[@]}" > "$SEOCHO_VLLM_RECEIPT_DIR/vllm-effective-argv.replica${i}.private.txt"
  CUDA_VISIBLE_DEVICES="$i" nohup "${cmd[@]}" > "$SEOCHO_VLLM_RECEIPT_DIR/vllm.replica${i}.stdout.private.log" 2> "$SEOCHO_VLLM_RECEIPT_DIR/vllm.replica${i}.stderr.private.log" &
  replica_pids+=($!)
  backends+=(--backend "http://127.0.0.1:${port}")
done
for i in $(seq 0 $((VLLM_REPLICAS-1))); do
  wait_ready "http://127.0.0.1:$((VLLM_PORT+1+i))" "$VLLM_READY_ATTEMPTS" "${replica_pids[$i]}" || { echo "replica $i did not become ready" >&2; exit 3; }
done
nohup python3 "$(dirname "${BASH_SOURCE[0]}")/vllm_router.py" --host 127.0.0.1 --port "$VLLM_PORT" --policy "$SEOCHO_ROUTER_POLICY" \
  --receipt "$SEOCHO_VLLM_RECEIPT_DIR/router-receipts.jsonl" "${backends[@]}" \
  > "$SEOCHO_VLLM_RECEIPT_DIR/router.stdout.private.log" 2> "$SEOCHO_VLLM_RECEIPT_DIR/router.stderr.private.log" &
router_pid=$!
wait_ready "http://127.0.0.1:${VLLM_PORT}" 60 "$router_pid" || { echo "router did not become ready" >&2; exit 3; }
write_receipt "${replica_pids[0]}"
echo "${replica_pids[0]}"
exit 0
