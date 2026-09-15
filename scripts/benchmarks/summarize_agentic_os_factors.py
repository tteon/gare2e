#!/usr/bin/env python3
"""Content-free explanatory factors on a pass's validated throughput window."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from summarize_agentic_os_otel_stages import throughput_receipt


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def distribution(values: list[Any]) -> dict[str, Any]:
    data = sorted(float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))
    return {"n": len(data), "mean": sum(data) / len(data) if data else None,
            **{f"p{p}": data[min(len(data) - 1, math.ceil(len(data) * p / 100) - 1)] if data else None for p in (50, 95)},
            "max": max(data) if data else None}


def summarize(root: Path) -> dict[str, Any]:
    windows = list(root.glob('e2e/*/shard-*/coordinator-window.json'))
    if len(windows) != 1:
        raise ValueError('requires exactly one coordinator window per pass')
    window = json.loads(windows[0].read_text())
    lifecycle = read_rows(windows[0].with_name('coordinator-receipts.jsonl'))
    samples = read_rows(root / 'workload/vllm-metrics.jsonl')
    rate = throughput_receipt(window, samples, lifecycle)
    output: dict[str, Any] = {'schema_version': 'agentic_os.workload_factors.v1', 'throughput': rate,
        'application_k': window['workers'], 'request_scope': 'fully_contained_case_cohort',
        'actual_scheduled_decode_sequences': None, 'saturation': 'requires_cross_K_comparison',
        'limitations': ['running requests are not decode sequences', 'process RSS is not exclusive physical memory',
                        'host Cached is not DB page cache', 'unexported metrics remain unknown',
                        'completed tasks are not correct answers', 'finite windows can bias the completed-case length distribution']}
    if rate['status'] != 'observed':
        return output
    start, end = rate['started_unix_s'], rate['finished_unix_s']
    selected = [s for s in samples if start <= s['timestamp_unix_s'] <= end]
    gauges = ('vllm:num_requests_running', 'vllm:num_requests_waiting', 'vllm:kv_cache_usage_perc')
    output['sampled_gauges'] = {name: distribution([(s.get('metrics') or {}).get(name) for s in selected]) for name in gauges}
    output['active_task_samples'] = distribution([sum(r['runner_started_unix_s'] <= s['timestamp_unix_s'] < r['runner_finished_unix_s'] for r in lifecycle) for s in selected])
    counters = ('vllm:num_preemptions', 'vllm:prompt_tokens_cached', 'vllm:prompt_tokens',
                'vllm:request_prefill_kv_computed_tokens_sum', 'vllm:request_prefill_kv_computed_tokens_count',
                'vllm:prefix_cache_hits', 'vllm:prefix_cache_queries', 'vllm:kv_offload_load_bytes',
                'vllm:kv_offload_load_time', 'vllm:spec_decode_num_draft_tokens', 'vllm:spec_decode_num_accepted_tokens')
    output['counter_deltas'] = {}
    for name in counters:
        values = [(s.get('metrics') or {}).get(name) for s in selected]
        valid = all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
        output['counter_deltas'][name] = values[-1] - values[0] if valid and all(b >= a for a, b in zip(values, values[1:])) else None
    contained = {r['run_id'] for r in lifecycle if start <= r['runner_started_unix_s'] and r['runner_finished_unix_s'] <= end}
    cases = [c for path in root.glob('e2e/*/shard-*/attempts/*/run-receipt.json') for c in json.loads(path.read_text()).get('cases', []) if c.get('run_id') in contained]
    turns = [t for c in cases for t in c.get('turns', [])]
    output['request_distributions'] = {name: distribution([t.get(name) for t in turns]) for name in ('input_tokens', 'output_tokens', 'cached_tokens', 'latency_ms')}
    output['case_distributions'] = {name: distribution([(c.get('time') or {}).get(name) for c in cases]) for name in ('wall_ms', 'llm_ms', 'tool_ms')}
    output['case_terminal_counts'] = {str(value): sum(c.get('terminal') == value for c in cases) for value in {c.get('terminal') for c in cases}}
    output['case_answer_status_counts'] = {str(value): sum(c.get('answer_status') == value for c in cases) for value in {c.get('answer_status') for c in cases}}
    output['actual_constrained_query_ratio'] = sum(t.get('constrained') is True for t in turns) / sum('constrained' in t and t.get('output_kind') == 'cypher' for t in turns) if any(t.get('output_kind') == 'cypher' and 'constrained' in t for t in turns) else None
    gpu_path = root / 'workload/gpu.csv'
    gpu_ids: set[str] = set()
    if gpu_path.exists():
        with gpu_path.open() as handle:
            gpu_ids = {r['gpu_uuid'] for r in csv.DictReader(handle) if start <= float(r['timestamp_unix_s']) <= end and r['gpu_uuid']}
    output['observed_physical_gpu_count'] = len(gpu_ids) or None
    output['output_tokens_per_s_per_gpu'] = rate['output_tokens_per_s'] / len(gpu_ids) if gpu_ids else None
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pass-root', type=Path, required=True)
    args = parser.parse_args()
    output = summarize(args.pass_root)
    directory = args.pass_root / 'otel'
    directory.mkdir(exist_ok=True)
    (directory / 'factors.json').write_text(json.dumps(output, indent=2) + '\n')
    (directory / 'throughput.json').write_text(json.dumps(output['throughput'], indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
