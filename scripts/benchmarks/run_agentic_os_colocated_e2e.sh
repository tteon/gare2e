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
# Frozen-input overrides (lab 007 strata cohorts live beside the gate-10 files).
: "${SEOCHO_GRBENCH_SCHEDULE:=grbench-single-tool-schedule.json}"
: "${SEOCHO_GTSQA_SINGLE_SCHEDULE:=gtsqa-single-tool-schedule.json}"
: "${SEOCHO_GTSQA_MULTI_SCHEDULE:=gtsqa-multi-tool-schedule.json}"
: "${SEOCHO_GRBENCH_DISCOVERY:=grbench-discovery.json}"
: "${SEOCHO_GTSQA_DISCOVERY:=gtsqa-discovery.json}"
# Gate strata: empty = one case per cell (gate-10 behaviour); "easy medium hard"
# = one case per stratum per cell, each stratum validated in its own root.
: "${SEOCHO_GATE_STRATA:=}"
# Serving-shape study (lab 008): run K coordinators concurrently over disjoint
# schedule shards; restrict the main matrix to one stratum; skip closed-book lanes.
: "${SEOCHO_CONCURRENCY:=1}"
: "${SEOCHO_MAIN_STRATUM:=}"
: "${SEOCHO_MAIN_SKIP_CLOSED:=0}"
: "${SEOCHO_MAIN_LANES:=grbench-single grbench-multi gtsqa-single gtsqa-multi}"   # lab 017: add grbench-graphcot; drop lanes to skip them (single-agent focus)
: "${SEOCHO_GRAPHCOT_MAX_TURNS:=12}"
: "${SEOCHO_PROFILE_QUERY_PLANS:=0}"   # lab 021: 1 = replay every successful Cypher under PROFILE after timing (grbench-single)   # hop actions need more turns than whole-query lanes (6)
: "${SEOCHO_MAIN_ARMS:=B0}"          # lab 015 schema axis: space/comma list, e.g. "B0 S1 S4"; non-B0 arms get a -<arm> run-root suffix
: "${SEOCHO_GRBENCH_ROW_CAP:=}"    # lab 014 result-size axis; empty = runner default (20 rows)
: "${SEOCHO_GRBENCH_BYTE_CAP:=}"   # empty = runner default (12000 bytes)
# Values may arrive comma-separated from an env-only channel (the pass queue
# cannot carry quoted spaces); normalise to the space-separated form used below.
SEOCHO_GATE_STRATA="${SEOCHO_GATE_STRATA//,/ }"
SEOCHO_MAIN_ARMS="${SEOCHO_MAIN_ARMS//,/ }"
SEOCHO_MAIN_LANES="${SEOCHO_MAIN_LANES//,/ }"
profile_flag=(); [ "$SEOCHO_PROFILE_QUERY_PLANS" = "1" ] && profile_flag=(--profile-query-plans)
SEOCHO_REASONING_MODES="${SEOCHO_REASONING_MODES//,/ }"

ROOT="$SEOCHO_APPLIANCE_ARTIFACTS/e2e"
INPUT="$SEOCHO_APPLIANCE_INPUT"
mkdir -p "$ROOT"
failures=0

run() {
  local label="$1"
  shift
  printf '%s\tstart\t%s\n' "$label" "$(date -Is)" >> "$ROOT/progress.tsv"
  local status=0
  if [ "$SEOCHO_CONCURRENCY" -le 1 ]; then
    python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/run_agentic_os_schedule.py" \
      "$@" --inter-attempt-gap-s "$SEOCHO_INTER_ATTEMPT_GAP_S" \
      --prompt-profile "$SEOCHO_PROMPT_PROFILE" || status=$?
  else
    # K coordinators share one vLLM (needs --max-num-seqs >= K) and run
    # disjoint ordinal shards; each shard gets its own run-root suffix so
    # attempt directories never collide.  GPU attribution per attempt is
    # confounded at K > 1 by design; the analysis reports aggregates.
    local pids=() i
    for ((i = 0; i < SEOCHO_CONCURRENCY; i++)); do
      python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/run_agentic_os_schedule.py" \
        "$@" --inter-attempt-gap-s "$SEOCHO_INTER_ATTEMPT_GAP_S" \
        --prompt-profile "$SEOCHO_PROMPT_PROFILE" --shard "$i/$SEOCHO_CONCURRENCY" \
        --run-root-suffix "shard-$i" &
      pids+=("$!")
    done
    for pid in "${pids[@]}"; do wait "$pid" || status=$?; done
  fi
  if [ "$status" -eq 0 ]; then
    printf '%s\tcomplete\t%s\n' "$label" "$(date -Is)" >> "$ROOT/progress.tsv"
  else
    failures=$((failures + 1))
    printf '%s\tfailed\t%s\n' "$label" "$(date -Is)" >> "$ROOT/progress.tsv"
  fi
  sleep "$SEOCHO_INTER_ATTEMPT_GAP_S"
}

