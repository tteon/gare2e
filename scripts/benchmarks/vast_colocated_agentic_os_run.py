#!/usr/bin/env python3
"""Provision, stage, execute, collect, checksum, and destroy one Vast appliance.

This is deliberately a narrow, fail-closed lifecycle for the paper baseline.
It stages inputs *before* the remote command, performs no SSH metric scraping,
copies only final artifacts, and always attempts instance destruction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import sys
import time
from typing import Any
from urllib.parse import urlparse


class _PartialArtifacts(RuntimeError):
    """Bulk copy failed after the essential receipts were verified; teardown proceeds."""


def _run(*args: str, check: bool = True, timeout_s: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True, timeout=timeout_s)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_private_log(path: Path, content: str) -> None:
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(content, encoding="utf-8")


def _write_lifecycle_checkpoint(path: Path, lifecycle: dict[str, Any], state: str) -> None:
    lifecycle["state"] = state
    lifecycle["checkpoint_unix_s"] = time.time()
    path.write_text(json.dumps(lifecycle, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("offers", "instances"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        return [dict(payload)]
    return []


def _offer_number(row: dict[str, Any], name: str, *, default: float) -> float:
    try:
        return float(row.get(name, default))
    except (TypeError, ValueError):
        return default


def _offer_is_admitted(
    row: dict[str, Any],
    *,
    max_dph: float,
    min_num_gpus: int,
    min_gpu_total_ram: float,
    min_effective_cpu: float,
    min_cpu_ram_mb: float,
    min_reliability: float,
    disk_gb: int,
    gpu_name_pattern: re.Pattern[str] | None,
    require_nvlink: bool = False,
) -> bool:
    """Fail closed when a live offer omits a required numeric resource.

    ``require_nvlink`` admits only offers whose GPUs report an NVLink fabric
    (``bw_nvlink`` > 0); PCIe-only pairs (RTX PRO 6000, A100 PCIe) are refused.
    """
    return (
        (not require_nvlink or _offer_number(row, "bw_nvlink", default=0) > 0)
        and
        _offer_number(row, "dph_total", default=float("inf")) <= max_dph
        and _offer_number(row, "num_gpus", default=0) >= min_num_gpus
        and _offer_number(row, "gpu_total_ram", default=0) >= min_gpu_total_ram
        and _offer_number(row, "cpu_cores_effective", default=0) >= min_effective_cpu
        and _offer_number(row, "cpu_ram", default=0) >= min_cpu_ram_mb
        and _offer_number(row, "reliability", default=0) >= min_reliability
        and _offer_number(row, "disk_space", default=0) >= disk_gb
        and (
            gpu_name_pattern is None
            or bool(gpu_name_pattern.search(str(row.get("gpu_name", ""))))
        )
    )


class _CostWatchdogProcess:
    def __init__(
        self,
        *,
        process: subprocess.Popen[str],
        receipt_path: Path,
        stop_path: Path,
        stdout_path: Path,
        stderr_path: Path,
        deadline_unix_s: float,
    ) -> None:
        self.process = process
        self.receipt_path = receipt_path
        self.stop_path = stop_path
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.deadline_unix_s = deadline_unix_s


def _private_stream(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("w", encoding="utf-8")
    path.chmod(0o600)
    return stream


def _start_cost_watchdog(
    *,
    instance_id: int,
    deadline_unix_s: float,
    local_artifacts: Path,
    poll_s: float = 5.0,
) -> _CostWatchdogProcess:
    """Launch an SSH-independent local deadline process in its own session."""
    watchdog_script = Path(__file__).with_name("vast_instance_watchdog.py").resolve()
    if not watchdog_script.is_file():
        raise FileNotFoundError(f"Vast cost watchdog is missing: {watchdog_script}")
    receipt_path = local_artifacts / "vast-cost-watchdog.json"
    stop_path = local_artifacts / "vast-cost-watchdog.stop.private.json"
    stdout_path = local_artifacts / "vast-cost-watchdog.stdout.private.log"
    stderr_path = local_artifacts / "vast-cost-watchdog.stderr.private.log"
    # A run directory is expected to be unique. Remove only watchdog-owned
    # markers so a stale stop request can never disarm a newly created instance.
    stop_path.unlink(missing_ok=True)
    receipt_path.unlink(missing_ok=True)
    command = [
        sys.executable,
        str(watchdog_script),
        "--instance-id",
        str(instance_id),
        "--deadline-unix-s",
        f"{deadline_unix_s:.6f}",
        "--receipt",
        str(receipt_path),
        "--stop-file",
        str(stop_path),
        "--poll-s",
        str(poll_s),
    ]
    stdout = _private_stream(stdout_path)
    stderr = _private_stream(stderr_path)
    try:
        process = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )
    finally:
        stdout.close()
        stderr.close()
    return _CostWatchdogProcess(
        process=process,
        receipt_path=receipt_path,
        stop_path=stop_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        deadline_unix_s=deadline_unix_s,
    )


def _read_watchdog_receipt(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, dict) else None


def _stop_cost_watchdog(
    watchdog: _CostWatchdogProcess,
    *,
    parent_destroy_verified_absent: bool,
    wait_timeout_s: float = 40.0,
) -> dict[str, Any]:
    """Stop and reap only after parent teardown proves the instance absent."""
    process = watchdog.process
    summary: dict[str, Any] = {
        "deadline_unix_s": watchdog.deadline_unix_s,
        "receipt_file": watchdog.receipt_path.name,
        "stdout_file": watchdog.stdout_path.name,
        "stderr_file": watchdog.stderr_path.name,
        "parent_destroy_verified_absent": parent_destroy_verified_absent,
    }
    return_code = process.poll()
    if return_code is None and not parent_destroy_verified_absent:
        summary.update(
            {
                "process_exit_code": None,
                "left_armed": True,
                "shutdown_verified": False,
                "terminal_status": "left_armed_after_parent_teardown_failure",
            }
        )
        return summary

    if return_code is None:
        _write_private_log(
            watchdog.stop_path,
            json.dumps(
                {
                    "schema_version": "agentic_os.vast_watchdog_stop.v1",
                    "parent_destroy_verified_absent": True,
                    "requested_unix_s": time.time(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        try:
            return_code = process.wait(timeout=wait_timeout_s)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                return_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=5)
            summary["forced_termination"] = True
    else:
        # poll() observes completion but wait() performs the actual reap.
        return_code = process.wait(timeout=0)

    receipt = _read_watchdog_receipt(watchdog.receipt_path)
    terminal_status = str((receipt or {}).get("terminal_status", "missing_receipt"))
    accepted_terminal = terminal_status in {
        "parent_teardown_verified_absent",
        "already_absent_at_deadline",
        "deadline_destroyed",
    }
    summary.update(
        {
            "process_exit_code": return_code,
            "left_armed": False,
            "receipt_preserved": receipt is not None,
            "terminal_status": terminal_status,
            "deadline_destroy_triggered": terminal_status == "deadline_destroyed",
            "shutdown_verified": bool(
                receipt is not None
                and return_code == 0
                and accepted_terminal
                and receipt.get("destroy_verified_absent") is True
            ),
        }
    )
    if receipt is not None:
        summary["receipt"] = receipt
    return summary


def _safe_remote_path(raw: str) -> str:
    path = PurePosixPath(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("remote paths must be absolute and traversal-free")
    return str(path)


def _parse_upload(raw: str) -> tuple[Path, str]:
    local, marker, remote = raw.partition("=")
    if not marker:
        raise argparse.ArgumentTypeError("--upload must be LOCAL_PATH=/absolute/remote/path")
    path = Path(local).resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"upload source is not a file: {path}")
    try:
        return path, _safe_remote_path(remote)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _wait_running(instance_id: int, timeout_s: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            result = _run(
                "vastai", "show", "instance", str(instance_id), "--raw",
                check=False, timeout_s=60,
            )
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                raise TimeoutError("Vast instance status checks timed out") from None
            time.sleep(10)
            continue
        try:
            rows = _rows(json.loads(result.stdout or "null"))
        except json.JSONDecodeError:
            rows = []
        status = str(rows[0].get("actual_status", "")) if rows else ""
        if status == "running":
            return rows[0]
        # Vast can briefly report ``offline`` while the direct SSH endpoint is
        # being attached even though the instance later becomes running.
        if status in {"exited", "unknown_error"}:
            raise RuntimeError(f"Vast instance terminal during startup: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError("Vast instance did not reach running before deadline")
        time.sleep(10)


def _ssh_prefix(instance_id: int) -> list[str]:
    """Resolve Vast's direct SSH helper into argv without invoking a shell."""
    result = _run("vastai", "ssh-url", str(instance_id), timeout_s=60)
    raw = result.stdout.strip()
    # Current Vast CLI emits an SSH URI, e.g. ssh://root@host:port.  Older
    # versions emitted an executable command, so retain that compatibility.
    if raw.startswith("ssh://"):
        parsed = urlparse(raw)
        if not parsed.hostname or not parsed.port or not parsed.username:
            raise RuntimeError("vastai ssh-url returned an incomplete SSH URI")
        return [
            "ssh", "-p", str(parsed.port),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            f"{parsed.username}@{parsed.hostname}",
        ]
    parts = shlex.split(raw)
    if not parts or parts[0] != "ssh" or len(parts) < 2:
        raise RuntimeError("vastai ssh-url returned an unsupported command")
    return parts


