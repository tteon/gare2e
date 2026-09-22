#!/usr/bin/env python3
"""Derive linked, content-free Agent / Runtime / Tool views without changing evidence.

Private captures are optional and used only to map exact query digests to lexical
shapes. Unmatched shapes and unrecorded token components remain unknown.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from seocho.eval.agentic_os_characterization import query_shape
from seocho.eval.agentic_os_graph_system import attach_graph_evidence, summarize_graph_system
from seocho.eval.agentic_os_otel import build_case_spans, iter_case_receipts
from seocho.eval.grbench_runtime import validate_grbench_read_query
from summarize_agentic_os_factors import distribution, read_rows
from summarize_agentic_os_otel_stages import build_rows


def query_shapes(root: Path, enabled: bool) -> dict[str, str]:
    labels: dict[str, str] = {}
    if not enabled:
        return labels
    for path in root.glob('run-capture/**/replay.private.jsonl'):
        for event in read_rows(path):
            query = event.get('query')
            if not isinstance(query, str) or not query:
                continue
            variants = [query]
            try:
                variants.append(validate_grbench_read_query(
                    query, row_cap=int(event.get('row_cap') or 20),
                    require_evidence_columns=False, require_anchor=True))
            except ValueError:
                pass
            for variant in variants:
                labels[hashlib.sha256(variant.encode()).hexdigest()] = query_shape(query)
    return labels


def complete_sum(rows: list[dict[str, Any]], key: str) -> int | float | None:
    values = [r.get(key) for r in rows]
    return sum(values) if values and all(isinstance(v, (int, float)) for v in values) else None


def parent_request(tool: dict[str, Any], turns: list[dict[str, Any]]) -> tuple[str | None, str]:
    if tool.get('parent_request_id'):
        return str(tool['parent_request_id']), 'recorded'
    start = tool.get('started_elapsed_ms')
    if not isinstance(start, (int, float)):
        return None, 'unknown'
    preceding = [t for t in turns if isinstance(t.get('started_elapsed_ms'), (int, float))
                 and isinstance(t.get('latency_ms'), (int, float))
                 and t['started_elapsed_ms'] + t['latency_ms'] <= start + .01]
    if not preceding:
        return None, 'unknown'
    latest = max(preceding, key=lambda t: t['started_elapsed_ms'] + t['latency_ms'])
    return latest.get('span_id'), 'derived_serial_intervals'


def tool_rows(case: dict[str, Any], task: dict[str, Any], labels: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    timings = ('latency_ms', 'validator_ms', 'tool_argument_decode_ms', 'bolt_submit_ms',
               'first_record_ms', 'hydrate_ms', 'serialize_ms', 'server_available_ms',
               'server_consumed_ms', 'bolt_consume_ms', 'connection_pool_wait_ms')
    for ordinal, tool in enumerate(case.get('tools') or [], 1):
        parent, provenance = parent_request(tool, case.get('turns') or [])
        name = tool.get('name')
        shape = tool.get('query_shape') or labels.get(tool.get('query_digest'))
        if name == 'inspect_graph_schema':
            shape = 'schema'
        result = 'error' if tool.get('outcome') == 'error' or tool.get('error_code') else (
            'empty_valid' if tool.get('rows') == 0 else (
                'nonempty' if isinstance(tool.get('rows'), int) and tool['rows'] > 0 else 'unclassified'))
        rows.append({
            'pass': task['pass'], 'task_id': task['task_id'], 'trace_id': task['trace_id'],
            'tool_call_id': tool.get('tool_call_id') or f"{task['task_id']}:tool:{ordinal}",
            'tool_id_source': 'recorded' if tool.get('tool_call_id') else 'derived_ordinal',
            'parent_request_id': parent, 'parent_id_source': provenance,
            'tool_ordinal': ordinal, 'name': name, 'query_shape': shape or 'unknown',
            'shape_source': 'recorded' if tool.get('query_shape') else (
                'tool_name' if shape == 'schema' else 'exact_capture_digest' if shape else 'unknown'),
            'result_class': result, 'error_code': tool.get('error_code'),
            'rows': tool.get('rows'), 'result_bytes': tool.get('result_bytes'),
            'result_tokens': tool.get('result_tokens'),
            'started_elapsed_ms': tool.get('started_elapsed_ms'),
            'session_create_ms': tool.get('session_create_ms', tool.get('pool_acquire_ms')),
            **{k: tool.get(k) for k in timings},
        })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()} for r in rows)


def replica_observations(directory: Path, start: float, end: float) -> list[dict[str, Any]]:
    output = []
    for path in sorted((directory / 'workload').glob('replica-*-metrics.jsonl')):
        rows = [r for r in read_rows(path) if r.get('available')
                and start <= r['timestamp_unix_s'] <= end]
        stamps = sorted(r['timestamp_unix_s'] for r in rows)
        covered = bool(len(stamps) >= 2 and stamps[0] - start <= 2.5 and end - stamps[-1] <= 2.5
                       and all(b - a <= 2.5 for a, b in zip(stamps, stamps[1:])))
        output.append({'source': path.name, 'full_window_coverage': covered,
                       'first_unix_s': stamps[0] if stamps else None,
                       'last_unix_s': stamps[-1] if stamps else None,
                       'gauges': {k: distribution([r['metrics'].get(k) for r in rows]) for k in (
                           'vllm:kv_cache_usage_perc', 'vllm:num_requests_running', 'vllm:num_requests_waiting')}})
    return output


def summarize(root: Path, output: Path, *, use_private_captures: bool = False,
              exclude_fixed_corpus: bool = False) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    server = read_rows(root / 'otel/collector-spans.jsonl')
    server_index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for span in server:
        if span.get('name') == 'llm_request':
            server_index[(span['trace_id'], span.get('parent_span_id'))].append(span)
    labels = query_shapes(root, use_private_captures)
    all_tasks, all_requests, all_tools, summaries = [], [], [], []
    excluded_fixed_corpus_passes = []
    for directory in sorted(root.glob('run-*')):
        factor_path = directory / 'otel/factors.json'
        if not factor_path.exists():
            continue
        factors = json.loads(factor_path.read_text())
        pass_receipt_path = directory / 'pass-receipt.json'
        pass_receipt = json.loads(pass_receipt_path.read_text()) if pass_receipt_path.exists() else {}
        if exclude_fixed_corpus and pass_receipt.get('evidence_class') == 'fixed_corpus_serving_diagnostic':
            excluded_fixed_corpus_passes.append(directory.name.removeprefix('run-'))
            continue
        config = pass_receipt.get('config', {})
        rate = factors.get('throughput', {})
        if rate.get('status') != 'observed':
            continue
        start, end = rate['started_unix_s'], rate['finished_unix_s']
        tasks, requests, tools, profiles = [], [], [], []
        pass_name = directory.name.removeprefix('run-')
        lifecycle = {r['run_id']: r for p in directory.glob('e2e/*/shard-*/coordinator-receipts.jsonl') for r in read_rows(p)}
        for shard in directory.glob('e2e/*/shard-*'):
            for case, anchor in iter_case_receipts(shard):
                profiles.extend(case.get('query_plan_replays') or [])
                spans = [s.to_json() for s in build_case_spans(case, lane='grbench-single',
                         topology='single_agent', anchor_unix_s=anchor)]
                case_span = spans[0]
                begun, finished = case_span['start_unix_ns'] / 1e9, case_span['end_unix_ns'] / 1e9
                trace = case_span['trace_id']
                task_id = f"{case.get('run_id')}:{case.get('case_id') or case.get('qid')}"
                turns = case.get('turns') or []
                coordinator = lifecycle.get(case.get('run_id'), {})
                contained = (start <= coordinator.get('runner_started_unix_s', begun)
                             and coordinator.get('runner_finished_unix_s', finished) <= end)
                task = {'pass': pass_name, 'task_id': task_id, 'trace_id': trace,
                        'recorded_task_id': case.get('task_id'),
                        'case_id': case.get('case_id') or case.get('qid'), 'agent_id': 'single_agent',
                        'started_unix_s': begun, 'finished_unix_s': finished,
                        'clock_source': case_span['attributes'].get('seocho.timing.anchor'),
                        'fully_contained': contained, 'llm_calls': len(turns),
                        'tool_calls': len(case.get('tools') or []),
                        'input_tokens': complete_sum(turns, 'input_tokens'),
                        'output_tokens': complete_sum(turns, 'output_tokens'),
                        'max_input_tokens': max((t['input_tokens'] for t in turns if isinstance(t.get('input_tokens'), int)), default=None),
                        'repair_count': case.get('repair_count'), 'query_retry_count': case.get('query_retry_count'),
                        'empty_result_retry_count': case.get('empty_result_retry_count'),
                        'terminal': case.get('terminal'), 'answer_status': case.get('answer_status'),
                        'semantic_accuracy': None, 'wall_ms': (case.get('time') or {}).get('wall_ms'),
                        'context_token_components': None}
                tasks.append(task)
                matching = [s for c in spans if c['name'].startswith('chat ')
                            for s in server_index.get((trace, c['span_id']), [])]
                for row in build_rows(spans, matching):
                    row.update({'pass': pass_name, 'task_id': task_id, 'task_fully_contained': contained,
                                'clock_source': task['clock_source']})
                    turn = next((t for t in turns if t.get('span_id') == row['client_span_id']), {})
                    composition = turn.get('context_composition') or {}
                    row['step_id'] = turn.get('step_id')
                    row['context_components_bytes'] = composition.get('components') if composition.get('unit') == 'utf8_content_bytes' else None
                    row['context_component_unit'] = composition.get('unit')
                    matches = server_index.get((trace, row['client_span_id']), [])
                    if len(matches) == 1:
                        row['server_started_unix_s'] = matches[0]['start_unix_ns'] / 1e9
                        row['server_finished_unix_s'] = matches[0]['end_unix_ns'] / 1e9
                    requests.append(row)
                tools.extend(tool_rows(case, task, labels))
        cohort = [t for t in tasks if t['fully_contained']]
        cohort_ids = {t['task_id'] for t in cohort}
        cr = [r for r in requests if r['task_id'] in cohort_ids]
        ct = [t for t in tools if t['task_id'] in cohort_ids]
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for tool in ct:
            groups[tool['query_shape']].append(tool)
        fields = ('llm_calls', 'tool_calls', 'input_tokens', 'output_tokens', 'max_input_tokens', 'wall_ms', 'repair_count')
        stages = ('queue_s', 'ttft_s', 'prefill_s', 'decode_s', 'gap_before_s', 'decode_tpot_ms', 'client_latency_s')
        prefill = complete_sum(cr, 'prefill_s')
        decode = complete_sum(cr, 'decode_s')
        gauges = dict(factors.get('sampled_gauges') or {})
        if config.get('replicas') == 2 and 'vllm:kv_cache_usage_perc' in gauges:
            gauges['kv_usage_fraction_sum_across_replicas'] = gauges.pop('vllm:kv_cache_usage_perc')
        capability_path = directory / 'workload/graph-capabilities.json'
        graph_system = attach_graph_evidence(
            summarize_graph_system(read_rows(directory / 'workload/graph-system.jsonl'), start, end),
            capabilities=json.loads(capability_path.read_text()) if capability_path.exists() else None,
            tools=ct, profiles=profiles, agent_samples_present=bool(cohort),
            process_samples_present=(directory / 'workload/host-process.csv').exists())
        summary = {'pass': pass_name, 'application_k': factors.get('application_k'),
                   'router_policy': config.get('router_policy'),
                   'serving_layout': '2 x TP=1' if config.get('replicas') == 2 else 'TP=2' if config.get('replicas') == 1 else 'unrecorded',
                   'configured_logical_cpus': config.get('cpu'),
                   'throughput': rate, 'output_tok_s_gpu': factors.get('output_tokens_per_s_per_gpu'),
                   'observed_gpu_count': factors.get('observed_physical_gpu_count'),
                   'cohort_scope': 'tasks fully contained in validated inner window; excludes boundary tasks',
                   'all_pass_tasks': len(tasks), 'cohort_tasks': len(cohort),
                   'agent': {k: distribution([t[k] for t in cohort]) for k in fields},
                   'terminal': dict(Counter(t['terminal'] for t in cohort)),
                   'answer_status': dict(Counter(t['answer_status'] for t in cohort)),
                   'output_kinds': {kind: {'requests': len(rows), 'tokens': complete_sum(rows, 'client_output_tokens')}
                                    for kind in sorted({str(r['client_output_kind']) for r in cr})
                                    if (rows := [r for r in cr if str(r['client_output_kind']) == kind])},
                   'runtime': {k: distribution([r.get(k) for r in cr]) for k in stages},
                   'requests': len(cr), 'matched_requests': sum(r['server_span_present'] for r in cr),
                   'all_pass_requests': len(requests), 'all_pass_matched_requests': sum(r['server_span_present'] for r in requests),
                   'decode_share_of_summed_request_prefill_decode': decode / (prefill + decode)
                       if prefill is not None and decode is not None and prefill + decode else None,
                   'sampled_gauges': gauges, 'counter_deltas': factors.get('counter_deltas'),
                   'graph_system': graph_system,
                   'replica_observations': replica_observations(directory, start, end),
                   'actual_constrained_query_ratio': factors.get('actual_constrained_query_ratio'),
                   'tools': [{'shape': key, 'calls': len(rows), 'latency_ms': distribution([t['latency_ms'] for t in rows]),
                              'result_bytes': distribution([t['result_bytes'] for t in rows]),
                              'result_rows': distribution([t['rows'] for t in rows]),
                              'results': dict(Counter(t['result_class'] for t in rows)),
                              'errors': dict(Counter(t['error_code'] for t in rows if t['error_code']))}
                             for key, rows in sorted(groups.items())],
                   'cypher_shape_coverage': {'classified': sum(t['query_shape'] != 'unknown' for t in ct if t['name'] == 'execute_cypher'),
                                            'calls': sum(t['name'] == 'execute_cypher' for t in ct)},
                   'examples': []}
        candidates = sorted(cohort, key=lambda t: t['wall_ms'] or 0)
        chosen = [candidates[int((len(candidates) - 1) * q)] for q in (.5, .95)] if candidates else []
        failed = next((t for t in reversed(candidates) if t['terminal'] != 'final'), None)
        if failed and failed not in chosen:
            chosen.append(failed)
        samples = read_rows(directory / 'workload/vllm-metrics.jsonl')
        for task in chosen:
            summary['examples'].append({'task': task, 'requests': [r for r in requests if r['task_id'] == task['task_id']],
                'tools': [t for t in tools if t['task_id'] == task['task_id']],
                'runtime_samples': [{'elapsed_s': s['timestamp_unix_s'] - task['started_unix_s'],
                                     'running': s.get('metrics', {}).get('vllm:num_requests_running'),
                                     'waiting': s.get('metrics', {}).get('vllm:num_requests_waiting'),
                                     'kv_fraction': s.get('metrics', {}).get('vllm:kv_cache_usage_perc') if config.get('replicas') != 2 else None,
                                     'kv_replica_fraction_sum': s.get('metrics', {}).get('vllm:kv_cache_usage_perc') if config.get('replicas') == 2 else None}
                                    for s in samples if task['started_unix_s'] <= s['timestamp_unix_s'] <= task['finished_unix_s']]})
        summaries.append(summary)
        all_tasks.extend(tasks)
        all_requests.extend(requests)
        all_tools.extend(tools)
    result = {'schema_version': 'agentic_os.three_view_characterization.v1', 'run_id': root.name, 'passes': summaries,
              'excluded_fixed_corpus_passes': excluded_fixed_corpus_passes,
              'counts': {'tasks': len(all_tasks), 'requests': len(all_requests), 'tools': len(all_tools)},
              'limitations': [
                  'Single ordered pass per condition; no independent repeats or confidence intervals.',
                  'Throughput uses engine output counter and same-window completions; task token means use contained tasks.',
                  'Completed or answered is not semantic accuracy. Replayed output lengths are controlled.',
                  'Request durations overlap across tasks and are not exclusive GPU time.',
                  'Router KV fractions are summed across replicas, not a global usage fraction; consult per-backend samples and coverage.',
                  'TTFT overlaps queue and prefill; do not stack these as disjoint stages.',
                  'Historical process-window clock approximates the case anchor; coordinator fallback includes startup.',
                  'Query labels are lexical shapes from exact capture digest matches; missing matches are unknown.',
                  'Session creation is not connection-pool wait; Bolt submit may include lazy acquisition.',
                  'Result bytes are not tokens. Reasoning/Cypher argument and context token components unobserved.',
                  'Engine sequence counts, grammar CPU and kernel gaps are outside this export; consult companion engine/profile receipts when available.',
                  'DB page-cache occupancy and offload transfers require separate telemetry and are not inferred from these traces.',
                  'When GPU samples are absent inside the window, primary per-GPU rate remains null.',
                  'Consult this session lifecycle and copy receipt for artifact verification status.',
              ]}
    for name, rows in (('tasks', all_tasks), ('requests', all_requests), ('tools', all_tools)):
        write_csv(output / f'{name}.csv', rows)
    (output / 'characterization.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    template = Path(__file__).with_name('agentic_os_characterization.html').read_text()
    (output / 'index.html').write_text(template.replace('__DATA__', json.dumps(result, ensure_ascii=False).replace('<', '\\u003c')))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--use-private-captures', action='store_true')
    parser.add_argument('--exclude-fixed-corpus', action='store_true', help='Keep fixed-corpus serving diagnostics out of the agent chartbook')
    args = parser.parse_args()
    result = summarize(args.run_root, args.output_dir, use_private_captures=args.use_private_captures,
                       exclude_fixed_corpus=args.exclude_fixed_corpus)
    print(json.dumps({'passes': len(result['passes']), **result['counts']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
