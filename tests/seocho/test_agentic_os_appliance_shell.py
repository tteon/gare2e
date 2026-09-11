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
    restart = (ROOT / "docker/agentic-os-appliance/restart_vllm.sh").read_text(encoding="utf-8")
    # Both vLLM launch paths resolve NCCL from the same topology-gated helper
    # (lab-s4: a frozen NCCL_P2P_DISABLE=1 made TP=2 skip NVLink on an SXM host).
    for text in (script, restart):
        assert '/nccl_env.sh"' in text and "seocho_resolve_nccl_env" in text
        assert 'NCCL_P2P_DISABLE="${NCCL_P2P_DISABLE:-1}"' not in text
    assert "SEOCHO_NCCL_P2P_DECISION" in script
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
    assert script.count('${SEOCHO_MAIN_STRATUM:+--only-stratum "$SEOCHO_MAIN_STRATUM"}') == 7  # 2 closed-book + 4 tool lanes + graphcot (lab 017)
    assert script.count('[ "$SEOCHO_MAIN_SKIP_CLOSED" = "1" ] || run "raw-') == 2


def test_restart_helper_accepts_comma_separated_extra_args_with_json() -> None:
    import subprocess
    script = ROOT / "docker/agentic-os-appliance/restart_vllm.sh"
    # extract the parsing block and run it in a bash subshell against a sample value
    body = script.read_text(encoding="utf-8")
    start = body.index("extra=()"); end = body.index("# An override of a flag the base argv already carries")
    snippet = body[start:end]
    probe = snippet + 'printf "%s\\n" "${extra[@]}"'
    out = subprocess.run(["bash", "-c", probe], env={"VLLM_EXTRA_ARGS": '--max-num-seqs,8,--speculative-config,{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_max":4}', "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == ["--max-num-seqs", "8", "--speculative-config", '{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_max":4}']


NVLINK_TOPO = (
    "\t\x1b[4mGPU0\tGPU1\tCPU Affinity\tNUMA Affinity\tGPU NUMA ID\x1b[0m\n"
    "GPU0\t X \tNV12\t32-63,160-191\t1\t\tN/A\n"
    "GPU1\tNV12\t X \t64-95,192-223\t2\t\tN/A\n\nLegend:\n"
)
PCIE_TOPO = (
    "\tGPU0\tGPU1\tNIC0\tCPU Affinity\tNUMA Affinity\tGPU NUMA ID\n"
    "GPU0\t X \tSYS\tNODE\t0-31\t0\t\tN/A\n"
    "GPU1\tSYS\t X \tSYS\t32-63\t1\t\tN/A\n"
    "NIC0\tNODE\tSYS\t X \n"
)
MIXED_TOPO = (
    "\tGPU0\tGPU1\tGPU2\tGPU3\tCPU Affinity\n"
    "GPU0\t X \tNV4\tPHB\tPHB\t0-31\n"
    "GPU1\tNV4\t X \tPHB\tPHB\t0-31\n"
    "GPU2\tPHB\tPHB\t X \tNV4\t32-63\n"
    "GPU3\tPHB\tPHB\tNV4\t X \t32-63\n"
)


def _resolve_nccl(topo: str, **env: str) -> tuple[str, str]:
    import os
    import subprocess

    helper = ROOT / "docker/agentic-os-appliance/nccl_env.sh"
    run_env = {k: v for k, v in os.environ.items() if k not in {"NCCL_P2P_DISABLE", "SEOCHO_NCCL_P2P_MODE"}}
    run_env.update(env)
    out = subprocess.run(
        ["bash", "-c", f'. "{helper}"; seocho_resolve_nccl_env "$1"; printf "%s\\n%s" "$NCCL_P2P_DISABLE" "$SEOCHO_NCCL_P2P_DECISION"', "_", topo],
        check=True, capture_output=True, text=True, env=run_env,
    ).stdout.split("\n", 1)
    return out[0], out[1]


def test_nccl_helper_enables_p2p_only_when_every_gpu_pair_is_nvlink() -> None:
    assert _resolve_nccl(NVLINK_TOPO)[0] == "0"            # lab-s4 host: NV12, escapes in header
    assert _resolve_nccl(PCIE_TOPO)[0] == "1"              # V14 host shape: SYS link, NIC column ignored
    disable, reason = _resolve_nccl(MIXED_TOPO)
    assert disable == "1" and "links=6 nvlink=2" in reason  # any non-NVLink pair -> fallback
    assert _resolve_nccl("")[0] == "1"                     # unknown topology -> fallback
    assert _resolve_nccl(NVLINK_TOPO, SEOCHO_NCCL_P2P_MODE="disable") == ("1", "mode=disable")
    assert _resolve_nccl(PCIE_TOPO, SEOCHO_NCCL_P2P_MODE="enable") == ("0", "mode=enable")
    assert _resolve_nccl(NVLINK_TOPO, NCCL_P2P_DISABLE="1") == ("1", "explicit:NCCL_P2P_DISABLE=1")


def test_restart_helper_replica_mode_routes_through_one_endpoint_and_dedupes_flags() -> None:
    restart = (ROOT / "docker/agentic-os-appliance/restart_vllm.sh").read_text(encoding="utf-8")
    lab_pass = (ROOT / "docker/agentic-os-appliance/run_lab_pass.sh").read_text(encoding="utf-8")
    # Session B: N TP=1 replicas pinned per GPU behind vllm_router.py on VLLM_PORT.
    assert 'CUDA_VISIBLE_DEVICES="$i" nohup "${cmd[@]}"' in restart
    assert "--tensor-parallel-size) cmd+=(--tensor-parallel-size 1)" in restart
    assert "vllm_router.py" in restart and '--policy "$SEOCHO_ROUTER_POLICY"' in restart
    # shared KV store arm: lmcache_server + LMCacheConnectorV1 on every replica
    assert "LMCacheConnectorV1" in restart and "LMCACHE_CONFIG_FILE" in restart
    # restarts also tear down the previous router / KV server
    assert "pkill -TERM -f 'vllm_router.py'" in restart
    # an override replaces the base flag instead of duplicating it
    assert "vllm_command=(\"${deduped[@]}\")" in restart
    # a replica/LMCache pass restarts even with unchanged extra args
    assert '[ -n "${VLLM_REPLICAS:-}" ] || [ -n "${LMCACHE_SHARED:-}" ]' in lab_pass
    assert '"vllm_replicas":%s,"router_policy":"%s","lmcache_shared":%s' in lab_pass


def _router_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("vllm_router", ROOT / "docker/agentic-os-appliance/vllm_router.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_vllm_router_sticky_key_and_metrics_merge() -> None:
    r = _router_module()
    case = {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "Q1"}, {"role": "assistant", "content": "t1"}]}
    later_turn = {"messages": case["messages"] + [{"role": "tool", "content": "rows"}, {"role": "assistant", "content": "t2"}]}
    other = {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "Q2"}]}
    assert r.routing_key(case) == r.routing_key(later_turn)   # every turn of a case -> same replica
    assert r.routing_key(case) != r.routing_key(other)
    a = "# HELP vllm:generation_tokens_total g\n# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total{m=\"x\"} 10\nvllm:ttft_bucket{le=\"+Inf\"} 3\nvllm:kv_cache_usage_perc 0.2\n"
    b = "# HELP vllm:generation_tokens_total g\n# TYPE vllm:generation_tokens_total counter\nvllm:generation_tokens_total{m=\"x\"} 5\nvllm:ttft_bucket{le=\"+Inf\"} 4\nvllm:kv_cache_usage_perc 0.1\n"
    merged = r.merge_metrics([a, b])
    assert 'vllm:generation_tokens_total{m="x"} 15' in merged
    assert 'vllm:ttft_bucket{le="+Inf"} 7' in merged
    assert "vllm:kv_cache_usage_perc 0.3" in merged
    assert merged.count("# TYPE vllm:generation_tokens_total") == 1


