from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess


def _watchdog():
    path = (
        Path(__file__).parents[2]
        / "scripts"
        / "benchmarks"
        / "vast_instance_watchdog.py"
    )
    spec = importlib.util.spec_from_file_location("vast_instance_watchdog", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_parent_stop_is_honored_only_after_instance_is_absent(
    monkeypatch, tmp_path: Path
) -> None:
    module = _watchdog()
    clock = _Clock(100.0)
    stop_file = tmp_path / "stop.json"
    stop_file.write_text("{}", encoding="utf-8")
    receipt_path = tmp_path / "watchdog.json"
    monkeypatch.setattr(module.time, "time", clock.time)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    monkeypatch.setattr(module, "_present", lambda _instance_id: False)

    return_code = module.run_watchdog(
        instance_id=7,
        deadline_unix_s=200.0,
        receipt_path=receipt_path,
        stop_file=stop_file,
        poll_s=5.0,
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert return_code == 0
    assert receipt["terminal_status"] == "parent_teardown_verified_absent"
    assert receipt["destroy_attempted"] is False
    assert receipt["destroy_verified_absent"] is True
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_deadline_starts_destroy_and_verifies_absence(
    monkeypatch, tmp_path: Path
) -> None:
    module = _watchdog()
    clock = _Clock(100.0)
    receipt_path = tmp_path / "watchdog.json"
    presence = iter((False,))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(module.time, "time", clock.time)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    monkeypatch.setattr(module, "_present", lambda _instance_id: next(presence))

    def _run(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run", _run)

    return_code = module.run_watchdog(
        instance_id=8,
        deadline_unix_s=110.0,
        receipt_path=receipt_path,
        stop_file=None,
        poll_s=5.0,
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert return_code == 0
    assert clock.now == 110.0
    assert commands == [("vastai", "destroy", "instance", "8")]
    assert receipt["terminal_status"] == "deadline_destroyed"
    assert receipt["destroy_attempt_count"] == 1
    assert receipt["destroy_verified_absent"] is True


def test_premature_stop_file_does_not_disarm_deadline(
    monkeypatch, tmp_path: Path
) -> None:
    module = _watchdog()
    clock = _Clock(50.0)
    stop_file = tmp_path / "stop.json"
    stop_file.write_text("{}", encoding="utf-8")
    receipt_path = tmp_path / "watchdog.json"
    presence = iter((True, False))
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(module.time, "time", clock.time)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    monkeypatch.setattr(module, "_present", lambda _instance_id: next(presence))

    def _run(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run", _run)

    return_code = module.run_watchdog(
        instance_id=9,
        deadline_unix_s=60.0,
        receipt_path=receipt_path,
        stop_file=stop_file,
        poll_s=5.0,
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert return_code == 0
    assert receipt["parent_stop_ignored_instance_present"] is True
    assert receipt["terminal_status"] == "deadline_destroyed"
    assert receipt["destroy_attempted"] is True
    assert commands == [("vastai", "destroy", "instance", "9")]
