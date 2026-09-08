#!/usr/bin/env python3
"""Fail-fast validation for the co-located Vast graph-agent appliance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import tarfile
import time
from typing import Any


SOURCE_MEMBERS = {
    "docker/agentic-os-appliance/cache_control.sh",
    "docker/agentic-os-appliance/entrypoint.sh",
    "pyproject.toml",
    "scripts/benchmarks/bootstrap_agentic_os_appliance.sh",
    "scripts/benchmarks/run_agentic_os_colocated_e2e.sh",
    "scripts/benchmarks/validate_agentic_os_e2e_acceptance.py",
    "scripts/benchmarks/validate_agentic_os_one_case_gate.py",
}
DOZER_EXECUTABLES = {
    "opt/java/openjdk/bin/java",
    "startup/docker-entrypoint.sh",
    "var/lib/neo4j/bin/cypher-shell",
    "var/lib/neo4j/bin/neo4j",
    "var/lib/neo4j/bin/neo4j-admin",
}
DOZER_ALLOWED_ROOTS = {"opt", "startup", "var"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(*args: str, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _run_ephemeral_container(
    image: str,
    probe_script: str,
    *,
    mounts: tuple[tuple[Path, str], ...] = (),
    timeout_s: int = 180,
) -> subprocess.CompletedProcess[str]:
    """Run an exact-image probe and guarantee removal after client timeout."""

    name = f"seocho-appliance-preflight-{os.getpid()}-{time.monotonic_ns()}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--stop-timeout",
        "5",
        "--entrypoint",
        "/bin/bash",
    ]
    for local, remote in mounts:
        command.extend(("--volume", f"{local.resolve()}:{remote}:ro"))
    command.extend((image, "-lc", probe_script))
    try:
        return _run(*command, timeout_s=timeout_s)
    finally:
        _run("docker", "rm", "-f", name, timeout_s=30)


def _check_source_bundle(source_bundle: Path, bootstrap: Path) -> dict[str, Any]:
    with tarfile.open(source_bundle, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        missing = sorted(SOURCE_MEMBERS - members.keys())
        archived_bootstrap = archive.extractfile(
            members["scripts/benchmarks/bootstrap_agentic_os_appliance.sh"]
        )
        if archived_bootstrap is None:
            raise RuntimeError("could not read archived bootstrap")
        archived_digest = hashlib.sha256(archived_bootstrap.read()).hexdigest()
        non_runnable_shells = sorted(
            name
            for name, member in members.items()
            if name.endswith(".sh") and member.isfile() and not member.mode & 0o111
        )
    standalone_digest = _sha256(bootstrap)
    return {
        "passed": not missing and not non_runnable_shells and archived_digest == standalone_digest,
        "missing_members": missing,
        "non_runnable_shells": non_runnable_shells,
        "archive_bootstrap_sha256": archived_digest,
        "standalone_bootstrap_sha256": standalone_digest,
    }


def _check_dozer_runtime(dozer_runtime: Path) -> dict[str, Any]:
    with tarfile.open(dozer_runtime, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        roots = sorted({name.split("/", 1)[0] for name in members if name})
        missing = sorted(DOZER_EXECUTABLES - members.keys())
        non_executable = sorted(
            name for name in DOZER_EXECUTABLES
            if name in members and not members[name].mode & 0o111
        )
    unexpected_roots = sorted(set(roots) - DOZER_ALLOWED_ROOTS)
    return {
        "passed": not missing and not non_executable and not unexpected_roots,
        "archive_roots": roots,
        "unexpected_roots": unexpected_roots,
        "missing_executables": missing,
        "non_executable": non_executable,
    }


def _check_vllm_image(image: str) -> dict[str, Any]:
    inspected = _run("docker", "image", "inspect", image, timeout_s=60)
    if inspected.returncode != 0:
        return {"passed": False, "failure": "image_not_local"}
    payload = json.loads(inspected.stdout)[0]
    config = payload.get("Config") or {}
    probe_script = """
set -eu
command -v bash
command -v python3
command -v vllm
python3 --version
python3 -c 'import importlib.metadata as m; print(m.version("vllm"))'
python3 -c 'import json; from vllm.tool_parsers import ToolParserManager; print(json.dumps(sorted(ToolParserManager.list_registered())))'
""".strip()
    probe = _run_ephemeral_container(image, probe_script, timeout_s=180)
    parser_names: list[str] = []
    if probe.returncode == 0 and probe.stdout.splitlines():
        try:
            parser_names = json.loads(probe.stdout.splitlines()[-1])
        except json.JSONDecodeError:
            parser_names = []
    required_parser = "qwen3_coder"
    machine = platform.machine().lower()
    expected_architectures = {"amd64", "x86_64"} if machine in {"amd64", "x86_64"} else {machine}
    return {
        "passed": (
            probe.returncode == 0
            and required_parser in parser_names
            and str(payload.get("Architecture", "")).lower() in expected_architectures
        ),
        "image_id": payload.get("Id"),
        "architecture": payload.get("Architecture"),
        "entrypoint": config.get("Entrypoint"),
        "cmd": config.get("Cmd"),
        "probe_exit_code": probe.returncode,
        "probe_stdout": probe.stdout.splitlines(),
        "probe_stderr_tail": probe.stderr.splitlines()[-20:],
        "tool_parsers": parser_names,
        "required_tool_parser": required_parser,
    }


def _check_combined_dozer_runtime(image: str, dozer_runtime: Path) -> dict[str, Any]:
    probe_script = """
