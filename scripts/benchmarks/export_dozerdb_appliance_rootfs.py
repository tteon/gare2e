#!/usr/bin/env python3
"""Export the pinned DozerDB runtime files for a no-Docker Vast appliance.

Vast's normal vLLM offers run a container without a Docker daemon.  This helper
extracts only the tested DozerDB distribution, bundled JRE and upstream startup
script from the pinned local image.  It never exports a mutable database store;
the graph is loaded from a separately versioned source artifact on the target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from typing import Any


DEFAULT_IMAGE = "graphstack/dozerdb:5.26.3.0"
_PATHS = ("/var/lib/neo4j", "/opt/java/openjdk", "/startup")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _image_digest(image: str) -> str:
    result = _run("docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}")
    values = json.loads(result.stdout)
    if not isinstance(values, list) or not values:
        raise RuntimeError(f"image has no immutable RepoDigest: {image}")
    return str(values[0])


def export_rootfs(*, image: str, output: Path) -> dict[str, Any]:
    """Create a deterministic gzip tarball and return a content-free receipt."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite appliance rootfs: {output}")
    image_digest = _image_digest(image)
    container_id = _run("docker", "create", image).stdout.strip()
    try:
        with tempfile.TemporaryDirectory(prefix="seocho-dozerdb-rootfs-") as raw_stage:
            stage = Path(raw_stage)
            for source in _PATHS:
                destination_parent = stage / Path(source.lstrip("/")).parent
                destination_parent.mkdir(parents=True, exist_ok=True)
                _run("docker", "cp", f"{container_id}:{source}", str(destination_parent))
            with tarfile.open(output, "x:gz", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(stage.rglob("*")):
                    archive.add(path, arcname=str(path.relative_to(stage)), recursive=False)
    finally:
        _run("docker", "rm", "-f", container_id)
    return {
        "schema_version": "agentic_os.dozerdb_appliance_rootfs.v1",
        "image": image,
        "image_digest": image_digest,
        "archive": output.name,
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "paths": list(_PATHS),
        "mutable_graph_store_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    args = parser.parse_args()
    receipt = export_rootfs(image=args.image, output=args.output)
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    if args.receipt_out.exists():
        raise FileExistsError(f"refusing to overwrite receipt: {args.receipt_out}")
    args.receipt_out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"archive": str(args.output), "bytes": receipt["bytes"], "sha256": receipt["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
