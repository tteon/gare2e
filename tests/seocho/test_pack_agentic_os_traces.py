"""Trace package (pack_agentic_os_traces.py): allowlist, leak scan, manifest, index rows."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "benchmarks" / "pack_agentic_os_traces.py"


def _load():
    spec = importlib.util.spec_from_file_location("pack_agentic_os_traces", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _fake_run(root: Path, *, leak: bool = False) -> Path:
    run = root / "lab-s0-try-000000"
    (run / "run-k4" / "otel").mkdir(parents=True)
    (run / "queue-receipt.jsonl").write_text('{"pass": "k4", "exit_code": 0}\n', encoding="utf-8")
    (run / "run-k4" / "otel" / "spans.jsonl").write_text('{"name": "agentic_os.case", "duration_ms": 12}\n', encoding="utf-8")
    (run / "appliance-phases.private.tsv").write_text("t\tphase\tservice_start\t0\n", encoding="utf-8")
    (run / "dozerdb.stdout.private.log").write_text("SECRET PROMPT TEXT\n", encoding="utf-8")
    lifecycle = {
        "instance_id": 1, "state": "complete", "max_runtime_s": 10,
        "verified_uploads": [{"remote": "/w/seocho-source.tar.gz", "sha256": "b" * 64}, {"remote": "/w/lab-queue.tsv", "sha256": "q" * 64}],
    }
    (run / "vast-colocated-lifecycle.json").write_text(json.dumps(lifecycle), encoding="utf-8")
    if leak:
        (run / "queue-receipt.jsonl").write_text('{"jupyter_token": "abcdefghijklmnop0123"}\n', encoding="utf-8")
    return run


def test_package_is_content_free_and_records_provenance(tmp_path: Path) -> None:
    run = _fake_run(tmp_path)
    out = tmp_path / "pkg"
    proc = subprocess.run([sys.executable, str(SCRIPT), str(run), "--output", str(out), "--lab-request", "013"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    paths = {f["path"] for f in manifest["files"]}
    assert "appliance-phases.tsv" in paths  # the one allowed private file, renamed
    assert not any("dozerdb" in p for p in paths)  # private logs never travel
    assert manifest["leak_scan"]["hits"] == []
    assert manifest["provenance"]["source_bundle_sha256"] == "b" * 64
    assert manifest["provenance"]["lab_request"] == "013"
    assert not (out / "vast-colocated-lifecycle.json").exists()


def test_leak_hit_blocks_the_package(tmp_path: Path) -> None:
    run = _fake_run(tmp_path, leak=True)
    proc = subprocess.run([sys.executable, str(SCRIPT), str(run), "--output", str(tmp_path / "pkg")], capture_output=True, text=True)
    assert proc.returncode == 3
    assert "instance_token_field" in proc.stderr


def test_index_append_replaces_same_run(tmp_path: Path) -> None:
    mod = _load()
    manifest = {"run_root": "lab-s8-try-1", "file_count": 3, "bytes": 10, "provenance": {"source_bundle_sha256": "a" * 64, "instance_id": 7, "state": "complete"}}
    idx = tmp_path / "index.json"
    e1 = mod.index_entry(manifest, "s3://b/seocho-agentic-os/traces/lab-s8-try-1/", "010", False)
    assert e1["manifest"] == "s3://b/seocho-agentic-os/traces/lab-s8-try-1/manifest.json"
    mod.append_index(idx, e1)
    mod.append_index(idx, dict(e1, keep=True))
    other = mod.index_entry(dict(manifest, run_root="lab-s11-try-2"), "s3://b/seocho-agentic-os/traces/lab-s11-try-2", "013", False)
    index = mod.append_index(idx, other)
    assert [r["run"] for r in index["runs"]] == ["lab-s8-try-1", "lab-s11-try-2"]
    assert index["runs"][0]["keep"] is True
    assert index["schema_version"] == "seocho.agentic_os_trace_index.v1"
