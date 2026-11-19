"""Local deployment evidence and request limits use synthetic FP8 header fixtures."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from seocho.eval.sf1_fp8 import MODEL_PATH, SF1FP8Profile, file_sha256, verify_model


@pytest.fixture
def model(tmp_path):
    values = {
        "config.json": json.dumps(
            {"dtype": "bfloat16", "quantization_config": {"quant_method": "fp8"}}
        ).encode(),
        "tokenizer.json": b"{}",
        "tokenizer_config.json": b"{}",
        "chat_template.jinja": b"synthetic",
        "generation_config.json": b"{}",
    }
    header = json.dumps(
        {"layer.weight": {"dtype": "F8_E4M3", "shape": [1], "data_offsets": [0, 1]}}
    ).encode()
    values["model.safetensors"] = len(header).to_bytes(8, "little") + header + b"\0"
    for name, content in values.items():
        (tmp_path / name).write_bytes(content)
    manifest = {
        "revision": "017b9c7a" + "0" * 32,
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in values.items()
        },
    }
    return tmp_path, manifest


def test_fp8_weights_allow_bfloat16_activation_config(model):
    root, manifest = model
    receipt = verify_model(root, manifest)
    assert receipt["weight_quantization"] == "fp8"
    assert receipt["fp8_weight_tensors"] == 1
    assert receipt["files"] == manifest["files"]


@pytest.mark.parametrize(
    "failure",
    ["hash", "bf16", "revision", "extra_shard", "escape", "tokenizer_asset", "index"],
)
def test_wrong_or_ambiguous_checkpoint_is_rejected(model, failure, tmp_path):
    root, manifest = model
    if failure == "hash":
        (root / "tokenizer.json").write_text("modified")
    elif failure == "bf16":
        (root / "config.json").write_text(
            '{"quantization_config":{"quant_method":"bf16"}}'
        )
        manifest["files"]["config.json"] = file_sha256(root / "config.json")
    elif failure == "revision":
        manifest["revision"] = "1" * 40
    elif failure == "extra_shard":
        (root / "extra.safetensors").write_bytes(b"not pinned")
    elif failure == "tokenizer_asset":
        (root / "added_tokens.json").write_text("{}")
    elif failure == "index":
        (root / "model.safetensors.index.json").write_text("{}")
    else:
        manifest["files"]["../outside"] = "0" * 64
    with pytest.raises(ValueError):
        verify_model(root, manifest)


def test_runtime_requires_exact_settings_and_kernel_evidence():
    profile = SF1FP8Profile(2, 2)
    resolved = profile.serving_settings()
    evidence = profile.verify_runtime(
        resolved, "Using MarlinFP8ScaledMMLinearKernel for Fp8LinearMethod"
    )
    assert evidence["status"] == "EVIDENCE_MATCH_NOT_LIVE_VERIFIED"
    for field, bad in (
        ("tensor_parallel_size", 1),
        ("seed", 0),
        ("enable_prefix_caching", 1),
        ("vllm_version", "other"),
        ("reasoning_parser", "none"),
        ("tool_call_parser", "none"),
        ("enable_auto_tool_choice", False),
        ("load_format", "pt"),
    ):
        with pytest.raises(ValueError, match="runtime_setting_mismatch"):
            profile.verify_runtime(
                {**resolved, field: bad}, "Using MarlinFP8LinearKernel"
            )
    with pytest.raises(ValueError, match="kernel_evidence"):
        profile.verify_runtime(
            resolved, "Marlin is supported but Using BF16LinearKernel"
        )


@pytest.mark.parametrize(
    "log",
    [
        "Not Using MarlinFP8ScaledMMLinearKernel",
        "Using MarlinFP8ScaledMMLinearKernel failed",
        "Using MarlinFP8ScaledMMLinearKernel\ninitialization failed",
        "Using MarlinFP8ScaledMMLinearKernel\nUsing BF16LinearKernel",
        "Using MarlinFP8ScaledMMLinearKernel\nERROR worker initialization",
        "Using MarlinFP8ScaledMMLinearKernel\nERROR [worker.py:100] CUDA out of memory",
        "Using MarlinFP8ScaledMMLinearKernel\nNot Using MarlinFP8ScaledMMLinearKernel",
    ],
)
def test_rejects_negated_or_failed_kernel_evidence(log):
    profile = SF1FP8Profile(2, 2)
    with pytest.raises(ValueError, match="kernel_evidence"):
        profile.verify_runtime(profile.serving_settings(), log)


def test_accepts_log_selection_with_vllm_prefix():
    profile = SF1FP8Profile(2, 2)
    receipt = profile.verify_runtime(
        profile.serving_settings(),
        "INFO 11-02 12:00:00 [utils.py:100] Using MarlinFP8ScaledMMLinearKernel for Fp8LinearMethod.",
    )
    assert receipt["kernel"] == "Marlin FP8 W8A16"


@pytest.mark.parametrize(
    "failure",
    ["missing_data", "header_length", "shape", "offsets", "overlap", "dtype", "index"],
)
def test_corrupt_safetensors_never_static_pass(model, failure):
    root, manifest = model
    path = root / "model.safetensors"
    raw = path.read_bytes()
    n = int.from_bytes(raw[:8], "little")
    header = json.loads(raw[8 : 8 + n])
    if failure == "missing_data":
        path.write_bytes(raw[:-1])
    elif failure == "header_length":
        path.write_bytes((n + 10).to_bytes(8, "little") + raw[8:])
    elif failure == "index":
        index = root / "model.safetensors.index.json"
        index.write_text(
            json.dumps({"weight_map": {"wrong.weight": "model.safetensors"}})
        )
        manifest["files"][index.name] = file_sha256(index)
    else:
        tensor = header["layer.weight"]
        if failure == "shape":
            tensor["shape"] = [2]
        elif failure == "offsets":
            tensor["data_offsets"] = [1, 0]
        elif failure == "dtype":
            tensor["dtype"] = "NOT_A_DTYPE"
        else:
            header["other.weight"] = dict(tensor)
        data = json.dumps(header).encode()
        path.write_bytes(len(data).to_bytes(8, "little") + data + b"\0")
    manifest["files"][path.name] = file_sha256(path)
    with pytest.raises(ValueError):
        verify_model(root, manifest)


@pytest.mark.parametrize("tp,gpus", [(1, 2), (0, 0), (True, 1), (1, True)])
def test_tp_is_explicit_and_consistent(tp, gpus):
    with pytest.raises(ValueError):
        SF1FP8Profile(tp, gpus)


def test_command_and_pi_profile_preserve_campaign_limits():
    profile = SF1FP8Profile(2, 2)
    argv = profile.server_argv()
    assert argv[:3] == ["vllm", "serve", MODEL_PATH]
    for flag, expected in (
        ("--max-num-seqs", "8"),
        ("--max-model-len", "8192"),
        ("--max-num-batched-tokens", "8192"),
        ("--seed", "42"),
        ("--kv-cache-memory-bytes", str(12 * 1024**3)),
        ("--quantization", "fp8"),
    ):
        assert argv[argv.index(flag) + 1] == expected
    assert "--enforce-eager" not in argv
    p = profile.pi_profile(max_requests=2, deadline_ms=60000)
    assert p["max_output_tokens"] == 800 and p["thinking"] == "high" and p["seed"] == 42
    assert p["max_total_tokens"] == 2 * 8192
    assert (
        SF1FP8Profile(2, 2, "off").pi_profile(max_requests=2, deadline_ms=1)["thinking"]
        == "off"
    )
    script = """
import {validateProfile,applyPayload} from './scripts/benchmarks/pi_native/profile.mjs';
const p=validateProfile(JSON.parse(process.argv[1]));
const result=applyPayload({max_output_tokens:800,seed:0},p);
console.log(JSON.stringify(result));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(p)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["seed"] == 42 and payload["temperature"] == 0
    assert payload["max_output_tokens"] == 800
    assert payload["reasoning"]["effort"] == "xhigh"


def test_cli_rejects_model_path_substitution_without_private_output(tmp_path):
    secret = "PRIVATE_SENTINEL"
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/benchmarks/prepare_sf1_fp8.py",
            "--model-dir",
            str(tmp_path / secret),
            "--model-manifest",
            str(tmp_path / secret),
            "--expected-manifest-sha256",
            "0" * 64,
            "--gpu-count",
            "2",
            "--tp",
            "2",
            "--max-requests",
            "2",
            "--deadline-ms",
            "60000",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        },
    )
    assert proc.returncode == 1
    assert secret not in proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["error_code"] == "FP8_PREFLIGHT_FAILED"
