#!/usr/bin/env bash
# Execute the frozen raw (SEOCHO-off) graph-agent baseline in one appliance.
#
# The entrypoint has already made vLLM and DozerDB ready when this is invoked.
# This script deliberately never polls /metrics: the enclosing co-located
# wrapper owns one background sampler for the entire run.
set -euo pipefail

: "${SEOCHO_APPLIANCE_INPUT:=/workspace/input}"
: "${SEOCHO_APPLIANCE_ARTIFACTS:=/artifacts}"
: "${SEOCHO_REPO_ROOT:=/opt/seocho}"
: "${VLLM_MODEL:?Set VLLM_MODEL}"
: "${NEO4J_PASSWORD:?Set NEO4J_PASSWORD}"
: "${DOZERDB_PID:?Set DOZERDB_PID to the ready DozerDB JVM PID}"
: "${SEOCHO_MAX_TURNS:=6}"
: "${SEOCHO_MAX_OUTPUT_TOKENS:=2048}"
: "${SEOCHO_TOKEN_BUDGET:=16384}"
: "${SEOCHO_REASONING_MODES:=false true}"
: "${SEOCHO_E2E_PHASE:=all}"
: "${SEOCHO_INTER_ATTEMPT_GAP_S:=1.25}"
: "${SEOCHO_GRBENCH_WORKSPACE_ID:=grbench-maple-physics}"
: "${SEOCHO_GRBENCH_LOAD_TIMEOUT_S:=1800}"
: "${SEOCHO_PROMPT_PROFILE:=baseline}"
: "${SEOCHO_GATE_B0_CRITERION:=validated}"

ROOT="$SEOCHO_APPLIANCE_ARTIFACTS/e2e"
INPUT="$SEOCHO_APPLIANCE_INPUT"
mkdir -p "$ROOT"
failures=0

run() {
  local label="$1"
  shift
  printf '%s\tstart\t%s\n' "$label" "$(date -Is)" >> "$ROOT/progress.tsv"
  if python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/run_agentic_os_schedule.py" \
    "$@" --inter-attempt-gap-s "$SEOCHO_INTER_ATTEMPT_GAP_S" \
    --prompt-profile "$SEOCHO_PROMPT_PROFILE"; then
    printf '%s\tcomplete\t%s\n' "$label" "$(date -Is)" >> "$ROOT/progress.tsv"
  else
    failures=$((failures + 1))
    printf '%s\tfailed\t%s\n' "$label" "$(date -Is)" >> "$ROOT/progress.tsv"
  fi
  sleep "$SEOCHO_INTER_ATTEMPT_GAP_S"
}

