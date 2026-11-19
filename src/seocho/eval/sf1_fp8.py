"""Offline FP8 deployment plans for a frozen SF1 comparison.

This module never starts a server, downloads weights, or infers resource ownership.
The owner supplies a trusted full-revision manifest before building a command.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MODEL_PATH = "/models/gare2e-pi-precision-fp8-017b9c7a"
VLLM_VERSION = "0.31.0"
KV_BYTES = 12 * 1024**3
_SHA = re.compile(r"[0-9a-f]{64}")
_DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E4M3": 1,
    "F8_E4M3FN": 1,
    "F8_E5M2": 1,
    "F8_E8M0": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "I64": 8,
    "U64": 8,
    "F64": 8,
}


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _local_file(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("model_file_outside_root_or_missing")
    return path


def _tensor_header(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw_length = stream.read(8)
        if len(raw_length) != 8:
            raise ValueError("invalid_safetensor_header")
        length = int.from_bytes(raw_length, "little")
        if not 1 <= length <= 16 * 1024**2:
            raise ValueError("invalid_safetensor_header")
        raw = stream.read(length)
        if len(raw) != length:
            raise ValueError("truncated_safetensor_header")
        header = json.loads(raw)
    if not isinstance(header, dict):
        raise ValueError("invalid_safetensor_header")
    metadata = header.pop("__metadata__", {})
    if not isinstance(metadata, dict) or any(
        not isinstance(v, str) for v in metadata.values()
    ):
        raise ValueError("invalid_safetensor_metadata")
    data_size = path.stat().st_size - 8 - length
    regions = []
    for item in header.values():
        if not isinstance(item, dict):
            raise ValueError("invalid_tensor")
        shape, offsets = item.get("shape"), item.get("data_offsets")
        dtype = item.get("dtype")
        if (
            not isinstance(shape, list)
            or any(type(n) is not int or n < 0 for n in shape)
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(type(n) is not int for n in offsets)
            or dtype not in _DTYPE_BYTES
        ):
            raise ValueError("invalid_tensor")
        start, end = offsets
        if (
            not 0 <= start <= end <= data_size
            or end - start != math.prod(shape) * _DTYPE_BYTES[dtype]
        ):
            raise ValueError("invalid_tensor_offsets")
        regions.append((start, end))
    cursor = 0
    for start, end in sorted(regions):
        if start != cursor:
            raise ValueError("overlapping_or_missing_tensor_data")
        cursor = end
    if cursor != data_size:
        raise ValueError("unaccounted_tensor_data")
    return header


def verify_model(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Hash all pinned files and inspect FP8 safetensor weight headers.

    The manifest must come from a trusted external record. Matching a manifest
    created from the same files does not authenticate a checkpoint's revision.
    """
    root = root.resolve()
    revision = manifest.get("revision", "")
    if not isinstance(revision, str) or not re.fullmatch(
        r"017b9c7a[0-9a-f]{32}", revision
    ):
        raise ValueError("snapshot_revision_mismatch")
    files = manifest.get("files")
    required = {
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "generation_config.json",
    }
    if not isinstance(files, dict) or not required <= files.keys():
        raise ValueError("model_manifest_incomplete")
    loader_assets = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
        and (
            p.suffix in {".json", ".jinja", ".model", ".tiktoken"}
            or p.name in {"merges.txt", "vocab.txt"}
        )
    }
    if not loader_assets <= files.keys():
        raise ValueError("unpinned_model_or_tokenizer_assets")
    for name, expected in files.items():
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or not _SHA.fullmatch(expected)
            or file_sha256(_local_file(root, name)) != expected
        ):
            raise ValueError("model_file_hash_mismatch")
    config = json.loads((root / "config.json").read_text())
    if config.get("quantization_config", {}).get("quant_method") != "fp8":
        raise ValueError("fp8_checkpoint_required")
    if "model.safetensors.index.json" in files:
        index = json.loads((root / "model.safetensors.index.json").read_text())
        weight_map = index["weight_map"]
        if not isinstance(weight_map, dict) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in weight_map.items()
        ):
            raise ValueError("invalid_weight_index")
        shards = set(weight_map.values())
    else:
        weight_map = None
        shards = {"model.safetensors"}
    if not shards or not shards <= files.keys():
        raise ValueError("unpinned_weight_shards")
    actual_shards = {str(p.relative_to(root)) for p in root.rglob("*.safetensors")}
    if actual_shards != shards:
        raise ValueError("ambiguous_weight_shards")
    fp8_weights = 0
    actual_map = {}
    for name in shards:
        header = _tensor_header(_local_file(root, name))
        if actual_map.keys() & header.keys():
            raise ValueError("duplicate_weight_tensor")
        actual_map.update({key: name for key in header})
        fp8_weights += sum(
            key.endswith(".weight") and value["dtype"] in {"F8_E4M3", "F8_E4M3FN"}
            for key, value in header.items()
        )
    if weight_map is not None and weight_map != actual_map:
        raise ValueError("weight_index_tensor_mismatch")
    if not fp8_weights:
        raise ValueError("fp8_weight_evidence_missing")
    return {
        "revision": revision,
        "files": dict(files),
        "fp8_weight_tensors": fp8_weights,
        "model_path": str(root),
        "weight_quantization": "fp8",
    }