set -euo pipefail
umask 077
mkdir -p /data /logs
tar -xzf /workspace/dozer-runtime.tar.gz -C /
export JAVA_HOME=/opt/java/openjdk
export NEO4J_HOME=/var/lib/neo4j
export NEO4J_EDITION=community
export PATH="$NEO4J_HOME/bin:$JAVA_HOME/bin:$PATH"
mkdir -p "$NEO4J_HOME/plugins" "$NEO4J_HOME/run"
chown -R 0:0 "$NEO4J_HOME/conf" "$NEO4J_HOME/plugins" "$NEO4J_HOME/run" /data /logs
chmod 0750 "$NEO4J_HOME/conf" "$NEO4J_HOME/plugins" "$NEO4J_HOME/run" /data /logs
find "$NEO4J_HOME/conf" -type f -exec chmod 0600 {} +
cat >> "$NEO4J_HOME/conf/neo4j.conf" <<'EOF'

server.memory.heap.initial_size=512m
server.memory.heap.max_size=512m
server.memory.pagecache.size=512m
EOF
neo4j-admin server validate-config
java -version
neo4j version
cypher-shell --version
neo4j-admin dbms set-initial-password --require-password-change=false preflight-only-password >/dev/null
neo4j console >/tmp/dozer-preflight.stdout 2>/tmp/dozer-preflight.stderr &
dozer_pid=$!
cleanup() {
  kill "$dozer_pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    kill -0 "$dozer_pid" 2>/dev/null || break
    sleep 1
  done
  kill -9 "$dozer_pid" 2>/dev/null || true
  wait "$dozer_pid" 2>/dev/null || true
}
trap cleanup EXIT
ready=0
for _ in $(seq 1 90); do
  if cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p preflight-only-password 'RETURN 1' >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$dozer_pid" 2>/dev/null; then
    tail -80 /tmp/dozer-preflight.stderr >&2
    exit 3
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  tail -80 /tmp/dozer-preflight.stderr >&2
  exit 4
fi
cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p preflight-only-password --format plain \
  "SHOW DATABASES YIELD name,currentStatus WHERE name = 'neo4j' RETURN name,currentStatus"
cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p preflight-only-password --format plain \
  "SHOW SETTINGS YIELD name,value WHERE name STARTS WITH 'server.memory' OR name STARTS WITH 'server.directories' RETURN name,value ORDER BY name"
exit 0
""".strip()
    probe = _run_ephemeral_container(
        image,
        probe_script,
        mounts=((dozer_runtime, "/workspace/dozer-runtime.tar.gz"),),
        timeout_s=180,
    )
    return {
        "passed": probe.returncode == 0,
        "exit_code": probe.returncode,
        "stdout": probe.stdout.splitlines(),
        "stderr_tail": probe.stderr.splitlines()[-80:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", required=True, type=Path)
    parser.add_argument("--bootstrap", required=True, type=Path)
    parser.add_argument("--dozer-runtime", required=True, type=Path)
    parser.add_argument("--vllm-image", required=True)
    parser.add_argument("--receipt-out", required=True, type=Path)
    args = parser.parse_args()

    checks: dict[str, dict[str, Any]] = {}
    check_functions = {
        "source_bundle": lambda: _check_source_bundle(args.source_bundle, args.bootstrap),
        "dozer_runtime": lambda: _check_dozer_runtime(args.dozer_runtime),
        "vllm_image": lambda: _check_vllm_image(args.vllm_image),
        "combined_dozer_runtime": lambda: _check_combined_dozer_runtime(
            args.vllm_image, args.dozer_runtime
        ),
    }
    for name, check in check_functions.items():
        try:
            checks[name] = check()
        except Exception as exc:
            checks[name] = {
                "passed": False,
                "failure_class": type(exc).__name__,
                "failure_message": str(exc),
            }
    passed = all(bool(check.get("passed")) for check in checks.values())
    receipt = {
        "schema_version": "agentic_os.appliance_preflight.v1",
        "created_unix_s": time.time(),
        "passed": passed,
        "inputs": {
            "source_bundle": str(args.source_bundle.resolve()),
            "source_bundle_sha256": _sha256(args.source_bundle),
            "bootstrap": str(args.bootstrap.resolve()),
            "bootstrap_sha256": _sha256(args.bootstrap),
            "dozer_runtime": str(args.dozer_runtime.resolve()),
            "dozer_runtime_sha256": _sha256(args.dozer_runtime),
            "vllm_image": args.vllm_image,
        },
        "checks": checks,
    }
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