gate() {
  python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/preflight_vllm_agentic.py" \
    --base-url http://127.0.0.1:8000 --model "$VLLM_MODEL" \
    --receipt-out "$ROOT/vllm-preflight.json" --metrics-out "$ROOT/vllm-preflight.prom"

  # The graph is loaded once before any timed case.  This receipt is graph-
  # construction evidence and is never folded into agent E2E latency.  When
  # the appliance bulk-imported the exporter CSV before start-up, only verify
  # counts; otherwise the Bolt projection runs under an explicit budget so a
  # slow load fails visibly instead of consuming the paid window (V13).
  if [ -f "$INPUT/grbench-import-manifest.json" ]; then
    python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/load_grbench_dozerdb.py" \
      --verify-only --schema-receipt "$INPUT/grbench-import-manifest.json" \
      --workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" \
      --receipt-out "$ROOT/grbench-load-receipt.json"
  else
    timeout --signal=TERM "$SEOCHO_GRBENCH_LOAD_TIMEOUT_S" \
      python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/load_grbench_dozerdb.py" \
      --graph "$INPUT/grbench-physics-graph.json" \
      --workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" \
      --receipt-out "$ROOT/grbench-load-receipt.json"
  fi

  python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/preflight_agentic_os_services.py" \
    --base-url http://127.0.0.1:8000 --model "$VLLM_MODEL" \
    --grbench-import-receipt "$ROOT/grbench-load-receipt.json" \
    --gtsqa-parquet "$INPUT/gtsqa-with-graphs.parquet" \
    --receipt-out "$ROOT/service-readiness.json"

  for thinking in $SEOCHO_REASONING_MODES; do
    run "gate-grbench-single-tool-$thinking" \
      --lane grbench-single --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/grbench-single-tool-schedule.json" \
      --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/grbench-discovery.json" \
      --run-root "$ROOT/gate-grbench-single-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "gate-grbench-multi-tool-$thinking" \
      --lane grbench-multi --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/grbench-single-tool-schedule.json" \
      --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/grbench-discovery.json" \
      --run-root "$ROOT/gate-grbench-multi-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "gate-gtsqa-single-tool-$thinking" \
      --lane gtsqa-single --schedule "$INPUT/gtsqa-single-tool-schedule.json" \
      --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/gtsqa-discovery.json" \
      --run-root "$ROOT/gate-gtsqa-single-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "gate-gtsqa-multi-tool-$thinking" \
      --lane gtsqa-multi --schedule "$INPUT/gtsqa-multi-tool-schedule.json" \
      --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/gtsqa-discovery.json" \
      --run-root "$ROOT/gate-gtsqa-multi-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"
  done
  python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/validate_agentic_os_one_case_gate.py" \
    --run-root "$ROOT" --receipt-out "$ROOT/one-case-gate.json" \
    --b0-criterion "$SEOCHO_GATE_B0_CRITERION"
}

main_matrix() {
  # Raw SEOCHO-OFF evidence is isolated to B0.  Treatment arms are scheduled
  # later and never pooled into this baseline catalog.
  for thinking in $SEOCHO_REASONING_MODES; do
    run "raw-grbench-single-closed-$thinking" \
      --lane grbench-single --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/grbench-single-tool-schedule.json" \
      --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/grbench-discovery.json" \
      --run-root "$ROOT/raw-grbench-single-closed-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition closed_book \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "raw-gtsqa-single-closed-$thinking" \
      --lane gtsqa-single --schedule "$INPUT/gtsqa-single-tool-schedule.json" \
      --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/gtsqa-discovery.json" \
      --run-root "$ROOT/raw-gtsqa-single-closed-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition closed_book \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "raw-grbench-single-tool-$thinking" \
    --lane grbench-single --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/grbench-single-tool-schedule.json" \
    --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/grbench-discovery.json" \
    --run-root "$ROOT/grbench-single-tool-$thinking" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
    --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "raw-grbench-multi-tool-$thinking" \
    --lane grbench-multi --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/grbench-single-tool-schedule.json" \
    --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/grbench-discovery.json" \
    --run-root "$ROOT/grbench-multi-tool-$thinking" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
    --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "raw-gtsqa-single-tool-$thinking" \
    --lane gtsqa-single --schedule "$INPUT/gtsqa-single-tool-schedule.json" \
    --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/gtsqa-discovery.json" \
    --run-root "$ROOT/gtsqa-single-tool-$thinking" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"

    run "raw-gtsqa-multi-tool-$thinking" \
    --lane gtsqa-multi --schedule "$INPUT/gtsqa-multi-tool-schedule.json" \
    --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/gtsqa-discovery.json" \
    --run-root "$ROOT/gtsqa-multi-tool-$thinking" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh"
  done
}

export DOZERDB_PID
case "$SEOCHO_E2E_PHASE" in
  gate) gate ;;
  main) main_matrix ;;
  all)
    gate
    main_matrix
    ;;
  *)
    printf 'unsupported SEOCHO_E2E_PHASE=%s\n' "$SEOCHO_E2E_PHASE" >&2
    exit 2
    ;;
esac

printf 'e2e\tcomplete\t%s\n' "$(date -Is)" >> "$ROOT/progress.tsv"
exit "$failures"