@dataclass(frozen=True)
class SF1FP8Profile:
    """Explicit GPU/TP selection; all other serving knobs stay frozen."""

    tensor_parallel_size: int
    gpu_count: int
    thinking: str = "high"

    def __post_init__(self) -> None:
        if (
            type(self.tensor_parallel_size) is not int
            or type(self.gpu_count) is not int
            or self.gpu_count < 1
            or self.tensor_parallel_size != self.gpu_count
        ):
            raise ValueError("explicit_equal_gpu_and_tp_required")
        if self.thinking not in {"high", "off"}:
            raise ValueError("thinking_must_be_high_or_off")

    def serving_settings(self, model_path: str = MODEL_PATH) -> dict[str, Any]:
        return {
            "model": model_path,
            "tokenizer": model_path,
            "chat_template": str(Path(model_path) / "chat_template.jinja"),
            "generation_config": model_path,
            "vllm_version": VLLM_VERSION,
            "quantization": "fp8",
            "tensor_parallel_size": self.tensor_parallel_size,
            "gpu_count": self.gpu_count,
            "seed": 42,
            "dtype": "bfloat16",
            "max_model_len": 8192,
            "max_num_seqs": 8,
            "max_num_batched_tokens": 8192,
            "kv_cache_memory_bytes": KV_BYTES,
            "enable_prefix_caching": True,
            "load_format": "safetensors",
            "enable_auto_tool_choice": True,
            "tool_call_parser": "qwen3_coder",
            "reasoning_parser": "qwen3",
            "enable_request_id_headers": True,
            "language_model_only": True,
            "mamba_ssm_cache_dtype": "float32",
            "host": "127.0.0.1",
        }

    def server_argv(self, model_path: str = MODEL_PATH) -> list[str]:
        settings = self.serving_settings(model_path)
        argv = ["vllm", "serve", model_path]
        for field, value in settings.items():
            if field in {"model", "vllm_version", "gpu_count"}:
                continue
            argv.append("--" + field.replace("_", "-"))
            if type(value) is not bool:
                argv.append(str(value))
        return argv

    def pi_profile(self, *, max_requests: int, deadline_ms: int) -> dict[str, Any]:
        for value in (max_requests, deadline_ms):
            if type(value) is not int or value < 1:
                raise ValueError("positive_request_and_deadline_limits_required")
        return {
            "max_requests": max_requests,
            "max_output_tokens": 800,
            "max_total_tokens": max_requests * 8192,
            "context_window": 8192,
            "deadline_ms": deadline_ms,
            "thinking": self.thinking,
            "temperature": 0,
            "seed": 42,
            "compaction": False,
        }

    def verify_runtime(
        self,
        resolved: Mapping[str, Any],
        startup_log: str,
        model_path: str = MODEL_PATH,
    ) -> dict[str, Any]:
        """Compare owner-collected evidence; this is not a live hardware probe."""
        for field, expected in self.serving_settings(model_path).items():
            if (
                type(resolved.get(field)) is not type(expected)
                or resolved[field] != expected
            ):
                raise ValueError("runtime_setting_mismatch:" + field)
        messages = [
            line.rsplit("] ", 1)[-1].strip() for line in startup_log.splitlines()
        ]
        selection = (
            r"(?:Using|Selected) MarlinFP8(?:ScaledMM)?LinearKernel(?: for [\w.]+)?\.?"
        )
        contradictory = (
            r"\b(?:ERROR|CRITICAL|Traceback|failed|failure|fallback|falling back)\b|"
            r"\bout of memory\b|\bnot (?:using|selected) MarlinFP8\w*LinearKernel\b|"
            r"\b(?:Using|Selected) (?!MarlinFP8)\w*LinearKernel\b"
        )
        if not any(
            re.fullmatch(selection, message) for message in messages
        ) or re.search(contradictory, startup_log, re.IGNORECASE):
            raise ValueError("actual_marlin_fp8_kernel_evidence_missing")
        return {
            "status": "EVIDENCE_MATCH_NOT_LIVE_VERIFIED",
            "kernel": "Marlin FP8 W8A16",
            "settings": dict(resolved),
            "startup_log_sha256": hashlib.sha256(startup_log.encode()).hexdigest(),
        }


OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
