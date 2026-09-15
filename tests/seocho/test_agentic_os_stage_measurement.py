from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from seocho.eval.agentic_os_observability import parse_vllm_histogram_buckets, parse_vllm_metrics


def stages() -> ModuleType:
    path = Path(__file__).parents[2] / 'scripts/benchmarks/summarize_agentic_os_otel_stages.py'
    spec = importlib.util.spec_from_file_location('stages', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iteration_tokens_are_histogram_observations_not_generation_counter() -> None:
    raw = '''vllm:iteration_tokens_total_sum{model_name="private-model"} 512
vllm:iteration_tokens_total_count{model_name="private-model"} 4
vllm:iteration_tokens_total_bucket{model_name="private-model",le="128"} 4
vllm:generation_tokens_total{model_name="private-model"} 128
'''
    assert parse_vllm_metrics(raw) == {
        'vllm:iteration_tokens_total_sum': 512,
        'vllm:iteration_tokens_total_count': 4,
        'vllm:generation_tokens': 128,
    }
    assert parse_vllm_histogram_buckets(raw) == {'vllm:iteration_tokens_total': {'128': 4}}


def client_spans() -> list[dict[str, Any]]:
    return [
        dict(name='agentic_os.case', trace_id='trace', span_id='case', start_unix_ns=0,
             end_unix_ns=10_000_000_000, attributes={'seocho.lane': 'single', 'seocho.case_id': 'q'}),
        dict(name='chat model', trace_id='trace', span_id='chat', parent_span_id='case',
             start_unix_ns=1_000_000_000, end_unix_ns=5_000_000_000, attributes={}),
    ]


def server_span(**kwargs: Any) -> dict[str, Any]:
    return dict(dict(name='llm_request', service_name='vllm', trace_id='trace',
                     span_id='server', parent_span_id='chat', start_unix_ns=1_000_000_000,
                     end_unix_ns=4_000_000_000, attributes={
                         'gen_ai.latency.time_in_queue': 0,
                         'gen_ai.latency.time_to_first_token': .1,
                         'gen_ai.latency.time_in_model_prefill': .08,
                         'gen_ai.latency.time_in_model_decode': 2,
                         'gen_ai.usage.completion_tokens': 101,
                     }), **kwargs)


def test_trace_join_uses_exact_request_and_preserves_zero_queue() -> None:
    row = stages().build_rows(client_spans(), [server_span(name='unrelated'), server_span(trace_id='other'), server_span()])[0]
    assert row['server_span_match_count'] == 1
    assert row['server_span_present']
    assert row['queue_s'] == 0
    assert row['ttft_s'] == .1
    assert row['decode_tpot_ms'] == 20
    assert row['request_started_unix_s'] == 1
    assert row['request_finished_unix_s'] == 5


@pytest.mark.parametrize('servers', [[], [server_span(), server_span()]])
def test_missing_or_ambiguous_server_spans_remain_unknown(servers) -> None:
    row = stages().build_rows(client_spans(), servers)[0]
    assert not row['server_span_present']
    assert row['queue_s'] == ''
    assert row['decode_tpot_ms'] is None


def window() -> dict[str, Any]:
    return dict(window_status='available', started_unix_s=1, finished_unix_s=5, execution_digest='digest')


def samples() -> list[dict[str, Any]]:
    return [dict(timestamp_unix_s=t, metrics={
        'vllm:generation_tokens': t * 100,
        'vllm:iteration_tokens_total_sum': t * 300,
        'vllm:iteration_tokens_total_count': t * 10,
    }) for t in (0, 2, 3, 4, 6)]


def lifecycle() -> list[dict[str, Any]]:
    return [dict(runner_finished_unix_s=t, runner_exit_code=0) for t in (1, 2, 3, 4, 5)]


def test_throughput_uses_identical_inner_interval_and_excludes_drain() -> None:
    receipt = stages().throughput_receipt(window(), samples(), lifecycle())
    assert receipt['status'] == 'observed'
    assert receipt['started_unix_s'] == 2
    assert receipt['finished_unix_s'] == 4
    assert receipt['completed_tasks'] == 2  # [2,4)
    assert receipt['task_completions_per_s'] == 1
    assert receipt['output_tokens_per_s'] == 100
    assert receipt['iteration_tokens_per_step'] == 30


@pytest.mark.parametrize('mode', ['missing', 'reset', 'gap', 'no_backlog', 'no_lifecycle'])
def test_incomplete_telemetry_cannot_become_a_zero_or_successful_rate(mode) -> None:
    w, s, events = window(), samples(), lifecycle()
    if mode == 'missing':
        s[2]['metrics'].pop('vllm:generation_tokens')
    elif mode == 'reset':
        s[2]['metrics']['vllm:generation_tokens'] = 1
    elif mode == 'gap':
        s = [s[1], dict(s[3], timestamp_unix_s=4.9)]
    elif mode == 'no_backlog':
        w['window_status'] = 'insufficient_backlog_or_failed_run'
    elif mode == 'no_lifecycle':
        events = []
    receipt = stages().throughput_receipt(w, s, events)
    assert receipt['status'] == 'unobserved'
    assert receipt['task_completions_per_s'] is None
    assert receipt['output_tokens_per_s'] is None
