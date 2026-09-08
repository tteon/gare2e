from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _coordinator():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "run_agentic_os_schedule.py"
    spec = importlib.util.spec_from_file_location("run_agentic_os_schedule", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(lane: str, condition: str = "tool_auto") -> SimpleNamespace:
    return SimpleNamespace(
        lane=lane,
        base_url="http://vllm",
        model="model",
        enable_thinking="true",
        condition=condition,
        metrics_url="http://vllm/metrics",
        dataset=Path("dataset"),
        discovery_cache=Path("discovery.json"),
        max_turns=6,
        max_output_tokens=2048,
        token_budget=8192,
        gpu_sampling="external",
        db_pid=None,
        profile_query_plans=False,
    )


def _entry(arm: str) -> dict[str, object]:
    return {
        "qid": "7",
        "arm": arm,
        "seed": 0,
        "repetition": 0,
        "cache_phase": "warm",
    }


def test_single_lane_command_carries_condition_and_shared_budget(tmp_path) -> None:
    module = _coordinator()
    command = module._runner_command(
        _args("grbench-single", "closed_book"),
        _entry("B0"),
        tmp_path,
        "run",
    )

    assert command[1].endswith("run_direct_react_text2cypher.py")
    assert command[command.index("--condition") + 1] == "closed_book"
    assert command[command.index("--max-output-tokens") + 1] == "2048"
    assert command[command.index("--token-budget") + 1] == "8192"
    assert "--gpu-samples-out" not in command


def test_multi_lanes_call_agents_sdk_agent_tool_runners(tmp_path) -> None:
    module = _coordinator()
    cypher = module._runner_command(
        _args("grbench-multi"),
        _entry("S1"),
        tmp_path,
        "cypher-run",
    )
    sparql = module._runner_command(
        _args("gtsqa-multi"),
        _entry("S4"),
        tmp_path,
        "sparql-run",
    )

    assert cypher[1].endswith("run_grbench_agents_sdk_handoff.py")
    assert sparql[1].endswith("run_gtsqa_agents_sdk_handoff.py")
    assert "--condition" not in cypher
    assert "--condition" not in sparql
    assert cypher[cypher.index("--token-budget") + 1] == "8192"
    assert sparql[sparql.index("--token-budget") + 1] == "8192"
    assert "--gpu-samples-out" not in cypher
    assert "--gpu-samples-out" not in sparql


def test_co_located_gpu_sampling_is_explicit(tmp_path) -> None:
    module = _coordinator()
    args = _args("grbench-single")
    args.gpu_sampling = "co-located"
    command = module._runner_command(args, _entry("S1"), tmp_path, "run")

    assert command[command.index("--gpu-samples-out") + 1].endswith("metrics/gpu.csv")


def test_grbench_command_carries_explicit_dozerdb_host_pid(tmp_path) -> None:
    module = _coordinator()
    args = _args("grbench-multi")
    args.db_pid = 4242
    command = module._runner_command(args, _entry("S1"), tmp_path, "run")

    assert command[command.index("--db-pid") + 1] == "4242"


def test_grbench_command_can_request_out_of_band_profile_replay(tmp_path) -> None:
    module = _coordinator()
    args = _args("grbench-single")
    args.profile_query_plans = True
    command = module._runner_command(args, _entry("B0"), tmp_path, "run")

    assert "--profile-query-plans" in command


def test_attempt_id_changes_with_execution_treatment() -> None:
    module = _coordinator()
    entry = {**_entry("B0"), "paired_unit": "7:0:warm:0"}

    non_reasoning = module._attempt_id(
        "grbench-single", entry, execution_digest="non-reasoning-digest"
    )
    reasoning = module._attempt_id(
        "grbench-single", entry, execution_digest="reasoning-digest"
    )

    assert non_reasoning != reasoning


def test_grbench_lanes_pin_the_runtime_valid_workspace_id(tmp_path) -> None:
    module = _coordinator()
    for lane in ("grbench-single", "grbench-multi"):
        command = module._runner_command(_args(lane), _entry("B0"), tmp_path, "run")
        assert command[command.index("--workspace-id") + 1] == "grbench-maple-physics"
    for lane in ("gtsqa-single", "gtsqa-multi"):
        command = module._runner_command(_args(lane), _entry("B0"), tmp_path, "run")
        assert "--workspace-id" not in command


def test_grbench_workspace_id_override_is_forwarded(tmp_path) -> None:
    module = _coordinator()
    args = _args("grbench-single")
    args.grbench_workspace_id = "grbench-smoke"
    command = module._runner_command(args, _entry("B0"), tmp_path, "run")
    assert command[command.index("--workspace-id") + 1] == "grbench-smoke"


def test_runner_streams_are_persisted_privately_even_when_runner_dies_early(tmp_path) -> None:
    module = _coordinator()
    attempt_root = tmp_path / "attempts" / "000000-deadbeef"
    module._persist_runner_streams(attempt_root, b"", b"ModuleNotFoundError: No module named 'runtime'\n")
    stderr = attempt_root / "runner.stderr.private.log"
    assert stderr.read_text(encoding="utf-8").startswith("ModuleNotFoundError")
    assert (attempt_root / "runner.stdout.private.log").read_bytes() == b""
    assert oct(stderr.stat().st_mode & 0o777) == "0o600"


def test_prompt_profile_is_forwarded_to_every_lane(tmp_path) -> None:
    module = _coordinator()
    for lane in ("grbench-single", "grbench-multi", "gtsqa-single", "gtsqa-multi"):
        args = _args(lane)
        command = module._runner_command(args, _entry("B0"), tmp_path, "run")
        assert command[command.index("--prompt-profile") + 1] == "baseline"
        args.prompt_profile = "strengthened"
        command = module._runner_command(args, _entry("B0"), tmp_path, "run")
        assert command[command.index("--prompt-profile") + 1] == "strengthened"


def test_only_stratum_filter_is_wired_into_the_coordinator() -> None:
    source = (Path(__file__).parents[2] / "scripts/benchmarks/run_agentic_os_schedule.py").read_text(encoding="utf-8")
    assert '"--only-stratum"' in source
    assert 'str(entry.get("stratum")) in set(args.only_stratum)' in source
