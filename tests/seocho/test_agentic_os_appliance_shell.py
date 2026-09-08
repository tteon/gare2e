from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_bootstrap_secret_generation_has_no_preinstall_binary_dependency() -> None:
    script = (ROOT / "scripts/benchmarks/bootstrap_agentic_os_appliance.sh").read_text(
        encoding="utf-8"
    )

    assert "secrets.token_hex" in script
    assert "python3 -c" in script
    assert '"$SEOCHO_VENV/bin/python" -m pip' in script
    assert "openssl rand" not in script


def test_bootstrap_isolates_benchmark_dependencies_in_a_venv() -> None:
    script = (ROOT / "scripts/benchmarks/bootstrap_agentic_os_appliance.sh").read_text(
        encoding="utf-8"
    )

    assert "python3 -m venv --system-site-packages" in script
    assert "pip-before.private.txt" in script
    assert "pip-after.private.txt" in script
    assert "pip-check.private.log" in script
    assert "pip-check-before.private.log" in script
    assert "pip-check-new-conflicts.private.txt" in script
    assert "introduced new pip conflicts" in script


def test_appliance_freezes_dozer_memory_and_vllm_observability_flags() -> None:
    script = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert "server.memory.heap.initial_size" in script
    assert "server.memory.heap.max_size" in script
    assert "server.memory.pagecache.size" in script
    assert "--enable-prefix-caching" in script
    assert "--reasoning-parser" in script
    assert "--kv-cache-metrics" in script
    assert "--kv-cache-metrics-sample" in script
    assert "VLLM_SERVER_DEV_MODE=1" in script
    assert "/metrics" in script
    assert "kill -0 \"$vllm_pid\"" in script
    assert "kill -0 \"$dozer_launcher_pid\"" in script


def test_appliance_has_real_prefix_cache_phase_control() -> None:
    script = (ROOT / "docker/agentic-os-appliance/cache_control.sh").read_text(
        encoding="utf-8"
    )

    assert "/reset_prefix_cache" in script
    assert 'get("success") is True' in script
    assert "vllm_prefix_cache_reset" in script


def test_appliance_bulk_imports_grbench_offline_before_dozer_starts() -> None:
    script = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    import_at = script.index("neo4j-admin database import full neo4j")
    start_at = script.index('record_phase "service_start"')
    assert import_at < start_at
    assert "--overwrite-destination=true" in script
    assert "SEOCHO_GRBENCH_IMPORT_DIR" in script
    assert "grbench-bulk-import-receipt.json" in script


def test_e2e_verifies_bulk_import_and_bounds_the_bolt_projection() -> None:
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    assert "--verify-only --schema-receipt" in script
    assert 'timeout --signal=TERM "$SEOCHO_GRBENCH_LOAD_TIMEOUT_S"' in script
    assert script.count('--grbench-workspace-id "$SEOCHO_GRBENCH_WORKSPACE_ID"') == script.count("--lane grbench-")
    assert "grbench:maple:physics" not in script


def test_appliance_fails_fast_on_vllm_startup_stall_and_freezes_nccl_fallback() -> None:
    script = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    assert 'NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}"' in script
    assert 'NCCL_DEBUG="${NCCL_DEBUG:-WARN}"' in script
    assert "gpu-topology.private.txt" in script
    assert "VLLM_STALL_TIMEOUT_S" in script
    assert "vLLM startup stalled" in script
    # The weight download is silent on stdout for 13+ minutes (V13: 803 s), so
    # the detector must treat HF-cache and GPU-memory growth as progress.
    assert "du -sb \"$hf_cache\"" in script
    assert "grep -v 'shm_broadcast'" in script  # idle heartbeat is not progress
    assert "nvidia-smi --query-gpu=memory.used" in script
    assert 'VLLM_STALL_TIMEOUT_S:-900' in script


def test_bootstrap_import_preflight_covers_gtsqa_lane_runner_dependencies() -> None:
    script = (ROOT / "scripts/benchmarks/bootstrap_agentic_os_appliance.sh").read_text(encoding="utf-8")
    assert "runtime/policy.py" in script
    assert 'record_phase "import_preflight"' in script
    assert '"runtime.policy"' in script
    assert '"seocho.eval.oxigraph_runtime"' in script
    assert script.index('record_phase "import_preflight"') < script.index('record_phase "appliance_entrypoint"')