def test_vllm_router_forwards_content_type_on_every_proxied_call() -> None:
    from aiohttp.test_utils import make_mocked_request

    r = _router_module()
    req = make_mocked_request("POST", "/tokenize", headers={"Content-Type": "application/json", "X-Other": "drop-me"})
    assert r.forward_headers(req) == {"Content-Type": "application/json"}
    # lab-s5: passthrough without headers made vLLM answer 400 on /tokenize and
    # killed 3 of 4 lanes; every proxied call must forward the request headers.
    src = (ROOT / "docker/agentic-os-appliance/vllm_router.py").read_text(encoding="utf-8")
    assert src.count("headers=forward_headers(request)") == 3


def test_wrapper_strict_telemetry_is_opt_in_from_the_appliance() -> None:
    entry = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    lab_pass = (ROOT / "docker/agentic-os-appliance/run_lab_pass.sh").read_text(encoding="utf-8")
    for text in (entry, lab_pass):
        assert 'SEOCHO_STRICT_TELEMETRY:-0' in text and "--strict-telemetry" in text


def test_appliance_exports_otel_spans_and_enables_vllm_otlp_when_endpoint_set() -> None:
    lab_pass = (ROOT / "docker/agentic-os-appliance/run_lab_pass.sh").read_text(encoding="utf-8")
    assert "export_agentic_os_otel.py" in lab_pass and 'otel/spans.jsonl' in lab_pass
    assert '--otlp-endpoint "$SEOCHO_OTLP_ENDPOINT"' in lab_pass
    for name in ("entrypoint.sh", "restart_vllm.sh"):
        text = (ROOT / "docker/agentic-os-appliance" / name).read_text(encoding="utf-8")
        assert '--otlp-traces-endpoint "$SEOCHO_OTLP_ENDPOINT"' in text
        assert '--collect-detailed-traces "${SEOCHO_VLLM_DETAILED_TRACES:-all}"' in text


