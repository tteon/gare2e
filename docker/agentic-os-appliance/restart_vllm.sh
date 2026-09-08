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
set -euo pipefail
: "${VLLM_MODEL:?}" "${VLLM_MODEL_REVISION:?}"
: "${VLLM_TENSOR_PARALLEL_SIZE:=2}" "${VLLM_PORT:=8000}"
: "${SEOCHO_VLLM_RECEIPT_DIR:=/artifacts/vllm-restart}"
: "${VLLM_READY_ATTEMPTS:=900}"
mkdir -p "$SEOCHO_VLLM_RECEIPT_DIR"
umask 077

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
export NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
export NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}"
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
# shellcheck disable=SC2206
extra=(${VLLM_EXTRA_ARGS:-})
vllm_command+=("${extra[@]}")
printf '%s\n' "${vllm_command[@]}" > "$SEOCHO_VLLM_RECEIPT_DIR/vllm-effective-argv.private.txt"
started_s="$(date +%s.%N)"
nohup "${vllm_command[@]}" > "$SEOCHO_VLLM_RECEIPT_DIR/vllm.stdout.private.log" 2> "$SEOCHO_VLLM_RECEIPT_DIR/vllm.stderr.private.log" &
vllm_pid=$!
for _ in $(seq 1 "$VLLM_READY_ATTEMPTS"); do
  if ! kill -0 "$vllm_pid" 2>/dev/null; then echo "vllm exited before readiness" >&2; exit 3; fi
  if curl --fail --silent "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1 \
     && curl --fail --silent "http://127.0.0.1:${VLLM_PORT}/metrics" >/dev/null 2>&1; then
    ready_s="$(date +%s.%N)"
    python3 - "$SEOCHO_VLLM_RECEIPT_DIR" "$started_s" "$ready_s" "$vllm_pid" "${VLLM_EXTRA_ARGS:-}" <<'PY'
import hashlib, json, sys
out, started, ready, pid, extra = sys.argv[1:6]
json.dump({
    "schema_version": "seocho.agentic_os.vllm_restart_receipt.v1",
    "vllm_pid": int(pid),
    "extra_args": extra.split(),
    "extra_args_sha256": hashlib.sha256(extra.encode()).hexdigest(),
    "ready_after_s": round(float(ready) - float(started), 3),
}, open(f"{out}/vllm-restart-receipt.json", "w"), indent=2, sort_keys=True)
PY
    echo "$vllm_pid"
    exit 0
  fi
  sleep 2
done
echo "vllm did not become ready" >&2
exit 3
