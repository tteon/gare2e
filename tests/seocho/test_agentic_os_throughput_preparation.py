from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from seocho.eval.agentic_os_observability import memory_pressure_sample, parse_vllm_metrics
from seocho.eval.agentic_os_replay import append_private_event


def load(name: str) -> Any:
    directory = Path(__file__).parents[2] / 'scripts/benchmarks'
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location(name, directory / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_scopes_and_unavailable_values(tmp_path: Path) -> None:
    proc, group = tmp_path / 'proc', tmp_path / 'cgroup'
    proc.mkdir()
    group.mkdir()
    (proc / 'meminfo').write_text('Cached: 8 kB\nMemAvailable: 15 kB\nSecret: 2 kB\n')
    (group / 'cgroup.controllers').touch()
    (group / 'memory.max').write_text('max')
    (group / 'memory.stat').write_text('file 8192\nanon 4096\npgfault 900\n')
    sample = memory_pressure_sample(proc=proc, cgroup=group)
    assert sample['host_bytes'] == {'Cached': 8192, 'MemAvailable': 15360}
    assert sample['cgroup_limit_bytes'] == 'max'
    assert sample['cgroup_current_bytes'] is None
    assert sample['cgroup_stat_bytes'] == {'file': 8192, 'anon': 4096}
    assert sample['cgroup_events'] == {}


def test_actual_prefill_cached_and_preemption_metrics() -> None:
    sample = parse_vllm_metrics('vllm:num_preemptions_total 2\nvllm:prompt_tokens_cached_total 100\nvllm:request_prefill_kv_computed_tokens_sum 30\nvllm:request_prefill_kv_computed_tokens_count 3\n')
    assert sample['vllm:num_preemptions'] == 2
    assert sample['vllm:prompt_tokens_cached'] == 100
    assert sample['vllm:request_prefill_kv_computed_tokens_sum'] == 30


def test_private_capture_requires_private_name_and_restricts_existing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        append_private_event(tmp_path / 'public.jsonl', {'secret': 'synthetic'})
    path = tmp_path / 'replay.private.jsonl'
    path.touch(mode=0o644)
    append_private_event(path, {'kind': 'llm', 'payload': {'messages': ['synthetic']}})
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('mode,expected_tools,db_calls', [('serving-only', 0, 0), ('tool-stub', 1, 0), ('real-db', 1, 1)])
def test_fixed_replay_preserves_next_payload_and_controls_tool_substrate(tmp_path, monkeypatch, mode, expected_tools, db_calls) -> None:
    module = load('run_agentic_os_fixed_replay')
    path = tmp_path / 'replay.private.jsonl'
    events = [dict(kind='llm', output_tokens=3, payload={'messages': [{'role': 'user', 'content': 'first'}]}),
        dict(kind='tool', name='execute_cypher', query='synthetic', observation='[]', outcome='ok', error_code=None,
             workspace_id='w', row_cap=20, byte_cap=12000),
        dict(kind='llm', output_tokens=2, payload={'messages': [{'role': 'user', 'content': 'frozen second'}]})]
    for event in events:
        append_private_event(path, event)
    index = tmp_path / 'index.private.jsonl'
    index.write_text(json.dumps({'qid': 'q', 'trace_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}) + '\n')
    requests, calls = [], []
    def post(url: str, payload: dict[str, Any]) -> tuple[dict[str, Any], float]:
        requests.append(payload)
        return {'usage': {'prompt_tokens': 8, 'completion_tokens': payload['max_tokens']},
                'choices': [{'message': {'content': 'different response'}}]}, 1.0
    monkeypatch.setattr(module, '_post', post)
    monkeypatch.setenv('NEO4J_PASSWORD', 'synthetic')
    monkeypatch.setattr(module.GraphDatabase, 'driver', lambda *a, **k: SimpleNamespace(close=lambda: None))
    class Executor:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass
        def execute_rows(self, query: str) -> tuple[str, dict[str, Any]]:
            calls.append(query)
            return '[]', {'rows': 0}
    monkeypatch.setattr(module, 'GRBenchDozerExecutor', Executor)
    monkeypatch.setattr(sys, 'argv', ['replay', '--questions', str(index), '--receipt-out', str(tmp_path / 'receipt.json'),
        '--base-url', 'http://unused', '--model', 'synthetic', '--qid', 'q', '--run-id', 'run', '--workspace-id', 'w', '--replay-mode', mode])
    assert module.main() == 0
    assert requests[1]['messages'][0]['content'] == 'frozen second'
    assert requests[0]['ignore_eos'] is True
    assert len(calls) == db_calls
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    assert len(receipt['cases'][0]['tools']) == expected_tools
    assert 'frozen second' not in json.dumps(receipt)


@pytest.mark.parametrize('count,duration,status', [(100, 0.03, 'available'), (2, 0.2, 'insufficient_backlog_or_failed_run')])
def test_duration_stops_dispatch_and_excludes_short_backlog(tmp_path, monkeypatch, count, duration, status) -> None:
    import subprocess
    module = load('run_agentic_os_schedule')
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        time.sleep(0.01)
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module, '_runner_command', lambda *args: ['synthetic'])
    entries = [dict(qid=str(i), arm='S1', seed=0, repetition=0, cache_phase='warm', paired_unit=str(i)) for i in range(count)]
    args = SimpleNamespace(lane='grbench-single', workers=1, runner_mode='fresh', run_root=tmp_path,
        continue_on_error=False, steady_state_warmup_s=0, measurement_duration_s=duration)
    assert module._run_dynamic(args, entries, 'digest', tmp_path / 'coordinator-receipts.jsonl') == 0
    window = json.loads((tmp_path / 'coordinator-window.json').read_text())
    assert window['window_status'] == status
    if status == 'available':
        assert window['skipped_attempts'] > 0
        assert window['duration_s'] == pytest.approx(duration, abs=1e-6)


def test_backlog_interleaves_strata_and_retains_pair_identity() -> None:
    module = load('prepare_agentic_os_throughput')
    entries = [dict(qid=str(i), arm='S1', seed=7, repetition=0, cache_phase='none', stratum='ab'[i % 2]) for i in range(4)]
    output = module.backlog_schedule({'entries': entries}, repeats=3)
    assert len(output['entries']) == 12
    assert len({e['paired_unit'] for e in output['entries']}) == 12
    assert [e['stratum'] for e in output['entries'][:4]] == ['a', 'b', 'a', 'b']
    assert all(':none:' in e['paired_unit'] for e in output['entries'])