gate_cells() {
  local GROOT="$1"; shift
  for thinking in $SEOCHO_REASONING_MODES; do
    run "gate-grbench-single-tool-$thinking" \
      --lane grbench-single --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/$SEOCHO_GRBENCH_SCHEDULE" \
      --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/$SEOCHO_GRBENCH_DISCOVERY" \
      --run-root "$GROOT/gate-grbench-single-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" "$@"

    run "gate-grbench-multi-tool-$thinking" \
      --lane grbench-multi --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/$SEOCHO_GRBENCH_SCHEDULE" \
      --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/$SEOCHO_GRBENCH_DISCOVERY" \
      --run-root "$GROOT/gate-grbench-multi-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" "$@"

    run "gate-gtsqa-single-tool-$thinking" \
      --lane gtsqa-single --schedule "$INPUT/$SEOCHO_GTSQA_SINGLE_SCHEDULE" \
      --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/$SEOCHO_GTSQA_DISCOVERY" \
      --run-root "$GROOT/gate-gtsqa-single-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" "$@"

    run "gate-gtsqa-multi-tool-$thinking" \
      --lane gtsqa-multi --schedule "$INPUT/$SEOCHO_GTSQA_MULTI_SCHEDULE" \
      --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/$SEOCHO_GTSQA_DISCOVERY" \
      --run-root "$GROOT/gate-gtsqa-multi-tool-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance \
      --only-arm B0 --max-attempts 1 \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" "$@"
  done
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

  if [ -z "$SEOCHO_GATE_STRATA" ]; then
    gate_cells "$ROOT"
    python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/validate_agentic_os_one_case_gate.py" \
      --run-root "$ROOT" --receipt-out "$ROOT/one-case-gate.json" \
      --b0-criterion "$SEOCHO_GATE_B0_CRITERION"
  else
    local gate_status=0
    for stratum in $SEOCHO_GATE_STRATA; do
      mkdir -p "$ROOT/strata/$stratum"
      gate_cells "$ROOT/strata/$stratum" --only-stratum "$stratum"
      python3 "$SEOCHO_REPO_ROOT/scripts/benchmarks/validate_agentic_os_one_case_gate.py" \
        --run-root "$ROOT/strata/$stratum" --receipt-out "$ROOT/strata/$stratum/one-case-gate.json" \
        --b0-criterion "$SEOCHO_GATE_B0_CRITERION" || gate_status=$?
    done
    return "$gate_status"
  fi
}

# Which frozen arms each lane's runner accepts (lab 015). An unsupported pair is
# logged to progress.tsv and skipped instead of failing the whole pass.
arm_supported() {
  local lane="$1" arm="$2" ok=1
  case "$lane" in
    grbench-single) case "$arm" in B0|C-|S1|O1) ok=0;; esac;;
    grbench-graphcot) case "$arm" in B0|O1) ok=0;; esac;;
    grbench-multi) case "$arm" in B0|C-|S1) ok=0;; esac;;
    gtsqa-single) case "$arm" in B0|C-|S1|S2a|S2b|S3|S4|O1) ok=0;; esac;;
    gtsqa-multi) case "$arm" in B0|S4) ok=0;; esac;;
  esac
  [ "$ok" = 0 ] || printf '%s\tskipped_unsupported_arm\t%s\n' "raw-$lane-tool-$arm" "$(date -Is)" >> "$ROOT/progress.tsv"
  return "$ok"
}

# Lane filter (lab 017 / single-agent focus): a lane not listed in SEOCHO_MAIN_LANES is
# skipped with a progress.tsv row, so historical queues (four lanes) are unchanged.
lane_enabled() {
  local lane="$1"
  case " $SEOCHO_MAIN_LANES " in *" $lane "*) return 0;; esac
  printf '%s\tskipped_lane_not_selected\t%s\n' "raw-$lane-tool" "$(date -Is)" >> "$ROOT/progress.tsv"
  return 1
}

