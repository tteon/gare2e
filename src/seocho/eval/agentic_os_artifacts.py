"""Immutable, content-free artifact contracts for AgenticOS graph experiments."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


CORRELATION_FIELDS = (
    "run_id",
    "case_id",
    "arm",
    "seed",
    "repetition",
    "cache_phase",
    "role",
)


class ArtifactPrivacyError(RuntimeError):
    """A telemetry artifact contains a deliberately injected content sentinel."""


def canonical_digest(value: Any) -> str:
    """Return a stable SHA-256 for a JSON-serializable experiment input."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class Correlation:
    run_id: str
    case_id: str
    arm: str
    repetition: int
    cache_phase: str
    role: str
    seed: int = 0

    def receipt_fields(self) -> dict[str, Any]:
        if self.repetition < 0 or self.seed < 0:
            raise ValueError("seed and repetition must be non-negative")
        if self.cache_phase not in {"cold", "warm", "none"}:
            raise ValueError("cache_phase must be cold, warm, or none")
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "arm": self.arm,
            "seed": self.seed,
            "repetition": self.repetition,
            "cache_phase": self.cache_phase,
            "role": self.role,
        }


def write_manifest(path: str | Path, manifest: Mapping[str, Any]) -> str:
    """Write a manifest once; overwriting a differing manifest is forbidden."""
    target = Path(path)
    body = json.dumps(dict(manifest), indent=2, sort_keys=True) + "\n"
    digest = canonical_digest(manifest)
    if target.exists() and target.read_text(encoding="utf-8") != body:
        raise FileExistsError(f"immutable manifest already differs: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return digest


def append_jsonl(path: str | Path, receipt: Mapping[str, Any]) -> None:
    """Append a correlated content-free receipt after schema validation."""
    _append_correlated_jsonl(path, receipt)


def append_private_jsonl(path: str | Path, record: Mapping[str, Any]) -> None:
    """Append a correlated private record under a caller-owned ignored path.

    This separate name prevents answer/evidence/error payloads from being
    mistaken for content-free telemetry while retaining exact judge/trace join
    keys.
    """
    _append_correlated_jsonl(path, record)


def _append_correlated_jsonl(path: str | Path, record: Mapping[str, Any]) -> None:
    missing = [field for field in CORRELATION_FIELDS if field not in record]
    if missing:
        raise ValueError(f"receipt missing correlation fields: {', '.join(missing)}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), sort_keys=True) + "\n")


def require_fresh_outputs(paths: Iterable[str | Path | None]) -> None:
    """Fail closed before a run can append to or overwrite prior evidence."""
    occupied = [
        str(path)
        for raw in paths
        if raw is not None
        for path in (Path(raw),)
        if path.exists() and (not path.is_file() or path.stat().st_size > 0)
    ]
    if occupied:
        raise FileExistsError(
            "run outputs already exist; choose a fresh run root: " + ", ".join(occupied)
        )


def scan_for_content_leak(paths: Iterable[str | Path], sentinels: Iterable[str]) -> dict[str, list[str]]:
    """Return files containing deliberately injected private-content sentinels."""
    needles = tuple(value for value in sentinels if value)
    findings: dict[str, list[str]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found = [needle for needle in needles if needle in text]
        if found:
            findings[str(path)] = found
    return findings


def assert_content_free(paths: Iterable[str | Path], sentinels: Iterable[str]) -> dict[str, Any]:
    """Validate telemetry artifacts before a run is admitted to analysis.

    This is intentionally applied only to content-free artifacts (receipts,
    sampler CSVs, manifests and traces), never to the private prediction file
    needed by the evaluator.  Callers inject per-run canary strings into raw
    prompts/tool observations; their appearance here is a hard privacy gate.
    """
    normalized_paths = tuple(Path(path) for path in paths)
    normalized_sentinels = tuple(value for value in sentinels if value)
    findings = scan_for_content_leak(normalized_paths, normalized_sentinels)
    report = {
        "schema_version": "agentic_os.artifact_privacy_validation.v1",
        "checked_files": [str(path) for path in normalized_paths if path.is_file()],
        "sentinel_count": len(normalized_sentinels),
        "passed": not findings,
        "findings": findings,
    }
    if findings:
        names = ", ".join(sorted(findings))
        raise ArtifactPrivacyError(f"content sentinel leaked into telemetry artifact(s): {names}")
    return report


__all__ = [
    "ArtifactPrivacyError",
    "CORRELATION_FIELDS",
    "Correlation",
    "append_jsonl",
    "assert_content_free",
    "canonical_digest",
    "require_fresh_outputs",
    "scan_for_content_leak",
    "write_manifest",
]
