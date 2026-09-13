from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[2] / 'scripts' / 'benchmarks' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_lane(tmp_path: Path) -> Path:
    path = tmp_path / 'fake_lane.py'
    path.write_text('''import json, os, sys, time
from pathlib import Path
calls = 0
print("import-private")
def main():
    global calls
    calls += 1
    qid, output, failing = sys.argv[1:]
    print("private-case-" + qid)
    os.write(2, b"native-private-error\\n")
    time.sleep(0.02)
    Path(output).write_text(json.dumps({"pid": os.getpid(), "calls": calls, "qid": qid}))
    if qid == failing:
        raise RuntimeError("private-exception")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
''')
    return path


def test_persistent_worker_reuses_module_and_isolates_argv_and_streams(tmp_path: Path) -> None:
    module = load_script('run_agentic_os_schedule')
    script = fake_lane(tmp_path)
    runner = module.PersistentRunner(tmp_path)
    try:
        receipts = []
        for i in range(3):
            root = tmp_path / str(i)
            root.mkdir()
            receipt = runner.run([sys.executable, str(script), str(i), str(root / 'case.json'), '1'], root)
            receipts.append(receipt)
            assert json.loads((root / 'case.json').read_text())['calls'] == i + 1
            output = (root / 'runner.stdout.private.log').read_text()
            assert ('import-private' in output) == (i == 0)
            assert f'private-case-{i}' in output
            assert oct((root / 'runner.stderr.private.log').stat().st_mode & 0o777) == '0o600'
            assert 'private-' not in json.dumps(receipt)
        assert [r['runner_exit_code'] for r in receipts] == [0, 1, 0]
        assert len({r['runner_pid'] for r in receipts}) == 1
    finally:
        runner.close()
    assert runner.process.poll() == 0


def run_dynamic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: str, keep_going: bool, fail: str = '') -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    module = load_script('run_agentic_os_schedule')
    script = fake_lane(tmp_path)
    entries = [dict(qid=str(i), arm='B0', seed=0, repetition=0,
                    cache_phase='warm', paired_unit=str(i)) for i in range(8)]
    args = SimpleNamespace(lane='grbench-single', workers=2, runner_mode=mode,
                           run_root=tmp_path / 'run', continue_on_error=keep_going,
                           steady_state_warmup_s=0)

    def command(args: Any, entry: dict[str, Any], root: Path, run_id: str) -> list[str]:
        root.mkdir(parents=True)
        return [sys.executable, str(script), entry['qid'], str(root / 'case.json'), fail]

    monkeypatch.setattr(module, '_runner_command', command)
    status = module._run_dynamic(args, entries, 'digest', args.run_root / 'coordinator-receipts.jsonl')
    rows = [json.loads(line) for line in (args.run_root / 'coordinator-receipts.jsonl').read_text().splitlines()]
    window = json.loads((args.run_root / 'coordinator-window.json').read_text())
    return status, rows, window


@pytest.mark.parametrize('mode', ['fresh', 'persistent'])
def test_dynamic_queue_preserves_every_identity_and_has_backlog_window(tmp_path, monkeypatch, mode) -> None:
    status, rows, window = run_dynamic(tmp_path, monkeypatch, mode=mode, keep_going=True)
    assert status == 0
    assert sorted(row['schedule_ordinal'] for row in rows) == list(range(8))
    assert len({row['run_id'] for row in rows}) == 8
    assert len({row['worker_slot'] for row in rows}) == 2
    assert window['window_status'] == 'available'
    assert window['duration_s'] > 0
    assert window['finished_unix_s'] < max(row['runner_finished_unix_s'] for row in rows)
    assert 'private-case' not in json.dumps(rows)
    if mode == 'persistent':
        assert len({row['runner_pid'] for row in rows}) == 2


@pytest.mark.parametrize('keep_going', [False, True])
def test_dynamic_failure_drains_running_work_and_records_skips(tmp_path, monkeypatch, keep_going) -> None:
    status, rows, window = run_dynamic(tmp_path, monkeypatch, mode='persistent', keep_going=keep_going, fail='0')
    assert status == 2
    assert window['failed_attempts'] == 1
    assert window['completed_attempts'] + window['skipped_attempts'] == 8
    assert window['completed_attempts'] == len(rows)
    assert (len(rows) == 8) == keep_going
    assert window['window_status'] == 'insufficient_backlog_or_failed_run'
    assert window['task_completions_per_s'] is None


def test_refill_does_not_wait_for_slow_static_shard(tmp_path, monkeypatch) -> None:
    import threading
    import subprocess
    module = load_script('run_agentic_os_schedule')
    reached_later_task = threading.Event()

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if command[0] == '0':
            assert reached_later_task.wait(3), 'second worker must take later FIFO work'
        elif command[0] == '4':
            reached_later_task.set()
        return subprocess.CompletedProcess(command, 0, b'', b'')

    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module, '_runner_command', lambda args, entry, root, run_id: [entry['qid']])
    args = SimpleNamespace(lane='grbench-single', workers=2, runner_mode='fresh',
                           run_root=tmp_path, continue_on_error=True, steady_state_warmup_s=0)
    entries = [dict(qid=str(i), arm='B0', seed=0, repetition=0, cache_phase='warm', paired_unit=str(i)) for i in range(5)]
    assert module._run_dynamic(args, entries, 'digest', tmp_path / 'coordinator-receipts.jsonl') == 0