def test_appliance_can_run_an_in_instance_otlp_file_receiver() -> None:
    entry = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    assert "otlp_file_receiver.py" in entry and 'SEOCHO_OTEL_FILE_RECEIVER:-0' in entry
    assert 'collector-spans.jsonl' in entry
    src = (ROOT / "docker/agentic-os-appliance/otlp_file_receiver.py").read_text(encoding="utf-8")
    assert "add_TraceServiceServicer_to_server" in src and "seocho.otlp_file_receiver_span.v1" in src


def test_appliance_creates_grbench_indexes_once_before_any_pass_and_names_vllm_spans() -> None:
    entry = (ROOT / "docker/agentic-os-appliance/entrypoint.sh").read_text(encoding="utf-8")
    restart = (ROOT / "docker/agentic-os-appliance/restart_vllm.sh").read_text(encoding="utf-8")
    assert 'record_phase "dozer_indexes"' in entry and "load_grbench_dozerdb.py" in entry and "--verify-only" in entry
    assert entry.index('record_phase "dozer_indexes"') < entry.index('record_phase "workload"')
    for text in (entry, restart):
        assert 'OTEL_SERVICE_NAME="${SEOCHO_VLLM_OTEL_SERVICE_NAME:-vllm}"' in text
        # the runner's own OTLP push must not inherit the engine's service name (lab-s11)
        pass_text = (ROOT / "docker" / "agentic-os-appliance" / "run_lab_pass.sh").read_text(encoding="utf-8")
        assert '--service-name "${SEOCHO_OTEL_SERVICE_NAME:-seocho-agentic-os}"' in pass_text
        assert '--service-name "${OTEL_SERVICE_NAME' not in pass_text


def test_e2e_forwards_grbench_result_caps_only_when_set():
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    assert ': "${SEOCHO_GRBENCH_ROW_CAP:=}"' in script
    assert '${SEOCHO_GRBENCH_ROW_CAP:+--row-cap "$SEOCHO_GRBENCH_ROW_CAP"}' in script
    assert '${SEOCHO_GRBENCH_BYTE_CAP:+--byte-cap "$SEOCHO_GRBENCH_BYTE_CAP"}' in script


