#!/usr/bin/env bash
set -euo pipefail
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib:${LD_LIBRARY_PATH:-}"
extra=()
# Loopback-only benchmark cache control, matching the existing cache helper.
export VLLM_SERVER_DEV_MODE="${VLLM_SERVER_DEV_MODE:-0}"
if [[ "${GARE2E_SERVER_TRACING:-1}" == 1 ]]; then
  export OTEL_SERVICE_NAME=gare2e-vllm
  extra+=(--kv-cache-metrics --kv-cache-metrics-sample 0.01 --otlp-traces-endpoint http://127.0.0.1:4317 --enable-request-id-headers)
fi
if [[ "${GARE2E_ENGINE_OBSERVER:-1}" == 1 ]]; then
  export SEOCHO_ENGINE_OBSERVER_DIR="${SEOCHO_ENGINE_OBSERVER_DIR:-/artifacts/engine-observer}"
  extra+=(--scheduler-cls observed_scheduler_031.ObservedAsyncScheduler)
fi
exec vllm serve "${MODEL_PATH:-/models/qwen3.8-27b}" \
  --served-model-name Qwen3.8-27B --host "${LISTEN_HOST:-127.0.0.1}" --port 8000 \
  --dtype bfloat16 --tensor-parallel-size 1 --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --max-num-seqs "${MAX_NUM_SEQS:-4}" --gpu-memory-utilization 0.90 \
  --language-model-only --enable-prefix-caching \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 "${extra[@]}"
