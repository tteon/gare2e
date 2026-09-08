#!/usr/bin/env python3
"""Cost-safe Vast lifecycle: provision, run, checksum/copy, destroy, verify."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any


def _run(
    *args: str,
    check: bool = True,
    timeout_s: int | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        input=input_text,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("offers", "instances"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, dict)]
        return [dict(payload)]
    return []


def _instance_present(payload: Any, instance_id: int) -> bool:
    return any(
        int(row.get("id") or row.get("instance_id") or row.get("contract_id") or -1)
        == instance_id
        for row in _rows(payload)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offer-id", required=True, type=int)
    parser.add_argument("--image", required=True)
    parser.add_argument("--disk", required=True, type=int)
    parser.add_argument("--label", required=True)
    parser.add_argument("--remote-command", required=True)
    parser.add_argument("--remote-artifacts", required=True, help="Remote directory inside container")
    parser.add_argument("--local-artifacts", required=True, type=Path)
    parser.add_argument("--startup-wait-s", type=int, default=45)
    parser.add_argument("--startup-timeout-s", type=int, default=600)
    parser.add_argument("--max-runtime-s", type=int, default=3600)
    parser.add_argument("--max-dph", type=float, required=True)
    args = parser.parse_args()
    if args.max_runtime_s < 1 or args.startup_wait_s < 0 or args.startup_timeout_s < 1:
        raise ValueError("runtime limits must be positive")
    if args.max_dph <= 0:
        raise ValueError("--max-dph must be positive")
    args.local_artifacts.mkdir(parents=True, exist_ok=True)
    instance_id: int | None = None
    lifecycle: dict[str, object] = {
        "schema_version": "seocho.agentic_os.vast_lifecycle.v1",
        "offer_id": args.offer_id,
        "label": args.label,
        "remote_artifacts": args.remote_artifacts,
        "started_unix_s": time.time(),
        "destroy_attempted": False,
        "configured_cost_ceiling_usd": round(
            args.max_dph
            * (args.startup_wait_s + args.startup_timeout_s + args.max_runtime_s + 30)
            / 3600,
            4,
        ),
    }
    lifecycle_path = args.local_artifacts / "vast-lifecycle.json"
    try:
        offer_result = _run(
            "vastai", "search", "offers", f"id={args.offer_id}", "-o", "dph", "--raw",
        )
        offer_rows = _rows(json.loads(offer_result.stdout))
        if len(offer_rows) != 1:
            raise RuntimeError("selected Vast offer is absent or ambiguous")
        selected_offer = offer_rows[0]
        dph_total = float(selected_offer.get("dph_total", float("inf")))
        if dph_total > args.max_dph:
            raise RuntimeError("selected Vast offer exceeds the approved hourly-price ceiling")
        lifecycle["offer"] = {
            "dph_total": dph_total,
            "gpu_name": selected_offer.get("gpu_name"),
            "num_gpus": selected_offer.get("num_gpus"),
            "reliability": selected_offer.get("reliability"),
            "inet_down": selected_offer.get("inet_down"),
        }
        created = _run(
            "vastai", "create", "instance", str(args.offer_id),
            "--image", args.image, "--disk", str(args.disk),
            "--ssh", "--direct", "--label", args.label,
            "--onstart-cmd", "tail -f /dev/null", "--raw",
        )
        payload = json.loads(created.stdout)
        instance_id = int(payload.get("new_contract") or payload.get("instance_id"))
        lifecycle["instance_id"] = instance_id
        if args.startup_wait_s:
            time.sleep(args.startup_wait_s)
        startup_deadline = time.monotonic() + args.startup_timeout_s
        while True:
            status_result = _run("vastai", "show", "instance", str(instance_id), "--raw", check=False)
            status_rows = _rows(json.loads(status_result.stdout or "null"))
            status = str(status_rows[0].get("actual_status", "")) if status_rows else ""
            lifecycle["last_startup_status"] = status
            if status == "running":
                break
            if status in {"exited", "offline", "unknown_error"}:
                raise RuntimeError("Vast instance entered a terminal startup state")
            if time.monotonic() >= startup_deadline:
                raise TimeoutError("Vast instance did not reach running before startup timeout")
            time.sleep(10)
        started = time.monotonic()
        result = _run(
            "vastai",
            "execute",
            str(instance_id),
            args.remote_command,
            check=False,
            timeout_s=args.max_runtime_s,
        )
        lifecycle["command_exit_code"] = result.returncode
        lifecycle["command_stdout_sha256"] = hashlib.sha256(result.stdout.encode()).hexdigest()
        lifecycle["command_stderr_sha256"] = hashlib.sha256(result.stderr.encode()).hexdigest()
        lifecycle["command_wall_s"] = round(time.monotonic() - started, 3)
        if result.returncode != 0:
            raise RuntimeError("remote workload failed; stdout/stderr retained only as digests")
        remote_sha = _run(
            "vastai",
            "execute",
            str(instance_id),
            f"find {args.remote_artifacts} -type f -print0 | sort -z | xargs -0 sha256sum",
        ).stdout
        lifecycle["remote_checksum_receipt_sha256"] = hashlib.sha256(remote_sha.encode()).hexdigest()
        _run("vastai", "copy", f"C.{instance_id}:{args.remote_artifacts}", f"local:{args.local_artifacts}")
        files = sorted(path for path in args.local_artifacts.rglob("*") if path.is_file() and path.name != "vast-lifecycle.json")
        if not files:
            raise RuntimeError("artifact copy produced no files")
        lifecycle["local_artifacts"] = [
            {"path": str(path.relative_to(args.local_artifacts)), "sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in files
        ]
        lifecycle["artifact_copy_verified"] = True
    finally:
        if instance_id is not None:
            lifecycle["destroy_attempted"] = True
            destroy = _run(
                "vastai", "destroy", "instance", str(instance_id),
                check=False,
                input_text="y\n",
            )
            lifecycle["destroy_exit_code"] = destroy.returncode
            verified_absent = False
            verification_stdout = ""
            for _ in range(6):
                verification = _run("vastai", "show", "instances", "--raw", check=False)
                verification_stdout = verification.stdout
                try:
                    verified_absent = not _instance_present(
                        json.loads(verification.stdout or "null"),
                        instance_id,
                    )
                except json.JSONDecodeError:
                    verified_absent = False
                if verified_absent:
                    break
                time.sleep(5)
            lifecycle["destroy_verify_stdout_sha256"] = hashlib.sha256(verification_stdout.encode()).hexdigest()
            lifecycle["destroy_verified_absent"] = verified_absent
            if not verified_absent:
                lifecycle["billing_alert"] = "instance_still_present_after_destroy_attempt"
        lifecycle["finished_unix_s"] = time.time()
        lifecycle_path.write_text(json.dumps(lifecycle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if instance_id is None or lifecycle.get("destroy_verified_absent") is True else 3


if __name__ == "__main__":
    raise SystemExit(main())