main_matrix() {
  # Raw SEOCHO-OFF evidence is isolated to B0.  Treatment arms are scheduled
  # later and never pooled into this baseline catalog.
  for thinking in $SEOCHO_REASONING_MODES; do
    [ "$SEOCHO_MAIN_SKIP_CLOSED" = "1" ] || run "raw-grbench-single-closed-$thinking" \
      --lane grbench-single --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/$SEOCHO_GRBENCH_SCHEDULE" \
      --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/$SEOCHO_GRBENCH_DISCOVERY" \
      --run-root "$ROOT/raw-grbench-single-closed-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition closed_book \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}

    [ "$SEOCHO_MAIN_SKIP_CLOSED" = "1" ] || run "raw-gtsqa-single-closed-$thinking" \
      --lane gtsqa-single --schedule "$INPUT/$SEOCHO_GTSQA_SINGLE_SCHEDULE" \
      --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/$SEOCHO_GTSQA_DISCOVERY" \
      --run-root "$ROOT/raw-gtsqa-single-closed-$thinking" --base-url http://127.0.0.1:8000 \
      --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition closed_book \
      --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm B0 --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}

    # Schema / treatment arms (lab 015). B0 keeps the historical run-root names;
    # other arms are suffixed so catalogs can glob e2e/*-tool-* and group by arm.
    for arm in $SEOCHO_MAIN_ARMS; do
      suffix=""; [ "$arm" = "B0" ] || suffix="-$arm"
    if lane_enabled grbench-single && arm_supported grbench-single "$arm"; then run "raw-grbench-single-tool-$thinking$suffix" \
    --lane grbench-single --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/$SEOCHO_GRBENCH_SCHEDULE" "${profile_flag[@]}" \
    --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/$SEOCHO_GRBENCH_DISCOVERY" \
    --run-root "$ROOT/grbench-single-tool-$thinking$suffix" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
    --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm "$arm" --continue-on-error \
      ${SEOCHO_GRBENCH_ROW_CAP:+--row-cap "$SEOCHO_GRBENCH_ROW_CAP"} ${SEOCHO_GRBENCH_BYTE_CAP:+--byte-cap "$SEOCHO_GRBENCH_BYTE_CAP"} \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}
    fi

    if lane_enabled grbench-multi && arm_supported grbench-multi "$arm"; then run "raw-grbench-multi-tool-$thinking$suffix" \
    --lane grbench-multi --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/$SEOCHO_GRBENCH_SCHEDULE" \
    --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/$SEOCHO_GRBENCH_DISCOVERY" \
    --run-root "$ROOT/grbench-multi-tool-$thinking$suffix" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
    --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm "$arm" --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}
    fi

    if lane_enabled gtsqa-single && arm_supported gtsqa-single "$arm"; then run "raw-gtsqa-single-tool-$thinking$suffix" \
    --lane gtsqa-single --schedule "$INPUT/$SEOCHO_GTSQA_SINGLE_SCHEDULE" \
    --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/$SEOCHO_GTSQA_DISCOVERY" \
    --run-root "$ROOT/gtsqa-single-tool-$thinking$suffix" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm "$arm" --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}
    fi

    if lane_enabled gtsqa-multi && arm_supported gtsqa-multi "$arm"; then run "raw-gtsqa-multi-tool-$thinking$suffix" \
    --lane gtsqa-multi --schedule "$INPUT/$SEOCHO_GTSQA_MULTI_SCHEDULE" \
    --dataset "$INPUT/gtsqa-with-graphs.parquet" --discovery-cache "$INPUT/$SEOCHO_GTSQA_DISCOVERY" \
    --run-root "$ROOT/gtsqa-multi-tool-$thinking$suffix" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" \
    --max-turns "$SEOCHO_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
      --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --only-arm "$arm" --continue-on-error \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}
    fi
    if lane_enabled grbench-graphcot && arm_supported grbench-graphcot "$arm"; then run "raw-grbench-graphcot-tool-$thinking$suffix" \
    --lane grbench-graphcot --grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID" --schedule "$INPUT/$SEOCHO_GRBENCH_SCHEDULE" \
    --dataset "$INPUT/grbench-questions.json" --discovery-cache "$INPUT/$SEOCHO_GRBENCH_DISCOVERY" \
    --run-root "$ROOT/grbench-graphcot-tool-$thinking$suffix" --base-url http://127.0.0.1:8000 \
    --model "$VLLM_MODEL" --enable-thinking "$thinking" --condition tool_auto \
    --max-turns "$SEOCHO_GRAPHCOT_MAX_TURNS" --max-output-tokens "$SEOCHO_MAX_OUTPUT_TOKENS" \
    --token-budget "$SEOCHO_TOKEN_BUDGET" --gpu-sampling appliance --db-pid "$DOZERDB_PID" \
      --only-arm "$arm" --continue-on-error \
      ${SEOCHO_GRBENCH_ROW_CAP:+--row-cap "$SEOCHO_GRBENCH_ROW_CAP"} ${SEOCHO_GRBENCH_BYTE_CAP:+--byte-cap "$SEOCHO_GRBENCH_BYTE_CAP"} \
      --cache-control-script "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/cache_control.sh" ${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}
    fi
    done
  done
  return 0
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
