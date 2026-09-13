"""Offline contracts only; fabricated spans are not performance evidence."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/benchmarks/summarize_agentic_os_characterization.py'


def _module():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location('three_view', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_usage_and_valid_empty_results_remain_distinct() -> None:
    module = _module()
    assert module.complete_sum([{'input_tokens': 3}, {}], 'input_tokens') is None
    turns = [{'span_id': 'first', 'started_elapsed_ms': 0, 'latency_ms': 3},
             {'span_id': 'next', 'started_elapsed_ms': 10, 'latency_ms': 5}]
    case = {'turns': turns, 'tools': [{'name': 'execute_cypher', 'rows': 0, 'outcome': 'ok',
                                     'started_elapsed_ms': 4, 'pool_acquire_ms': .2}]}
    rows = module.tool_rows(case, {'pass': 'p', 'task_id': 't', 'trace_id': 'x'}, {})
    assert rows[0]['result_class'] == 'empty_valid'
    assert rows[0]['parent_request_id'] == 'first'
    assert rows[0]['parent_id_source'] == 'derived_serial_intervals'
    assert rows[0]['session_create_ms'] == .2
    assert rows[0]['connection_pool_wait_ms'] is None
    assert rows[0]['query_shape'] == 'unknown'
    assert rows[0]['result_tokens'] is None


def test_report_rejects_ambiguous_server_join_and_keeps_boundary_scope(tmp_path: Path) -> None:
    module = _module()
    run = tmp_path / 'source'
    p = run / 'run-base-k8'
    shard = p / 'e2e/grbench-single/shard-dynamic'
    attempt = shard / 'attempts/1'
    attempt.mkdir(parents=True)
    (p / 'otel').mkdir()
    (run / 'otel').mkdir()
    case = {'run_id': 'run1', 'case_id': 'q1', 'started_unix_s': 10,
            'trace': {'trace_id': 'trace1', 'root_span_id': 'case1'},
            'time': {'wall_ms': 3000}, 'terminal': 'final',
            'turns': [{'span_id': 'request1', 'started_elapsed_ms': 0, 'latency_ms': 2000,
                       'input_tokens': 10, 'output_tokens': 2, 'output_kind': 'message'}]}
    (attempt / 'run-receipt.json').write_text(json.dumps({'cases': [case]}))
    (shard / 'coordinator-receipts.jsonl').write_text(json.dumps({
        'run_id': 'run1', 'case_id': 'q1', 'runner_started_unix_s': 9, 'runner_finished_unix_s': 13}) + '\n')
    factors = {'throughput': {'status': 'observed', 'started_unix_s': 10, 'finished_unix_s': 20},
               'application_k': 8}
    (p / 'otel/factors.json').write_text(json.dumps(factors))
    span = {'name': 'llm_request', 'service_name': 'vllm', 'trace_id': 'trace1',
            'parent_span_id': 'request1', 'start_unix_ns': 10_000_000_000,
            'end_unix_ns': 12_000_000_000, 'attributes': {'gen_ai.latency.time_in_model_decode': 1}}
    (run / 'otel/collector-spans.jsonl').write_text((json.dumps(span) + '\n') * 2)
    result = module.summarize(run, tmp_path / 'derived')
    assert result['passes'][0]['all_pass_tasks'] == 1
    assert result['passes'][0]['cohort_tasks'] == 0  # coordinator startup crosses boundary
    assert result['passes'][0]['all_pass_matched_requests'] == 0  # two matches are ambiguous
    assert result['passes'][0]['output_tok_s_gpu'] is None
    assert result['passes'][0]['decode_share_of_summed_request_prefill_decode'] is None
    assert json.loads((attempt / 'run-receipt.json').read_text())['cases'][0] == case
