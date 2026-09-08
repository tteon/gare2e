#!/usr/bin/env python3
"""Create a source-only, checksum-addressed bundle for a Vast appliance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Any


DEFAULT_INCLUDE = (
    "src",
    # The GTSQA lane runners import ``runtime.policy`` from the repo root; a
    # bundle without it fails every GTSQA gate cell in ~2 s (incident V16).
    "runtime",
    "scripts/benchmarks",
    "pyproject.toml",
    "README.md",
    "docker/agentic-os-appliance",
)
_EXCLUDED_PARTS = {".git", ".seocho", ".venv", "__pycache__", "node_modules", "data", "outputs", "logs"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(raw: str) -> Path:
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"bundle path must be a safe repo-relative path: {raw}")
    return Path(candidate)


def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    if any(part in _EXCLUDED_PARTS for part in PurePosixPath(info.name).parts):
        return None
    if info.name.endswith((".pyc", ".pyo")):
        return None
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    # Vast executes the staged shell entrypoints from the archive.  Preserve a
    # deterministic runnable contract even when a local checkout was created
    # with conservative 0644 modes (for example by an artifact restore).
    if info.isfile() and info.name.endswith(".sh"):
        info.mode = 0o755
    return info


def create_bundle(*, repo_root: Path, output: Path, include: tuple[str, ...]) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite bundle: {output}")
    sources: list[Path] = []
    for raw in include:
        relative = _safe_relative(raw)
        path = repo_root / relative
        if not path.exists():
            raise FileNotFoundError(f"bundle input does not exist: {relative}")
        sources.append(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "x:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in sources:
            archive.add(path, arcname=str(path.relative_to(repo_root)), filter=_filter)
    return {
        "schema_version": "agentic_os.appliance_source_bundle.v1",
        "archive": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "include": list(include),
        "repo_root": repo_root.name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    parser.add_argument("--include", action="append", default=[])
    args = parser.parse_args()
    include = tuple(args.include) or DEFAULT_INCLUDE
    receipt = create_bundle(repo_root=args.repo_root.resolve(), output=args.output, include=include)
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    if args.receipt_out.exists():
        raise FileExistsError(f"refusing to overwrite receipt: {args.receipt_out}")
    args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(args.output), "bytes": receipt["bytes"], "sha256": receipt["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
