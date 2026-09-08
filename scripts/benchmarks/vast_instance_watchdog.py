#!/usr/bin/env python3
"""Destroy one Vast instance at a fixed deadline and verify billing stopped.

The watchdog deliberately runs as a separate local process from the lifecycle
runner and its remote SSH command.  A parent may request a clean shutdown only
after it has independently verified that the instance is absent.  If that
verification never happens, the watchdog remains armed until the hard
deadline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _run(
    *args: str,
    input_text: str | None = None,
    timeout_s: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=timeout_s,
    )


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        instances = payload.get("instances")
        if isinstance(instances, list):
            return [dict(item) for item in instances if isinstance(item, dict)]
        return [dict(payload)]
    return []


def _present(instance_id: int) -> bool:
    try:
        result = _run("vastai", "show", "instances", "--raw", timeout_s=20)
    except subprocess.TimeoutExpired:
        return True
    if result.returncode != 0:
        return True
    try:
        rows = _rows(json.loads(result.stdout or "null"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return True
    try:
        return any(int(row.get("id", -1)) == instance_id for row in rows)
    except (TypeError, ValueError):
        return True


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Atomically preserve a mode-0600 watchdog checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _finish(
    receipt_path: Path,
    receipt: dict[str, Any],
    *,
    terminal_status: str,
    return_code: int,
) -> int:
    receipt["terminal_status"] = terminal_status
    receipt["finished_unix_s"] = time.time()
    _write_receipt(receipt_path, receipt)
    return return_code


def run_watchdog(
    *,
    instance_id: int,
    deadline_unix_s: float,
    receipt_path: Path,
    stop_file: Path | None,
    poll_s: float,
) -> int:
    """Remain armed until parent teardown is proven or the deadline fires."""
    if instance_id <= 0:
        raise ValueError("instance_id must be positive")
    if poll_s < 0.1:
        raise ValueError("poll_s must be at least 0.1 seconds")
    receipt: dict[str, Any] = {
        "schema_version": "seocho.agentic_os.vast_watchdog.v2",
        "instance_id": instance_id,
        "watchdog_started_unix_s": time.time(),
        "deadline_unix_s": deadline_unix_s,
        "stop_file_enabled": stop_file is not None,
        "destroy_attempted": False,
        "destroy_attempt_count": 0,
        "destroy_verified_absent": None,
    }
    _write_receipt(receipt_path, receipt)

    stop_seen = False
    while time.time() < deadline_unix_s:
        if stop_file is not None and stop_file.exists() and not stop_seen:
            stop_seen = True
            receipt["parent_stop_observed_unix_s"] = time.time()
            if not _present(instance_id):
                receipt["destroy_verified_absent"] = True
                return _finish(
                    receipt_path,
                    receipt,
                    terminal_status="parent_teardown_verified_absent",
                    return_code=0,
                )
            # A stale or premature stop file must never disarm cost safety.
            receipt["parent_stop_ignored_instance_present"] = True
            _write_receipt(receipt_path, receipt)
        remaining_s = max(0.0, deadline_unix_s - time.time())
        time.sleep(min(poll_s, remaining_s))

    # Do not put a potentially slow provider status request in front of the
    # hard-deadline action. Destroy is idempotent enough for this safety path;
    # absence is verified immediately after the request.
    receipt["deadline_reached_unix_s"] = time.time()
    receipt["destroy_attempted"] = True
    _write_receipt(receipt_path, receipt)
    last_destroy_exit_code = 124
    for attempt in range(1, 4):
        receipt["destroy_attempt_count"] = attempt
        try:
            destroy = _run(
                "vastai",
                "destroy",
                "instance",
                str(instance_id),
                input_text="y\n",
                timeout_s=30,
            )
            last_destroy_exit_code = destroy.returncode
        except subprocess.TimeoutExpired:
            last_destroy_exit_code = 124
        receipt["destroy_exit_code"] = last_destroy_exit_code
        _write_receipt(receipt_path, receipt)
        for _ in range(4):
            if not _present(instance_id):
                receipt["destroy_verified_absent"] = True
                return _finish(
                    receipt_path,
                    receipt,
                    terminal_status="deadline_destroyed",
                    return_code=0,
                )
            time.sleep(5)

    receipt["destroy_exit_code"] = last_destroy_exit_code
    receipt["destroy_verified_absent"] = False
    return _finish(
        receipt_path,
        receipt,
        terminal_status="billing_alert_instance_present",
        return_code=3,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", type=int, required=True)
    parser.add_argument("--deadline-unix-s", type=float, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--stop-file",
        type=Path,
        help=(
            "Parent-created stop marker. It is honored only when Vast also "
            "reports the instance absent."
        ),
    )
    parser.add_argument("--poll-s", type=float, default=30.0)
    args = parser.parse_args()
    if args.poll_s < 5:
        raise ValueError("--poll-s must be at least 5 seconds")
    return run_watchdog(
        instance_id=args.instance_id,
        deadline_unix_s=args.deadline_unix_s,
        receipt_path=args.receipt,
        stop_file=args.stop_file,
        poll_s=args.poll_s,
    )


if __name__ == "__main__":
    raise SystemExit(main())