def test_appliance_persists_secret_for_persistent_lab_sessions_with_private_mode() -> None:
    script = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    assert 'record_phase "appliance_secret_file"' in script
    assert "umask 077; printf '%s' \"$NEO4J_PASSWORD\" > \"$SEOCHO_APPLIANCE_SECRET_FILE\"" in script
    assert script.index('record_phase "appliance_secret_file"') < script.index('record_phase "service_start"')


def test_restart_helper_mirrors_entrypoint_vllm_flags_and_adds_prompt_token_details() -> None:
    entrypoint = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    helper = (ROOT / "docker/agentic-os-appliance/restart_vllm.sh").read_text(encoding="utf-8")
    for flag in ("--enable-prefix-caching", "--enable-auto-tool-choice", '--tool-call-parser "${VLLM_TOOL_CALL_PARSER:-qwen3_coder}"', '--reasoning-parser "${VLLM_REASONING_PARSER:-qwen3}"', "--kv-cache-metrics", "--language-model-only", '--max-num-seqs "${VLLM_MAX_NUM_SEQS:-4}"'):
        assert flag in entrypoint and flag in helper, flag
    assert "--enable-prompt-tokens-details" in helper
    assert "vllm-restart-receipt.json" in helper and "extra_args_sha256" in helper


def test_lab_pass_runner_brackets_e2e_with_workload_wrapper_and_secret_file() -> None:
    script = (ROOT / "docker/agentic-os-appliance/run_lab_pass.sh").read_text(encoding="utf-8")
    assert 'NEO4J_PASSWORD="$(cat "$SEOCHO_APPLIANCE_SECRET_FILE")"' in script
    assert "run_agentic_os_colocated_workload.py" in script and '--role-pid "vllm=$vllm_pid"' in script
    assert 'if [ -e "$SEOCHO_APPLIANCE_ARTIFACTS" ]; then' in script
    assert "pass-receipt.json" in script


def test_e2e_supports_frozen_input_overrides_and_per_stratum_gate() -> None:
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    for var in ("SEOCHO_GRBENCH_SCHEDULE", "SEOCHO_GTSQA_SINGLE_SCHEDULE", "SEOCHO_GTSQA_MULTI_SCHEDULE", "SEOCHO_GRBENCH_DISCOVERY", "SEOCHO_GTSQA_DISCOVERY", "SEOCHO_GATE_STRATA"):
        assert var in script, var
    assert "grbench-single-tool-schedule.json\"" not in script.replace(': "${SEOCHO_GRBENCH_SCHEDULE:=grbench-single-tool-schedule.json}"', "")
    assert 'gate_cells "$ROOT/strata/$stratum" --only-stratum "$stratum"' in script
    assert 'SEOCHO_GATE_STRATA="${SEOCHO_GATE_STRATA//,/ }"' in script  # env-only channels cannot quote spaces
    assert script.count('"$@"') >= 4  # every gate lane forwards the stratum filter


def test_lab_queue_runner_is_unattended_and_exits_when_done() -> None:
    script = (ROOT / "docker/agentic-os-appliance/run_lab_queue.sh").read_text(encoding="utf-8")
    assert "sleep infinity" not in script
    assert "queue-receipt.jsonl" in script and "SEOCHO_QUEUE_STOP_ON_FAIL" in script
    assert 'env PASS_ID="$pass_id" $payload bash "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/run_lab_pass.sh"' in script


def test_e2e_concurrency_runs_disjoint_shards_and_main_filters() -> None:
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    assert '--shard "$i/$SEOCHO_CONCURRENCY"' in script and '--run-root-suffix "shard-$i"' in script
    assert 'for pid in "${pids[@]}"; do wait "$pid" || status=$?; done' in script
    assert script.count('${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}') == 6
    assert script.count('[ "$SEOCHO_MAIN_SKIP_CLOSED" = "1" ] || run "raw-') == 2


def test_restart_helper_accepts_comma_separated_extra_args_with_json() -> None:
    import subprocess
    script = ROOT / "docker/agentic-os-appliance/restart_vllm.sh"
    # extract the parsing block and run it in a bash subshell against a sample value
    body = script.read_text(encoding="utf-8")
    start = body.index("extra=()"); end = body.index("printf '%s\\n' \"${vllm_command[@]}\"")
    snippet = body[start:end]
    probe = snippet + 'printf "%s\\n" "${extra[@]}"'
    out = subprocess.run(["bash", "-c", probe], env={"VLLM_EXTRA_ARGS": '--max-num-seqs,8,--speculative-config,{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_max":4}', "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == ["--max-num-seqs", "8", "--speculative-config", '{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_max":4}']
