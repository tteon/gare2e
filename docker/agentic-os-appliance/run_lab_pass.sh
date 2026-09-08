#!/usr/bin/env bash
# One persistent-lab experiment pass over SSH: optional vLLM restart with a
# serving configuration, then the e2e (gate|main) under the co-located workload
# wrapper so GPU/process/vLLM samplers bracket exactly this pass.
#
# Required env: PASS_ID (e.g. pe0-gate), SEOCHO_E2E_PHASE (gate|main|all).
# Optional env: VLLM_EXTRA_ARGS_OVERRIDE (triggers a vLLM restart when set),
# SEOCHO_PROMPT_PROFILE, SEOCHO_GATE_B0_CRITERION, SEOCHO_MAX_OUTPUT_TOKENS,
# SEOCHO_REASONING_MODES, SEOCHO_TOKEN_BUDGET.
set -euo pipefail
: "${PASS_ID:?}" "${SEOCHO_E2E_PHASE:?}"
: "${SEOCHO_REPO_ROOT:=/workspace/seocho}" "${SEOCHO_APPLIANCE_INPUT:=/workspace/input}"
: "${SEOCHO_APPLIANCE_SECRET_FILE:=/workspace/appliance.secret}"
: "${VLLM_MODEL:=Qwen/Qwen3.6-27B}" "${VLLM_MODEL_REVISION:=6a9e13bd6fc8f0983b9b99948120bc37f49c13e9}"
export SEOCHO_APPLIANCE_ARTIFACTS="/artifacts/run-${PASS_ID}"
if [ -e "$SEOCHO_APPLIANCE_ARTIFACTS" ]; then echo "pass dir exists: $SEOCHO_APPLIANCE_ARTIFACTS" >&2; exit 2; fi
mkdir -p "$SEOCHO_APPLIANCE_ARTIFACTS"
export NEO4J_PASSWORD; NEO4J_PASSWORD="$(cat "$SEOCHO_APPLIANCE_SECRET_FILE")"
export PATH="/workspace/seocho-venv/bin:/var/lib/neo4j/bin:/opt/java/openjdk/bin:$PATH"
export JAVA_HOME="${JAVA_HOME:-/opt/java/openjdk}" NEO4J_HOME="${NEO4J_HOME:-/var/lib/neo4j}"
export VLLM_MODEL VLLM_MODEL_REVISION SEOCHO_REPO_ROOT SEOCHO_APPLIANCE_INPUT

if [ -n "${VLLM_EXTRA_ARGS_OVERRIDE:-}" ]; then
  VLLM_EXTRA_ARGS="$VLLM_EXTRA_ARGS_OVERRIDE" SEOCHO_VLLM_RECEIPT_DIR="$SEOCHO_APPLIANCE_ARTIFACTS/vllm-restart" \
    bash "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/restart_vllm.sh" > "$SEOCHO_APPLIANCE_ARTIFACTS/vllm-restart.pid"
fi
vllm_pid="$(pgrep -o -f 'vllm serve' || true)"
dozer_pid="$(pgrep -o -f 'org\.neo4j|neo4j\.server' || true)"
[ -n "$vllm_pid" ] && [ -n "$dozer_pid" ] || { echo "services not running (vllm=$vllm_pid dozer=$dozer_pid)" >&2; exit 3; }
export DOZERDB_PID="$dozer_pid"
python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/run_agentic_os_colocated_workload.py" \
  --vllm-metrics-url "http://127.0.0.1:${VLLM_PORT:-8000}/metrics" \
  --role-pid "vllm=$vllm_pid" --role-pid "dozerdb=$dozer_pid" \
  --interval-s "${SEOCHO_SAMPLE_INTERVAL_S:-1}" --max-gap-s "${SEOCHO_SAMPLE_MAX_GAP_S:-2.5}" \
  --artifacts "$SEOCHO_APPLIANCE_ARTIFACTS/workload" \
  --workload env SEOCHO_E2E_PHASE="$SEOCHO_E2E_PHASE" bash "$SEOCHO_REPO_ROOT/scripts/benchmarks/run_agentic_os_colocated_e2e.sh"
status=$?
printf '{"pass_id":"%s","phase":"%s","prompt_profile":"%s","b0_criterion":"%s","vllm_extra_args_override":"%s","exit_code":%s}\n' \
  "$PASS_ID" "$SEOCHO_E2E_PHASE" "${SEOCHO_PROMPT_PROFILE:-baseline}" "${SEOCHO_GATE_B0_CRITERION:-validated}" "${VLLM_EXTRA_ARGS_OVERRIDE:-}" "$status" \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pass-receipt.json"
exit "$status"
