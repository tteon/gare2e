"""Verify chart denominators and missingness, not synthetic performance."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_PATH = Path(__file__).parents[2] / 'scripts/benchmarks/render_agentic_os_characterization_figures.py'
_SPEC = importlib.util.spec_from_file_location('characterization_figures', _PATH)
assert _SPEC and _SPEC.loader
chart = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chart)


def test_task_interval_union_clips_and_counts_parallel_work_once() -> None:
    result = chart.split_intervals(0, 10, [(-2, 5), (3, 7)], [(4, 8), (6, 9)])
    assert result == {'LLM only': 4, 'Tool only': 2, 'Overlap': 3, 'Unattributed': 1}
    assert sum(result.values()) == 10


def fixture() -> tuple[dict, list[dict], list[dict], list[dict]]:
    summary = {'pass': 'sample'}
    tasks = [{'task_id': 'a', 'fully_contained': True, 'started_unix_s': 0,
              'finished_unix_s': 10, 'llm_calls': 2, 'tool_calls': 2,
              'max_input_tokens': 100, 'input_tokens': 150, 'terminal': 'final'}]
    requests = [{'task_id': 'a', 'client_span_id': 'r1', 'request_ordinal': 1,
                 'request_started_unix_s': 0, 'request_finished_unix_s': 2,
                 'client_input_tokens': 50, 'client_cached_tokens': 10, 'client_output_tokens': 5,
                 'server_span_match_count': 1, 'prefill_s': .2, 'decode_s': .8},
                {'task_id': 'a', 'client_span_id': 'r2', 'request_ordinal': 2,
                 'request_started_unix_s': 4, 'request_finished_unix_s': 7,
                 'client_input_tokens': 100, 'client_cached_tokens': '', 'client_output_tokens': 5,
                 'server_span_match_count': 0, 'prefill_s': '', 'decode_s': ''}]
    tools = [{'task_id': 'a', 'parent_request_id': 'r1', 'tool_ordinal': 1,
              'started_elapsed_ms': 2000, 'latency_ms': 1000, 'name': 'execute_cypher',
              'query_shape': 'neighborhood', 'result_class': 'empty_valid', 'result_bytes': 2},
             {'task_id': 'a', 'parent_request_id': 'r2', 'tool_ordinal': 2,
              'started_elapsed_ms': 7000, 'latency_ms': 1000, 'name': 'execute_cypher',
              'query_shape': 'neighborhood', 'result_class': 'error', 'result_bytes': ''}]
    return summary, tasks, requests, tools


def test_missing_cache_and_exact_stages_are_not_zero_filled() -> None:
    result = chart.characterize(*fixture())
    assert result['cache_request_n'] == 1
    assert result['cache_mean_tokens'] == [10, 40, 5]
    assert result['stage_request_n'] == 1
    assert result['stage_sums_seconds'] == [.2, .8]
    assert result['task_wall_seconds'] == {'LLM only': 5, 'Tool only': 2, 'Overlap': 0, 'Unattributed': 3}
    assert result['peak_final'] == [100]
    assert 'accuracy' not in result


def test_valid_empty_is_not_failure_and_progress_is_call_based() -> None:
    result = chart.characterize(*fixture())
    assert result['tool_rankings'][0]['error_fraction'] == .5
    assert result['activity_quartiles'] == [{'Graph query': 1}, {}, {'Graph query': 1}, {}]
    assert result['linked_tool_rounds'] == [2]


def test_invalid_telemetry_and_unknown_parent_preserve_coverage_gap() -> None:
    summary, tasks, requests, tools = fixture()
    requests[0]['client_cached_tokens'] = 51  # impossible cached > input
    requests[0]['server_span_match_count'] = 2  # ambiguous linkage
    tools[0]['parent_request_id'] = 'missing'
    tools[0]['latency_ms'] = ''
    result = chart.characterize(summary, tasks, requests, tools)
    assert result['cache_mean_tokens'] is None
    assert result['stage_request_n'] == 0
    assert result['stage_sums_seconds'] is None
    assert result['time_task_n'] == 0
    assert result['activity_unknown_n'] == 1
    assert result['linked_tool_rounds'] == [1]


@pytest.mark.parametrize('value', ['', None, 'NaN', 'inf', -1])
def test_invalid_measurement_is_unknown(value: object) -> None:
    assert chart.nonnegative(value) is None


def test_replica_capacity_uses_individual_complete_windows_only() -> None:
    data = {'serving_layout': '2 x TP=1', 'sampled_gauges': {'vllm:kv_cache_usage_perc': {'mean': 1.2}},
            'replica_observations': [{'source': 'replica-0-metrics.jsonl', 'full_window_coverage': False, 'gauges': {}},
                                     {'source': 'replica-1-metrics.jsonl', 'full_window_coverage': True,
                                      'gauges': {'vllm:kv_cache_usage_perc': {'mean': .4}}}]}
    assert chart.capacity_rows(data) == [('replica-1', {'vllm:kv_cache_usage_perc': {'mean': .4}})]
