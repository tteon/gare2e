from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

import pytest


def _runner():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "vast_colocated_agentic_os_run.py"
    spec = importlib.util.spec_from_file_location("vast_colocated_agentic_os_run", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remote_paths_are_absolute_and_traversal_free() -> None:
    module = _runner()

    assert module._safe_remote_path("/workspace/input/file.json") == "/workspace/input/file.json"
    with pytest.raises(ValueError):
        module._safe_remote_path("relative/file.json")
    with pytest.raises(ValueError):
        module._safe_remote_path("/workspace/input/../secret")


def test_remote_checksum_map_matching_prefers_exact_path(tmp_path) -> None:
    module = _runner()
    target = tmp_path / "e2e" / "receipt.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")

    assert module._match_local_artifact(tmp_path, "e2e/receipt.json") == target


def test_ssh_prefix_accepts_current_vast_uri_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runner()

    class _Result:
        stdout = "ssh://root@ssh6.vast.example:16123\n"

    monkeypatch.setattr(module, "_run", lambda *args, **kwargs: _Result())
    prefix = module._ssh_prefix(7)

    assert prefix[-1] == "root@ssh6.vast.example"
    assert prefix[prefix.index("-p") + 1] == "16123"


def test_scp_prefix_reuses_direct_ssh_port() -> None:
    module = _runner()

    prefix = [
        "ssh", "-p", "16123", "-o", "BatchMode=yes", "-o",
        "StrictHostKeyChecking=accept-new", "root@ssh6.vast.example",
    ]

    assert module._scp_prefix(prefix) == [
        "scp", "-P", "16123", "-o", "BatchMode=yes", "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def test_remote_uploads_are_verified_before_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _runner()
    source = tmp_path / "bootstrap.sh"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    digest = module._sha256(source)

    class _Result:
        stdout = f"{digest}  /workspace/bootstrap/bootstrap.sh\n"

    monkeypatch.setattr(module, "_ssh", lambda *args, **kwargs: _Result())

    assert module._verify_remote_uploads(
        ["ssh", "host"], [(source, "/workspace/bootstrap/bootstrap.sh")]
    ) == [{"remote": "/workspace/bootstrap/bootstrap.sh", "sha256": digest}]


def test_wait_ssh_retries_until_direct_endpoint_accepts_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _runner()
    returncodes = iter((255, 0))

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    monkeypatch.setattr(
        module,
        "_ssh",
        lambda *args, **kwargs: _Result(next(returncodes)),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    module._wait_ssh(["ssh", "host"], timeout_s=30)


def test_offer_admission_requires_declared_host_ram() -> None:
    module = _runner()
    offer = {
        "dph_total": 1.5,
        "num_gpus": 2,
        "gpu_total_ram": 163_840,
        "cpu_cores_effective": 48,
        "cpu_ram": 358_504,
        "reliability": 0.995,
        "disk_space": 500,
        "gpu_name": "A100 SXM4",
    }
    admission = {
        "max_dph": 2.0,
        "min_num_gpus": 2,
        "min_gpu_total_ram": 160_000,
        "min_effective_cpu": 32,
        "min_reliability": 0.99,
        "disk_gb": 160,
        "gpu_name_pattern": re.compile("A100"),
    }

    assert module._offer_is_admitted(
        offer, min_cpu_ram_mb=350_000, **admission
    )
    assert not module._offer_is_admitted(
        offer, min_cpu_ram_mb=400_000, **admission
    )
    assert not module._offer_is_admitted(
        {key: value for key, value in offer.items() if key != "cpu_ram"},
        min_cpu_ram_mb=1,
        **admission,
    )


def test_cost_watchdog_starts_in_independent_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _runner()
    captured: dict[str, object] = {}

    class _Process:
        pid = 9123

    def _popen(command, **kwargs):  # noqa: ANN001, ANN202 - subprocess test double
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(module.subprocess, "Popen", _popen)
    watchdog = module._start_cost_watchdog(
        instance_id=42,
        deadline_unix_s=1234.5,
        local_artifacts=tmp_path,
    )

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert kwargs["start_new_session"] is True
    assert command[command.index("--instance-id") + 1] == "42"
    assert command[command.index("--deadline-unix-s") + 1] == "1234.500000"
    assert command[command.index("--stop-file") + 1] == str(watchdog.stop_path)
    assert watchdog.stdout_path.stat().st_mode & 0o777 == 0o600
    assert watchdog.stderr_path.stat().st_mode & 0o777 == 0o600


def test_cost_watchdog_stops_only_after_parent_verified_absence(
    tmp_path: Path,
) -> None:
    module = _runner()
    receipt_path = tmp_path / "watchdog.json"
    stop_path = tmp_path / "watchdog.stop.json"

    class _Process:
        pid = 99

        def __init__(self) -> None:
            self.waited = False

        def poll(self):  # noqa: ANN201
            return None

        def wait(self, *, timeout):  # noqa: ANN001, ANN201
            self.waited = True
            assert stop_path.is_file()
            receipt_path.write_text(
                json.dumps(
                    {
                        "terminal_status": "parent_teardown_verified_absent",
                        "destroy_verified_absent": True,
                    }
                ),
                encoding="utf-8",
            )
            return 0

    process = _Process()
    watchdog = module._CostWatchdogProcess(
        process=process,
        receipt_path=receipt_path,
        stop_path=stop_path,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        deadline_unix_s=2000.0,
    )

    summary = module._stop_cost_watchdog(
        watchdog,
        parent_destroy_verified_absent=True,
    )

    assert process.waited is True
    assert summary["shutdown_verified"] is True
    assert summary["left_armed"] is False
    assert stop_path.stat().st_mode & 0o777 == 0o600


def test_cost_watchdog_remains_armed_when_parent_teardown_is_unresolved(
    tmp_path: Path,
) -> None:
    module = _runner()

    class _Process:
        pid = 101

        def poll(self):  # noqa: ANN201
            return None

        def wait(self, *, timeout):  # noqa: ANN001, ANN201
            raise AssertionError("an armed watchdog must not be reaped")

    watchdog = module._CostWatchdogProcess(
        process=_Process(),
        receipt_path=tmp_path / "watchdog.json",
        stop_path=tmp_path / "watchdog.stop.json",
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
        deadline_unix_s=2000.0,
    )

    summary = module._stop_cost_watchdog(
        watchdog,
        parent_destroy_verified_absent=False,
    )

    assert summary["left_armed"] is True
    assert summary["shutdown_verified"] is False
    assert not watchdog.stop_path.exists()


def test_main_arms_watchdog_immediately_after_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _runner()
    upload = tmp_path / "bootstrap.sh"
    upload.write_text("#!/bin/sh\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    events: list[str] = []
    run_results = iter(
        (
            module.subprocess.CompletedProcess(
                (),
                0,
                stdout=json.dumps(
                    [
                        {
                            "id": 123,
                            "dph_total": 1.0,
                            "num_gpus": 2,
                            "gpu_total_ram": 163_840,
                            "cpu_cores_effective": 48,
                            "cpu_ram": 400_000,
                            "reliability": 0.999,
                            "disk_space": 500,
                            "gpu_name": "A100 SXM4",
                        }
                    ]
                ),
                stderr="",
            ),
            module.subprocess.CompletedProcess(
                (), 0, stdout=json.dumps({"new_contract": 456}), stderr=""
            ),
        )
    )

    def _run(*args, **_kwargs):  # noqa: ANN001, ANN202
        events.append(str(args[1]))
        return next(run_results)

    class _Process:
        pid = 77

    def _start(**kwargs):  # noqa: ANN003, ANN202
        events.append("watchdog")
        return module._CostWatchdogProcess(
            process=_Process(),
            receipt_path=artifacts / "watchdog.json",
            stop_path=artifacts / "watchdog.stop.json",
            stdout_path=artifacts / "watchdog.stdout.log",
            stderr_path=artifacts / "watchdog.stderr.log",
            deadline_unix_s=kwargs["deadline_unix_s"],
        )

    def _wait_running(*_args, **_kwargs):
        events.append("wait_running")
        return {"actual_status": "running", "cpu_ram": 400_000}

    monkeypatch.setattr(module, "_run", _run)
    monkeypatch.setattr(module, "_start_cost_watchdog", _start)
    monkeypatch.setattr(module, "_wait_running", _wait_running)
    monkeypatch.setattr(module, "_ssh_prefix", lambda _instance_id: ["ssh", "host"])
    monkeypatch.setattr(module, "_wait_ssh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_copy_to_remote", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "_verify_remote_uploads",
        lambda *_args, **_kwargs: [{"remote": "/remote/bootstrap.sh", "sha256": "a"}],
    )
    monkeypatch.setattr(
        module,
        "_ssh",
        lambda *_args, **_kwargs: module.subprocess.CompletedProcess(
            (), 0, stdout="ok", stderr=""
        ),
    )
    monkeypatch.setattr(module, "_remote_checksum_map", lambda *_args: {})
    monkeypatch.setattr(module, "_copy_from_remote", lambda *_args, **_kw: None)
    monkeypatch.setattr(module, "_collect_essentials", lambda *_args, **_kw: {"verified": False, "skipped": "test"})
    monkeypatch.setattr(module, "_destroy_and_verify", lambda _instance_id: (0, True))
    monkeypatch.setattr(
        module,
        "_stop_cost_watchdog",
        lambda *_args, **_kwargs: {
            "shutdown_verified": True,
            "deadline_destroy_triggered": False,
            "terminal_status": "parent_teardown_verified_absent",
        },
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "vast_colocated_agentic_os_run.py",
            "--offer-id",
            "123",
            "--image",
            "vllm:test",
            "--disk-gb",
            "160",
            "--label",
            "mock-only",
            "--max-dph",
            "2",
            "--min-num-gpus",
            "2",
            "--min-cpu-ram-mb",
            "350000",
            "--max-runtime-s",
            "3600",
            "--remote-command",
            "true",
            "--local-artifacts",
            str(artifacts),
            "--upload",
            f"{upload}=/remote/bootstrap.sh",
        ],
    )

    assert module.main() == 0
    assert events[:4] == ["search", "create", "watchdog", "wait_running"]
    receipt = json.loads(
        (artifacts / "vast-colocated-lifecycle.json").read_text(encoding="utf-8")
    )
    assert receipt["offer"]["cpu_ram"] == 400_000
    assert receipt["min_cpu_ram_mb"] == 350_000
    assert receipt["cost_watchdog"]["shutdown_verified"] is True
    assert receipt["state"] == "complete"


def test_destroy_skips_the_interactive_confirmation_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _runner()
    commands: list[tuple[str, ...]] = []

    class _Result:
        returncode = 0
        stdout = "[]"

    def fake_run(*args: str, **_kwargs):
        commands.append(args)
        return _Result()

    monkeypatch.setattr(module, "_run", fake_run)
    exit_code, absent = module._destroy_and_verify(49648917)

    assert (exit_code, absent) == (0, True)
    assert commands[0] == ("vastai", "destroy", "instance", "49648917", "-y")


def test_copy_from_remote_avoids_dot_suffix_and_flattens_into_local_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _runner()
    seen: list[tuple[str, ...]] = []

    def fake_run(*args: str, **_kwargs):
        seen.append(args)
        destination = Path(args[-1]) / "artifacts"
        (destination / "e2e").mkdir(parents=True)
        (destination / "e2e" / "receipt.json").write_text("{}", encoding="utf-8")
        (destination / "lifecycle.tsv").write_text("x", encoding="utf-8")

    monkeypatch.setattr(module, "_run", fake_run)
    prefix = ["ssh", "-p", "16123", "-o", "BatchMode=yes", "root@ssh6.vast.example"]
    module._copy_from_remote(prefix, "/artifacts", tmp_path)

    assert seen[0][-2] == "root@ssh6.vast.example:/artifacts"
    assert not seen[0][-2].endswith("/.")
    assert (tmp_path / "e2e" / "receipt.json").is_file()
    assert (tmp_path / "lifecycle.tsv").is_file()
    assert not (tmp_path / ".remote-incoming").exists()


def test_collection_classifies_still_growing_remote_logs_as_volatile_not_fatal(tmp_path, monkeypatch) -> None:
    """Mirror of the collection block: a remote digest that changes between passes is volatile."""
    module = _runner()
    (tmp_path / "vllm.stdout.private.log").write_text("partial", encoding="utf-8")
    (tmp_path / "receipt.json").write_text("{}", encoding="utf-8")
    stable = module._sha256(tmp_path / "receipt.json")
    first = {"vllm.stdout.private.log": "aaaa", "receipt.json": stable}
    second = {"vllm.stdout.private.log": "bbbb", "receipt.json": stable}
    monkeypatch.setattr(module, "_remote_checksum_map", lambda *_args: second)

    mismatches = [rel for rel, digest in first.items() if module._sha256(module._match_local_artifact(tmp_path, rel)) != digest]
    assert mismatches == ["vllm.stdout.private.log"]
    rehashed = module._remote_checksum_map(None, "/artifacts")
    volatile = [rel for rel in mismatches if rehashed.get(rel) not in (None, first[rel])]
    assert volatile == ["vllm.stdout.private.log"]


def test_exclude_host_id_flag_is_parsed_as_repeatable_ints() -> None:
    module = _runner()
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude-host-id", action="append", type=int, default=[])
    args = parser.parse_args(["--exclude-host-id", "458442", "--exclude-host-id", "3497"])
    assert args.exclude_host_id == [458442, 3497]
    assert "--exclude-host-id" in Path(module.__file__).read_text(encoding="utf-8")
    assert 'int(row.get("host_id", -1)) not in excluded' in Path(module.__file__).read_text(encoding="utf-8")


def test_require_nvlink_refuses_pcie_only_pairs():
    module = _runner()
    base = {"dph_total": 2.0, "num_gpus": 2, "gpu_total_ram": 163840, "cpu_cores_effective": 32, "cpu_ram": 190000, "reliability": 0.99, "disk_space": 400, "gpu_name": "A100 SXM4"}
    kwargs = dict(max_dph=2.9, min_num_gpus=2, min_gpu_total_ram=163840, min_effective_cpu=16, min_cpu_ram_mb=65536, min_reliability=0.98, disk_gb=200, gpu_name_pattern=None)
    assert module._offer_is_admitted(dict(base, bw_nvlink=300.0), require_nvlink=True, **kwargs)
    assert not module._offer_is_admitted(dict(base, bw_nvlink=0.0), require_nvlink=True, **kwargs)
    assert not module._offer_is_admitted(dict(base), require_nvlink=True, **kwargs)  # missing field fails closed
    assert module._offer_is_admitted(dict(base, bw_nvlink=0.0), **kwargs)  # default keeps the old behaviour


def test_essentials_tar_command_lists_only_content_free_globs_and_hashes_the_archive():
    module = _runner()
    cmd = module._essentials_tar_command("/artifacts", "/tmp/e.tgz")
    assert cmd.startswith("cd /artifacts && ")
    assert "run-*/e2e/*/shard-*/attempts/*/run-receipt.json" in cmd and "otel/collector-spans.jsonl" in cmd
    assert "private.log" not in cmd  # bulky private logs never travel in the essential archive
    assert "sha256sum -- /tmp/e.tgz" in cmd and "recursive=False" in cmd


def test_missing_essential_globs_do_not_archive_working_directory(tmp_path):
    import subprocess, tarfile
    module = _runner()
    (tmp_path / 'unrelated.private.log').write_text('must stay excluded')
    (tmp_path / 'queue-receipt.jsonl').write_text('{}\n')
    archive = tmp_path.parent / (tmp_path.name + '.tgz')
    subprocess.run(['bash', '-c', module._essentials_tar_command(str(tmp_path), str(archive))], check=True, capture_output=True)
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ['queue-receipt.jsonl']


def test_collect_essentials_copies_verifies_and_extracts(tmp_path, monkeypatch):
    import hashlib, io, tarfile
    module = _runner()
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as tar:
        data = b'{"pass_id":"x"}\n'
        info = tarfile.TarInfo("queue-receipt.jsonl"); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        bad = tarfile.TarInfo("../escape.txt"); bad.size = 1; tar.addfile(bad, io.BytesIO(b"x"))
    blob = payload.getvalue(); digest = hashlib.sha256(blob).hexdigest()

    class _P:
        def __init__(self, out): self.stdout = out; self.returncode = 0

    monkeypatch.setattr(module, "_ssh", lambda prefix, cmd, **kw: _P(f"{digest}  /tmp/seocho-essentials.tgz\n"))
    def fake_run(*args, **kw):
        target = Path(args[-1]); target.write_bytes(blob); return _P("")
    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr(module, "_scp_prefix", lambda prefix: ["scp"])
    receipt = module._collect_essentials(["ssh", "root@h"], "/artifacts", tmp_path, timeout_s=10)
    assert receipt["verified"] and receipt["files"] == 1
    checkpoint = json.loads((tmp_path / "essential-copy-receipt.json").read_text())
    assert checkpoint["remote_sha256"] == digest == checkpoint["local_sha256"]
    assert checkpoint["extraction_complete"] is True
    assert checkpoint["bytes"] == receipt["bytes"] == len(blob)
    assert (tmp_path / "queue-receipt.jsonl").read_text() == '{"pass_id":"x"}\n'
    assert not (tmp_path / "essentials.tgz").exists() and not (tmp_path.parent / "escape.txt").exists()


def test_collect_essentials_refuses_a_digest_mismatch(tmp_path, monkeypatch):
    module = _runner()
    class _P:
        def __init__(self, out): self.stdout = out
    monkeypatch.setattr(module, "_ssh", lambda prefix, cmd, **kw: _P("0" * 64 + "  /tmp/seocho-essentials.tgz\n"))
    monkeypatch.setattr(module, "_run", lambda *a, **k: Path(a[-1]).write_bytes(b"not-the-archive") or _P(""))
    monkeypatch.setattr(module, "_scp_prefix", lambda prefix: ["scp"])
    with pytest.raises(RuntimeError, match="digest mismatch"):
        module._collect_essentials(["ssh", "root@h"], "/artifacts", tmp_path, timeout_s=10)


def test_collect_flags_default_to_an_hour_with_one_retry():
    module = _runner()
    import inspect
    src = inspect.getsource(module.main)
    assert '"--collect-timeout-s", type=int, default=3600' in src and '"--collect-retries", type=int, default=1' in src
    assert '"--collect-bulk", choices=("full", "essential-only")' in src and 'SkippedByFlag' in src
    assert "_PartialArtifacts" in src and "essential_artifacts_collected" in src and "artifacts_partial" in src