@pytest.mark.parametrize('extra', [
    ['--shard', '0/2'], ['--cache-control-script', 'script'],
    ['--inter-attempt-gap-s', '1'], ['--lane', 'grbench-multi'],
    ['--lane', 'grbench-router'], ['--gpu-sampling', 'co-located'],
])
def test_incompatible_persistent_modes_fail_before_inputs(monkeypatch, extra) -> None:
    module = load_script('run_agentic_os_schedule')
    monkeypatch.setattr(sys, 'argv', ['schedule', '--lane', 'grbench-single', '--schedule', 'missing',
        '--dataset', 'missing', '--discovery-cache', 'missing', '--run-root', 'unused',
        '--base-url', 'http://unused', '--model', 'unused', '--enable-thinking', 'false',
        '--runner-mode', 'persistent', '--gpu-sampling', 'appliance', *extra])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


def test_real_text2cypher_main_reentry_keeps_cases_and_trace_context_separate(tmp_path: Path) -> None:
    """Actual lane main with a synthetic provider; no LLM or graph service used."""
    module = load_script('run_agentic_os_schedule')
    script = tmp_path / 'offline_lane.py'
    script.write_text('''import run_direct_react_text2cypher as lane
from seocho.eval.agentic_os_trace import current_case_trace
def post(url, payload):
    assert len(payload["messages"]) == 2
    return {"choices": [{"message": {"content": "synthetic reply"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}, 1.0
lane._post = post
def main():
    assert current_case_trace() is None
    result = lane.main()
    assert current_case_trace() is None
    return result
''')
    questions = tmp_path / 'questions.jsonl'
    questions.write_text(''.join(json.dumps(dict(qid=str(i), question='synthetic', answer='synthetic')) + '\n' for i in range(2)))
    runner = module.PersistentRunner(tmp_path)
    try:
        for i in range(2):
            root = tmp_path / f'real-{i}'
            command = [sys.executable, str(script), '--questions', str(questions), '--base-url', 'http://unused',
                       '--model', 'offline', '--condition', 'closed_book', '--qid', str(i), '--run-id', f'run-{i}',
                       '--receipt-out', str(root / 'run-receipt.json'),
                       '--predictions-out', str(root / 'predictions.private.json')]
            result = runner.run(command, root)
            assert result['runner_exit_code'] == 0, (root / 'runner.stderr.private.log').read_text()
            receipt = json.loads((root / 'run-receipt.json').read_text())
            assert len(receipt['cases']) == 1
            assert receipt['config']['run_id'] == f'run-{i}'
            assert receipt['cases'][0]['case_id'] == str(i)
    finally:
        runner.close()


@pytest.mark.parametrize('mode', ['dynamic', 'static'])
def test_appliance_main_dispatches_correct_coordinator_mode(tmp_path, mode) -> None:
    import os
    import subprocess
    root = Path(__file__).parents[2]
    binary = tmp_path / 'bin'
    binary.mkdir()
    fake = binary / 'python3'
    fake.write_text(f'''#!{sys.executable}
import json, os, sys
with open(os.environ["CALLS_FILE"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
''')
    fake.chmod(0o755)
    calls = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'PATH': f'{binary}:{os.environ["PATH"]}', 'CALLS_FILE': str(calls),
           'SEOCHO_REPO_ROOT': str(root), 'SEOCHO_APPLIANCE_ARTIFACTS': str(tmp_path / 'artifacts'),
           'VLLM_MODEL': 'offline', 'NEO4J_PASSWORD': 'synthetic', 'DOZERDB_PID': '1',
           'SEOCHO_E2E_PHASE': 'main', 'SEOCHO_MAIN_SKIP_CLOSED': '1',
           'SEOCHO_MAIN_LANES': 'grbench-single', 'SEOCHO_MAIN_ARMS': 'B0',
           'SEOCHO_REASONING_MODES': 'false', 'SEOCHO_SCHEDULE_MODE': mode,
           'SEOCHO_CONCURRENCY': '2', 'SEOCHO_INTER_ATTEMPT_GAP_S': '0',
           'SEOCHO_RUNNER_MODE': 'persistent', 'SEOCHO_STEADY_STATE_WARMUP_S': '10'}
    proc = subprocess.run(['bash', str(root / 'scripts/benchmarks/run_agentic_os_colocated_e2e.sh')],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    if mode == 'dynamic':
        assert len(commands) == 1
        command = commands[0]
        assert command[command.index('--workers') + 1] == '2'
        assert command[command.index('--runner-mode') + 1] == 'persistent'
        assert command[command.index('--run-root-suffix') + 1] == 'shard-dynamic'
        assert '--shard' not in command
        assert '--cache-control-script' not in command
        assert '--inter-attempt-gap-s' not in command
    else:
        assert len(commands) == 2
        assert {command[command.index('--shard') + 1] for command in commands} == {'0/2', '1/2'}
        assert all('--cache-control-script' in command for command in commands)