def test_e2e_main_matrix_loops_over_arms_with_suffixed_roots():
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    assert ': "${SEOCHO_MAIN_ARMS:=B0}"' in script
    assert 'SEOCHO_MAIN_ARMS="${SEOCHO_MAIN_ARMS//,/ }"' in script
    main = script[script.index("main_matrix() {"):]
    assert 'for arm in $SEOCHO_MAIN_ARMS; do' in main
    assert '[ "$arm" = "B0" ] || suffix="-$arm"' in main
    assert main.count('--only-arm "$arm"') == 5  # the four tool lanes + grbench-graphcot (lab 017)
    assert main.count("--only-arm B0") == 2  # the two closed-book baselines stay B0
    assert '"$ROOT/grbench-single-tool-$thinking$suffix"' in main


def test_e2e_skips_arms_a_lane_does_not_support(tmp_path):
    """Exercise arm_supported() from the real script with bash: unsupported pairs log a skip row."""
    import subprocess
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    start = script.index("arm_supported() {")
    end = script.index("\n}\n", start) + 3
    fn = script[start:end]
    probe = fn + (
        'ROOT="$1"; touch "$ROOT/progress.tsv"\n'
        'arm_supported grbench-single S4 && echo YES1 || echo NO1\n'
        'arm_supported gtsqa-multi S4 && echo YES2 || echo NO2\n'
        'arm_supported gtsqa-multi S1 && echo YES3 || echo NO3\n'
        'arm_supported grbench-multi C- && echo YES4 || echo NO4\n'
        'arm_supported grbench-single O1 && echo YES5 || echo NO5\n'
        'arm_supported grbench-multi O1 && echo YES6 || echo NO6\n'
    )
    out = subprocess.run(["bash", "-c", probe, "probe", str(tmp_path)], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["NO1", "YES2", "NO3", "YES4", "YES5", "NO6"]
    rows = (tmp_path / "progress.tsv").read_text(encoding="utf-8").splitlines()
    assert [r.split("\t")[0] for r in rows] == ["raw-grbench-single-tool-S4", "raw-gtsqa-multi-tool-S1", "raw-grbench-multi-tool-O1"]
    assert all("skipped_unsupported_arm" in r for r in rows)
    main = script[script.index("main_matrix() {"):]
    assert main.count('&& arm_supported ') == 5  # if lane_enabled X && arm_supported X; then run … fi (four whole-query lanes + graphcot)
    assert main.count('if lane_enabled ') == 5
    assert 'return 0' in main[:main.index('\n}\n')]


def test_pass_script_exports_otel_even_when_the_workload_fails():
    text = (ROOT / "docker" / "agentic-os-appliance" / "run_lab_pass.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert 'run_agentic_os_colocated_e2e.sh" || status=$?' in text
    assert "\nstatus=0\n" in text
    assert text.index("|| status=$?") < text.index("export_agentic_os_otel.py")


def test_e2e_lane_filter_and_graphcot_lane(tmp_path):
    import subprocess
    script = (ROOT / "scripts/benchmarks/run_agentic_os_colocated_e2e.sh").read_text(encoding="utf-8")
    assert ': "${SEOCHO_MAIN_LANES:=grbench-single grbench-multi gtsqa-single gtsqa-multi}"' in script  # historical default: graphcot opt-in
    assert ': "${SEOCHO_GRAPHCOT_MAX_TURNS:=12}"' in script
    main = script[script.index("main_matrix() {"):]
    assert main.count("lane_enabled ") == 5 and 'lane_enabled grbench-graphcot && arm_supported grbench-graphcot "$arm"' in main
    assert '--lane grbench-graphcot' in main and '--max-turns "$SEOCHO_GRAPHCOT_MAX_TURNS"' in main
    start = script.index("lane_enabled() {"); fn = script[start: script.index("\n}\n", start) + 3]
    probe = 'SEOCHO_MAIN_LANES="grbench-single grbench-graphcot"; ROOT="$1"; touch "$ROOT/progress.tsv"\n' + fn + (
        'lane_enabled grbench-graphcot && echo Y1 || echo N1\n'
        'lane_enabled gtsqa-multi && echo Y2 || echo N2\n'
    )
    out = subprocess.run(["bash", "-c", probe, "probe", str(tmp_path)], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["Y1", "N2"]
    rows = (tmp_path / "progress.tsv").read_text(encoding="utf-8").splitlines()
    assert rows == [r for r in rows if "skipped_lane_not_selected" in r] and rows[0].startswith("raw-gtsqa-multi-tool")
