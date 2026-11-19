"""Boundary checks only; GPU/owner settlement is not inferred from these tests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest


@pytest.fixture
def launcher():
    path = Path(__file__).resolve().parents[2] / 'docker/agentic-os-appliance/vast-runtime/run_pi.py'
    spec = importlib.util.spec_from_file_location('vast_pi_launcher', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('native_exit,export_exit,expected', [(86, 0, 86), (0, 1, 1), (1, 1, 1), (0, 0, 0)])
def test_native_failure_is_not_hidden_by_successful_trace_export(launcher, tmp_path, monkeypatch, native_exit, export_exit, expected):
    (tmp_path / 'agent-events.jsonl').write_text('{}\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'output': str(tmp_path)}))
    results = iter([native_exit, export_exit])
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=next(results)))
    assert launcher.native(str(config)) == expected
    assert (tmp_path / 'agent-events.jsonl').exists()


def test_escaping_artifacts_rejected_before_any_launch(launcher):
    with pytest.raises(ValueError, match='fresh absolute'):
        launcher.context_for({'output': '/artifacts/../outside'})


def test_context_and_archive_identity_must_match(launcher, tmp_path):
    context = tmp_path / 'trace.json'
    context.write_text(json.dumps({'trace_id': '1' * 32, 'root_span_id': '2' * 16,
                                  'experiment': dict(project_id='p', campaign_id='c', experiment_id='e', run_id='wrong')}))
    with pytest.raises(ValueError, match='identity mismatch'):
        launcher.context_for({'output': '/artifacts/new-run', 'env': {'PI_TRACE_CONTEXT_FILE': str(context)}})


def test_existing_runtime_raw_is_included_without_reconstructing_events(launcher, tmp_path, monkeypatch):
    # The captured bytes start at the measured offset; a CSV header is retained.
    source = tmp_path / 'host.csv'
    source.write_bytes(b'time,value\n1,2\n3,4\n')
    monkeypatch.setattr(launcher, 'raw_files', lambda: [source])
    # Keep the source under the same explicit artifact root used by deployment.
    original = Path.relative_to
    monkeypatch.setattr(Path, 'relative_to', lambda p, base: Path('sample.csv') if p == source else original(p, base))
    out = tmp_path / 'run'
    out.mkdir()
    launcher.snapshot_raw(out, {str(source): len(b'time,value\n1,2\n')})
    assert (out / 'runtime-raw/sample.csv').read_bytes() == b'time,value\n3,4\n'
    assert source.read_bytes() == b'time,value\n1,2\n3,4\n'


def test_observed_wrapper_reaps_its_actual_direct_child():
    path = Path(__file__).resolve().parents[2] / 'scripts/benchmarks/run_observed_experiment.py'
    spec = importlib.util.spec_from_file_location('observed_run', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    try:
        assert module.cleanup_group(child, grace_s=0.2)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