def _ssh(prefix: list[str], remote_command: str, *, check: bool = True, timeout_s: int | None = None) -> subprocess.CompletedProcess[str]:
    return _run(*prefix, remote_command, check=check, timeout_s=timeout_s)


def _wait_ssh(ssh_prefix: list[str], timeout_s: int) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            result = _ssh(
                ssh_prefix,
                "true",
                check=False,
                timeout_s=15,
            )
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Vast direct SSH endpoint did not become ready")
        time.sleep(5)


def _scp_prefix(ssh_prefix: list[str]) -> list[str]:
    """Translate our direct-SSH argv into an SCP argv.

    Vast's ``copy`` helper currently tunnels an rsync daemon connection.  That
    path can remain in ``CLOSE_WAIT`` indefinitely after a provider-side
    disconnect, so staging and collection deliberately use the same direct
    SSH transport that is used for bootstrap and execution.
    """
    try:
        port = ssh_prefix[ssh_prefix.index("-p") + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError("SSH prefix has no port for SCP transfer") from exc
    return [
        "scp", "-P", port,
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
    ]


def _copy_to_remote(ssh_prefix: list[str], local: Path, remote: str) -> None:
    _run(
        *_scp_prefix(ssh_prefix), str(local), f"{ssh_prefix[-1]}:{remote}",
        timeout_s=1800,
    )



# Content-free essentials that must survive a slow or broken bulk copy (lab-s15:
# a UAE host pushed 27 MB in 30 min, the 1800 s scp timed out and the fail-closed
# destroy threw away the whole session's receipts).  Everything needed for the
# funnel / bottleneck / stage tables is small; the private logs are the bulk.
ESSENTIAL_GLOBS = (
    "queue-receipt.jsonl", "appliance-phases.private.tsv", "bootstrap-phases.private.tsv",
    "e2e/*.json", "otel/collector-spans.jsonl", "*discovery-serving.json",
    "run-*/pass-receipt.json", "run-*/otel-export-receipt.json", "run-*/otel/spans.jsonl",
    "run-*/otel/throughput.json", "run-*/otel/factors.json", "run-*/otel/*.csv",
    "session-summary/*.json", "session-summary/*.csv", "session-summary/*.md",
    "run-*/e2e/*/shard-*/attempts/*/replay.private.jsonl",
    "run-*/vllm-restart/*.json", "run-*/workload/*.csv", "run-*/workload/*.json", "run-*/workload/*.jsonl",
    "run-*/e2e/progress.tsv", "run-*/e2e/*/coordinator-*.json*", "run-*/e2e/*/shard-*/coordinator-*.json*",
    "run-*/e2e/*/shard-*/attempts/*/run-receipt.json", "run-*/e2e/*/shard-*/attempts/*/receipts.jsonl",
    "run-*/e2e/*/shard-*/attempts/*/manifest.json", "run-*/e2e/*/attempts/*/run-receipt.json",
    "run-*/e2e/*/attempts/*/receipts.jsonl", "run-*/e2e/*/attempts/*/manifest.json",
)


def _essentials_tar_command(remote_artifacts: str, archive: str) -> str:
    """Shell command that archives only the essential globs (missing globs are fine)."""
    quoted = " ".join(shlex.quote(g) for g in ESSENTIAL_GLOBS)
    return (
        f"cd {shlex.quote(remote_artifacts)} && set +f && shopt -s nullglob globstar 2>/dev/null; "
        f"files=$(for g in {quoted}; do ls -1d $g 2>/dev/null; done | sort -u); "
        f"tar -czf {shlex.quote(archive)} --ignore-failed-read $files && sha256sum -- {shlex.quote(archive)}"
    )


def _collect_essentials(ssh_prefix: list[str], remote_artifacts: str, local_root: Path, *, timeout_s: int) -> dict[str, Any]:
    """Archive + copy + verify + extract the essential receipts before the bulk copy."""
    archive = "/tmp/seocho-essentials.tgz"
    hashed = _ssh(ssh_prefix, _essentials_tar_command(remote_artifacts, archive), timeout_s=timeout_s)
    remote_digest = hashed.stdout.strip().split()[0] if hashed.stdout.strip() else ""
    local_archive = local_root / "essentials.tgz"
    _run(*_scp_prefix(ssh_prefix), f"{ssh_prefix[-1]}:{archive}", str(local_archive), timeout_s=timeout_s)
    local_digest = _sha256(local_archive)
    if not remote_digest or local_digest != remote_digest:
        raise RuntimeError("essential archive digest mismatch after copy")
    with tarfile.open(local_archive) as tar:
        members = [m for m in tar.getmembers() if not (m.name.startswith("/") or ".." in PurePosixPath(m.name).parts)]
        tar.extractall(local_root, members=members)  # noqa: S202 - members filtered above
    local_archive.unlink()
    return {"verified": True, "sha256": remote_digest, "files": len(members), "bytes": hashed.stdout and int(0) or 0}

def _copy_from_remote(ssh_prefix: list[str], remote_artifacts: str, local_root: Path, *, timeout_s: int = 1800) -> None:
    """Copy the remote artifact tree into ``local_root`` without a ``/.`` suffix.

    OpenSSH 8.9+ scp (SFTP mode) rejects ``host:/dir/.`` with
    ``unexpected filename: .`` (incident V13), so the directory is fetched into a
    private staging directory and its children are then moved into place.
    """
    staging = local_root / ".remote-incoming"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    _run(
        *_scp_prefix(ssh_prefix), "-r", f"{ssh_prefix[-1]}:{remote_artifacts}", str(staging),
        timeout_s=timeout_s,
    )
    fetched = staging / PurePosixPath(remote_artifacts).name
    if not fetched.is_dir():
        raise RuntimeError(f"scp did not produce the remote artifact directory: {fetched}")
    for child in fetched.iterdir():
        target = local_root / child.name
        if target.exists():
            raise RuntimeError(f"refusing to overwrite local artifact during collection: {target}")
        shutil.move(str(child), str(target))
    shutil.rmtree(staging)


def _verify_remote_uploads(
    ssh_prefix: list[str], uploads: list[tuple[Path, str]]
) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for local, remote in uploads:
        expected = _sha256(local)
        result = _ssh(
            ssh_prefix,
            f"sha256sum -- {shlex.quote(remote)}",
            timeout_s=120,
        )
        actual = result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else ""
        if actual != expected:
            raise RuntimeError(f"remote upload checksum mismatch: {local.name}")
        verified.append({"remote": remote, "sha256": actual})
    return verified


def _remote_checksum_map(ssh_prefix: list[str], remote_artifacts: str) -> dict[str, str]:
    command = f"cd {remote_artifacts} && find . -type f -print0 | sort -z | xargs -0 sha256sum"
    result = _ssh(ssh_prefix, command)
    checksums: dict[str, str] = {}
    for line in result.stdout.splitlines():
        digest, separator, path = line.partition("  ")
        if separator and len(digest) == 64:
            checksums[path.removeprefix("./")] = digest
    if not checksums:
        raise RuntimeError("remote artifact checksum receipt was empty")
    return checksums


def _match_local_artifact(root: Path, remote_relative: str) -> Path:
    exact = root / remote_relative
    if exact.is_file():
        return exact
    candidates = [path for path in root.rglob(Path(remote_relative).name) if path.is_file()]
    if len(candidates) != 1:
        raise RuntimeError(f"could not uniquely locate copied artifact: {remote_relative}")
    return candidates[0]


def _destroy_and_verify(instance_id: int) -> tuple[int, bool]:
    destroy_returncode = 124
    for _attempt in range(3):
        try:
            # ``vastai destroy instance`` prompts for confirmation unless ``-y``
            # is given; without it the call idles until the timeout (V12/V13
            # ``destroy_exit_code=124``) and the paid instance keeps running.
            result = _run(
                "vastai", "destroy", "instance", str(instance_id), "-y",
                check=False, timeout_s=30,
            )
            destroy_returncode = result.returncode
        except subprocess.TimeoutExpired:
            destroy_returncode = 124
        for _ in range(3):
            try:
                listing = _run(
                    "vastai", "show", "instances", "--raw",
                    check=False, timeout_s=20,
                )
                present = any(
                    int(row.get("id", -1)) == instance_id
                    for row in _rows(json.loads(listing.stdout or "null"))
                )
            except (ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
                present = True
            if not present:
                return destroy_returncode, True
            time.sleep(3)
    return destroy_returncode, False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", type=int, help="Optional exact offer ID; omit to select live candidate.")
    parser.add_argument("--image", required=True)
    parser.add_argument("--disk-gb", required=True, type=int)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--offer-query",
        default="rentable=true",
        help="Vast search predicate used to refresh/verify the short-lived offer ID.",
    )
    parser.add_argument("--max-dph", required=True, type=float)
    parser.add_argument("--min-num-gpus", type=int, default=1)
    parser.add_argument("--min-gpu-total-ram", type=float, default=0)
    parser.add_argument("--min-effective-cpu", type=float, default=0)
    parser.add_argument(
        "--min-cpu-ram-mb",
        type=float,
        default=0,
        help="Minimum Vast offer cpu_ram in MiB for co-located model, loader, and graph DB memory.",
    )
    parser.add_argument("--min-reliability", type=float, default=0)
    parser.add_argument("--gpu-name-regex", help="Optional case-insensitive GPU-name admission filter.")
    parser.add_argument("--require-nvlink", action="store_true", help="Admit only offers with an NVLink fabric between the GPUs (bw_nvlink > 0); lab rule since 2026-09-12.")
    parser.add_argument(
        "--exclude-host-id",
        action="append",
        type=int,
        default=[],
        help="Vast host_id values never admitted (hosts where TP=2 NCCL hung: V14 458442, lab-s1 3497).",
    )
    parser.add_argument("--startup-timeout-s", type=int, default=900)
    parser.add_argument("--max-runtime-s", type=int, required=True)
    parser.add_argument("--remote-command", required=True)
    parser.add_argument("--remote-artifacts", default="/artifacts")
    parser.add_argument("--collect-timeout-s", type=int, default=3600, help="per-attempt timeout for the essential archive copy and each bulk scp (lab-s15: 1800 s was too short from a slow host)")
    parser.add_argument("--collect-retries", type=int, default=1, help="extra bulk scp attempts after the first failure")
    parser.add_argument("--collect-bulk", choices=("full", "essential-only"), default="full", help="essential-only skips the bulk private-log copy (slow uplinks: lab-s15/s16 UAE host); the lifecycle records artifacts_partial")
    parser.add_argument("--local-artifacts", required=True, type=Path)
    parser.add_argument("--upload", action="append", type=_parse_upload, default=[])
    args = parser.parse_args()
    if (
        args.disk_gb < 20
        or args.max_dph <= 0
        or args.max_runtime_s < 1
        or args.min_cpu_ram_mb < 0
    ):
        parser.error("disk, price, and runtime limits must be positive")
    if not args.upload:
        parser.error("at least one immutable --upload is required")
    remote_artifacts = _safe_remote_path(args.remote_artifacts)
    args.local_artifacts.mkdir(parents=True, exist_ok=True)
    receipt_path = args.local_artifacts / "vast-colocated-lifecycle.json"
    lifecycle: dict[str, Any] = {
        "schema_version": "agentic_os.vast_colocated_lifecycle.v2",
        "requested_offer_id": args.offer_id,
        "image": args.image,
        "disk_gb": args.disk_gb,
        "max_dph": args.max_dph,
        "max_runtime_s": args.max_runtime_s,
        "min_cpu_ram_mb": args.min_cpu_ram_mb,
        "uploads": [
            {"local_name": local.name, "local_sha256": _sha256(local), "remote": remote}
            for local, remote in args.upload
        ],
        "started_unix_s": time.time(),
    }
    _write_lifecycle_checkpoint(receipt_path, lifecycle, "admission_pending")
    instance_id: int | None = None
    cost_watchdog: _CostWatchdogProcess | None = None
    failure: Exception | None = None
    try:
        # Offer IDs can disappear within seconds.  Query and select in this
        # process immediately before create rather than relying on a stale
        # candidate printed by a previous operator command.
        offers = _rows(json.loads(_run(
            "vastai", "search", "offers", args.offer_query,
            "-o", "dph_total", "--raw", timeout_s=120,
        ).stdout))
        gpu_name_pattern = re.compile(args.gpu_name_regex, flags=re.IGNORECASE) if args.gpu_name_regex else None
        candidates = [
            row
            for row in offers
            if _offer_is_admitted(
                row,
                max_dph=args.max_dph,
                min_num_gpus=args.min_num_gpus,
                min_gpu_total_ram=args.min_gpu_total_ram,
                min_effective_cpu=args.min_effective_cpu,
                min_cpu_ram_mb=args.min_cpu_ram_mb,
                min_reliability=args.min_reliability,
                require_nvlink=args.require_nvlink,
                disk_gb=args.disk_gb,
                gpu_name_pattern=gpu_name_pattern,
            )
        ]
        excluded = {int(value) for value in args.exclude_host_id}
        candidates = [row for row in candidates if int(row.get("host_id", -1)) not in excluded]
        offer = [row for row in candidates if int(row.get("id", -1)) == args.offer_id] if args.offer_id else candidates
        if args.offer_id is None:
            offer = sorted(offer, key=lambda row: float(row.get("dph_total", float("inf"))))[:1]
        if len(offer) != 1:
            raise RuntimeError("selected offer is absent or ambiguous")
        args.offer_id = int(offer[0]["id"])
        hourly = float(offer[0].get("dph_total", float("inf")))
        if hourly > args.max_dph:
            raise RuntimeError("offer exceeds cost ceiling")
        lifecycle["offer"] = {
            key: offer[0].get(key)
            for key in (
                "id",
                "gpu_name",
                "num_gpus",
                "gpu_total_ram",
                "cpu_cores_effective",
                "bw_nvlink",
                "cpu_ram",
                "disk_space",
                "dph_total",
                "reliability",
            )
        }
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "offer_admitted")
        create_requested_unix_s = time.time()
        lifecycle["create_requested_unix_s"] = create_requested_unix_s
        created = json.loads(_run(
            "vastai", "create", "instance", str(args.offer_id), "--image", args.image,
            "--disk", str(args.disk_gb), "--ssh", "--direct", "--label", args.label,
            "--onstart-cmd", "tail -f /dev/null", "--raw",
            timeout_s=120,
        ).stdout)
        instance_id = int(created.get("new_contract") or created.get("instance_id"))
        lifecycle["instance_id"] = instance_id
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "instance_created")
        watchdog_deadline_unix_s = create_requested_unix_s + args.max_runtime_s
        cost_watchdog = _start_cost_watchdog(
            instance_id=instance_id,
            deadline_unix_s=watchdog_deadline_unix_s,
            local_artifacts=args.local_artifacts,
        )
        lifecycle["cost_watchdog"] = {
            "state": "armed",
            "process_pid": cost_watchdog.process.pid,
            "deadline_unix_s": watchdog_deadline_unix_s,
            "receipt_file": cost_watchdog.receipt_path.name,
            "start_new_session": True,
            "scope": "local_process_independent_of_remote_ssh",
        }
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "cost_watchdog_started")
        lifecycle["startup"] = _wait_running(instance_id, args.startup_timeout_s)
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "provider_running")
        ssh_prefix = _ssh_prefix(instance_id)
        _wait_ssh(ssh_prefix, args.startup_timeout_s)
        lifecycle["ssh_control"] = "bootstrap_execute_checksum_only"
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "ssh_ready")
        staged_uploads: list[str] = []
        for local, remote in args.upload:
            parent = str(PurePosixPath(remote).parent)
            _ssh(ssh_prefix, f"mkdir -p {parent}")
            _copy_to_remote(ssh_prefix, local, remote)
            staged_uploads.append(remote)
            lifecycle["staged_uploads"] = staged_uploads
            _write_lifecycle_checkpoint(receipt_path, lifecycle, "staging")
        lifecycle["verified_uploads"] = _verify_remote_uploads(ssh_prefix, args.upload)
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "uploads_verified")
        started = time.monotonic()
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "remote_execution")
        completed = _ssh(ssh_prefix, args.remote_command, check=False, timeout_s=args.max_runtime_s)
        _write_private_log(
            args.local_artifacts / "remote-execution.stdout.private.log",
            completed.stdout,
        )
        _write_private_log(
            args.local_artifacts / "remote-execution.stderr.private.log",
            completed.stderr,
        )
        lifecycle["remote_execution"] = {
            "exit_code": completed.returncode,
            "wall_s": round(time.monotonic() - started, 3),
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        }
        if completed.returncode != 0:
            lifecycle["failure_class"] = "RemoteExecutionFailed"
            lifecycle["failure_message"] = (
                f"remote workload exited with status {completed.returncode}; "
                "inspect private stderr and collected phase logs"
            )
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "remote_execution_finished")
        # A bounded remote timeout or a failed late lane can still leave
        # valuable, self-describing partial receipts.  Preserve and checksum
        # them before destruction; the lifecycle return code remains nonzero.
        lifecycle["remote_execution"]["completed_successfully"] = completed.returncode == 0
        remote_checksums = _remote_checksum_map(ssh_prefix, remote_artifacts)
        lifecycle["remote_checksum_count"] = len(remote_checksums)
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "remote_artifacts_hashed")
        try:
            lifecycle["essential_copy"] = _collect_essentials(ssh_prefix, remote_artifacts, args.local_artifacts, timeout_s=args.collect_timeout_s)
        except Exception as exc:  # class only
            lifecycle["essential_copy"] = {"verified": False, "error_class": type(exc).__name__}
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "essential_artifacts_collected")
        bulk_error: str | None = None
        if args.collect_bulk == "essential-only":
            bulk_error = "SkippedByFlag"
            lifecycle["bulk_copy_attempts"] = 0
        for attempt in range(1, (args.collect_retries + 2) if bulk_error is None else 1):
            try:
                _copy_from_remote(ssh_prefix, remote_artifacts, args.local_artifacts, timeout_s=args.collect_timeout_s)
                bulk_error = None
                break
            except Exception as exc:  # class only
                bulk_error = type(exc).__name__
                lifecycle["bulk_copy_attempts"] = attempt
        if bulk_error is not None:
            lifecycle["bulk_copy_error_class"] = bulk_error
            if not (lifecycle.get("essential_copy") or {}).get("verified"):
                raise RuntimeError(f"artifact copy failed ({bulk_error}) and no essential copy was verified")
            lifecycle["artifact_copy_verified"] = False
            lifecycle["artifact_checksum_mismatches"] = []
            lifecycle["volatile_artifacts"] = []
            lifecycle["failure_class"] = lifecycle.get("failure_class") or "BulkArtifactCopyIncomplete"
            lifecycle["failure_message"] = lifecycle.get("failure_message") or (
                f"bulk artifact copy failed ({bulk_error}); essential receipts were copied and verified"
            )
            _write_lifecycle_checkpoint(receipt_path, lifecycle, "artifacts_partial")
            raise _PartialArtifacts(lifecycle["failure_message"])
        mismatches: list[str] = []
        for relative, remote_digest in remote_checksums.items():
            local_path = _match_local_artifact(args.local_artifacts, relative)
            if _sha256(local_path) != remote_digest:
                mismatches.append(relative)
        # A file whose remote digest changed between the two remote passes was
        # still being appended (V16: vLLM stdout after the workload ended).
        # That is a volatile log, not a corrupt copy: record both digests and
        # keep the run.  A stable remote digest that still differs locally is
        # a real transfer fault and remains fatal.
        volatile: list[dict[str, str]] = []
        if mismatches:
            rehashed = _remote_checksum_map(ssh_prefix, remote_artifacts)
            for relative in list(mismatches):
                first = remote_checksums.get(relative)
                second = rehashed.get(relative)
                if second is not None and second != first:
                    volatile.append({"path": relative, "first_sha256": first or "", "second_sha256": second})
                    mismatches.remove(relative)
        lifecycle["artifact_copy_verified"] = not mismatches
        lifecycle["artifact_checksum_mismatches"] = mismatches
        lifecycle["volatile_artifacts"] = volatile
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "artifacts_collected")
        if mismatches:
            raise RuntimeError("artifact checksums did not match after copy")
    except Exception as exc:  # persist failure class without secrets or remote output content
        failure = exc
        lifecycle["failure_class"] = type(exc).__name__
        lifecycle["failure_message"] = str(exc)
        # Persist before teardown: a provider-side destroy can itself block,
        # and the operator must still see the original admission/SSH failure.
        lifecycle["failure_checkpoint_unix_s"] = time.time()
        _write_lifecycle_checkpoint(receipt_path, lifecycle, "failed_before_teardown")
    finally:
        if instance_id is not None:
            _write_lifecycle_checkpoint(receipt_path, lifecycle, "teardown")
            try:
                exit_code, absent = _destroy_and_verify(instance_id)
            except Exception as exc:  # watchdog must remain armed on parent failure
                exit_code, absent = 125, False
                lifecycle["teardown_failure_class"] = type(exc).__name__
                lifecycle["teardown_failure_message"] = str(exc)
            lifecycle["destroy_exit_code"] = exit_code
            lifecycle["destroy_verified_absent"] = absent
        watchdog_shutdown_ok = instance_id is None
        watchdog_deadline_triggered = False
        if cost_watchdog is not None:
            try:
                watchdog_summary = _stop_cost_watchdog(
                    cost_watchdog,
                    parent_destroy_verified_absent=(
                        lifecycle.get("destroy_verified_absent") is True
                    ),
                )
            except Exception as exc:  # retain teardown evidence even if reaping fails
                watchdog_summary = {
                    "shutdown_verified": False,
                    "left_armed": cost_watchdog.process.poll() is None,
                    "failure_class": type(exc).__name__,
                    "failure_message": str(exc),
                }
            lifecycle["cost_watchdog"] = {
                **dict(lifecycle.get("cost_watchdog") or {}),
                **watchdog_summary,
            }
            watchdog_shutdown_ok = watchdog_summary.get("shutdown_verified") is True
            watchdog_deadline_triggered = (
                watchdog_summary.get("deadline_destroy_triggered") is True
            )
            _write_lifecycle_checkpoint(receipt_path, lifecycle, "cost_watchdog_reconciled")
        if instance_id is not None and lifecycle.get("destroy_verified_absent") is not True:
            # Unresolved billing is more important than the workload failure
            # that led us into teardown. The independent watchdog remains the
            # final recovery path whenever its process is still live.
            lifecycle["failure_class"] = "TeardownVerificationFailed"
            lifecycle["failure_message"] = (
                "Vast instance absence was not verified; inspect the independent cost watchdog receipt"
            )
        elif watchdog_deadline_triggered:
            lifecycle.setdefault("failure_class", "CostWatchdogDeadlineReached")
            lifecycle.setdefault(
                "failure_message",
                "independent cost watchdog reached the hard billing deadline",
            )
        elif cost_watchdog is not None and not watchdog_shutdown_ok:
            lifecycle.setdefault("failure_class", "CostWatchdogReconciliationFailed")
            lifecycle.setdefault(
                "failure_message",
                "cost watchdog did not preserve a verified terminal receipt",
            )
        lifecycle["finished_unix_s"] = time.time()
        remote_success = bool(
            (lifecycle.get("remote_execution") or {}).get("completed_successfully")
        )
        final_state = (
            "complete"
            if (
                failure is None
                and remote_success
                and lifecycle.get("destroy_verified_absent") is True
                and watchdog_shutdown_ok
                and not watchdog_deadline_triggered
            )
            else "failed"
        )
        _write_lifecycle_checkpoint(receipt_path, lifecycle, final_state)
    return 0 if final_state == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
