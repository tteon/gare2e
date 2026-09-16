#!/usr/bin/env python3
"""Paper-inspired, content-free chartbook from preserved characterization exports.

No inference or service calls. Matplotlib is required only to render figures.
Input can contain plain CSV or gzip CSV. Existing reports are never modified.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def nonnegative(value: Any) -> float | None:
    result = number(value)
    return result if result is not None and result >= 0 else None


def values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [n for row in rows if (n := nonnegative(row.get(key))) is not None]


def read_csv(directory: Path, name: str) -> tuple[list[dict[str, Any]], Path]:
    path = directory / f'{name}.csv'
    if path.exists():
        with path.open(newline='') as handle:
            return list(csv.DictReader(handle)), path
    path = directory / f'{name}.csv.gz'
    with gzip.open(path, 'rt', newline='') as handle:
        return list(csv.DictReader(handle)), path


def split_intervals(start: float, end: float, llm: list[tuple[float, float]],
                    tool: list[tuple[float, float]]) -> dict[str, float]:
    """Union clipped intervals; overlapping work is counted once in task wall time."""
    events: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    events[start]
    events[end]
    for kind, intervals in enumerate((llm, tool)):
        for left, right in intervals:
            left, right = max(start, left), min(end, right)
            if right > left:
                events[left][kind] += 1
                events[right][kind] -= 1
    result = dict.fromkeys(('LLM only', 'Tool only', 'Overlap', 'Unattributed'), 0.0)
    active = [0, 0]
    previous = start
    for stamp, delta in sorted(events.items()):
        key = ('Overlap' if all(active) else 'LLM only' if active[0]
               else 'Tool only' if active[1] else 'Unattributed')
        result[key] += stamp - previous
        active = [a + b for a, b in zip(active, delta)]
        previous = stamp
    return result


def characterize(summary: dict[str, Any], tasks: list[dict[str, Any]],
                 requests: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = [t for t in tasks if str(t.get('fully_contained')).lower() == 'true']
    ids = {t['task_id'] for t in tasks}
    requests = [r for r in requests if r['task_id'] in ids]
    tools = [t for t in tools if t['task_id'] in ids]
    task_requests: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_tools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in requests:
        task_requests[row['task_id']].append(row)
    for row in tools:
        task_tools[row['task_id']].append(row)
    components: Counter[str] = Counter()
    time_tasks = 0
    tool_rounds, peak_final, peak_other = [], [], []
    context_progress: dict[int, list[float]] = defaultdict(list)
    activity = [Counter() for _ in range(4)]
    activity_unknown, parent_sources = 0, Counter()
    for task in tasks:
        reqs, calls = task_requests[task['task_id']], task_tools[task['task_id']]
        start, end = number(task.get('started_unix_s')), number(task.get('finished_unix_s'))
        llm_intervals, tool_intervals = [], []
        complete = start is not None and end is not None and end > start
        for row in reqs:
            left, right = number(row.get('request_started_unix_s')), number(row.get('request_finished_unix_s'))
            if left is None or right is None or right < left:
                complete = False
            else:
                llm_intervals.append((left, right))
            ordinal, tokens = number(row.get('request_ordinal')), nonnegative(row.get('client_input_tokens'))
            if ordinal is not None and tokens is not None:
                context_progress[int(ordinal)].append(tokens)
        for row in calls:
            offset, latency = nonnegative(row.get('started_elapsed_ms')), nonnegative(row.get('latency_ms'))
            if offset is None or latency is None or start is None:
                complete = False
            else:
                tool_intervals.append((start + offset / 1000, start + (offset + latency) / 1000))
        # Counts are checked too: absent rows must not turn into unattributed time.
        complete = complete and len(reqs) == number(task.get('llm_calls')) and len(calls) == number(task.get('tool_calls'))
        if complete:
            components.update(split_intervals(start, end, llm_intervals, tool_intervals))
            time_tasks += 1
        by_parent = {r['client_span_id']: r for r in reqs if r.get('client_span_id')}
        valid_parents = {t['parent_request_id'] for t in calls if t.get('parent_request_id') in by_parent}
        # This is a linked-tool-round lower bound if any parent is unknown.
        tool_rounds.append(len(valid_parents))
        count = max(values(reqs, 'request_ordinal'), default=0)
        previous_error = False
        for call in sorted(calls, key=lambda row: number(row.get('tool_ordinal')) or 0):
            parent = by_parent.get(call.get('parent_request_id'))
            ordinal = number(parent.get('request_ordinal')) if parent else None
            if ordinal is None or count < 1:
                activity_unknown += 1
            else:
                bucket = min(3, int(4 * (ordinal - 1) / count))
                label = ('Query after error' if previous_error and call.get('name') == 'execute_cypher'
                         else 'Schema discovery' if call.get('name') == 'inspect_graph_schema'
                         else 'Graph query' if call.get('name') == 'execute_cypher' else 'Other')
                activity[bucket][label] += 1
                parent_sources[call.get('parent_id_source') or 'unknown'] += 1
            previous_error = call.get('result_class') == 'error'
        peak = nonnegative(task.get('max_input_tokens'))
        if peak is not None:
            (peak_final if task.get('terminal') == 'final' else peak_other).append(peak)
    cache_rows = []
    for row in requests:
        prompt, cached = nonnegative(row.get('client_input_tokens')), nonnegative(row.get('client_cached_tokens'))
        output = nonnegative(row.get('client_output_tokens'))
        if prompt is not None and cached is not None and output is not None and cached <= prompt:
            cache_rows.append((cached, prompt - cached, output))
    stage_rows = [r for r in requests if number(r.get('server_span_match_count')) == 1
                  and nonnegative(r.get('prefill_s')) is not None and nonnegative(r.get('decode_s')) is not None]
    tool_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tools:
        tool_groups[row.get('query_shape') or 'unknown'].append(row)
    rankings = []
    for shape, rows in tool_groups.items():
        latencies, sizes = values(rows, 'latency_ms'), values(rows, 'result_bytes')
        known = [r for r in rows if r.get('result_class') in ('error', 'empty_valid', 'nonempty')]
        failures = sum(r.get('result_class') == 'error' for r in known)
        rankings.append({'shape': shape, 'calls': len(rows), 'latency_n': len(latencies),
                         'mean_ms': mean(latencies) if latencies else None,
                         'mean_bytes': mean(sizes) if sizes else None, 'bytes_n': len(sizes),
                         'classified_n': len(known), 'errors': failures,
                         'error_fraction': failures / len(known) if known else None})
    return {'pass': summary['pass'], 'summary': summary, 'cohort_n': len(tasks),
            'request_n': len(requests), 'tool_n': len(tools),
            'task_wall_seconds': dict(components), 'time_task_n': time_tasks,
            'cache_request_n': len(cache_rows),
            'cache_mean_tokens': [mean(x) for x in zip(*cache_rows)] if cache_rows else None,
            'stage_request_n': len(stage_rows),
            'stage_sums_seconds': [sum(float(r[key]) for r in stage_rows) for key in ('prefill_s', 'decode_s')] if stage_rows else None,
            'tool_names': dict(Counter(r.get('name') or 'unknown' for r in tools)),
            'tool_shapes': {k: len(v) for k, v in tool_groups.items()}, 'tool_rankings': rankings,
            'activity_quartiles': [dict(c) for c in activity], 'activity_unknown_n': activity_unknown,
            'parent_sources': dict(parent_sources), 'llm_calls': values(tasks, 'llm_calls'),
            'linked_tool_rounds': tool_rounds, 'input_tokens': values(tasks, 'input_tokens'),
            'output_tokens': values(tasks, 'output_tokens'), 'peak_tokens': values(tasks, 'max_input_tokens'),
            'peak_final': peak_final, 'peak_other': peak_other,
            'context_progress': dict(context_progress),
            'termination': dict(Counter(t.get('terminal') or 'unknown' for t in tasks))}


COLORS = ['#2563eb', '#f59e0b', '#10b981', '#94a3b8', '#a855f7', '#ec4899', '#64748b', '#06b6d4']


def capacity_rows(summary: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Never reinterpret a sum of per-replica KV fractions as global utilization."""
    if summary.get('serving_layout') == '2 x TP=1':
        return [(r['source'].replace('-metrics.jsonl', ''), r['gauges'])
                for r in summary.get('replica_observations', []) if r.get('full_window_coverage')]
    return [('Engine', summary.get('sampled_gauges') or {})]


def save_svg(fig: Any, path: Path) -> None:
    fig.savefig(path, metadata={'Date': None})
    # Matplotlib emits trailing spaces in path coordinates; keep Git diffs clean.
    path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines()) + '\n')


def horizontal(ax: Any, labels: list[str], amounts: list[float], title: str, unit: str) -> None:
    ax.set_title(title, loc='left', fontsize=11, weight='bold')
    if not labels:
        ax.text(.5, .5, 'Not observed', ha='center', transform=ax.transAxes)
        ax.set_axis_off()
        return
    bars = ax.barh(labels, amounts, color=COLORS[:len(labels)] if len(labels) <= len(COLORS) else COLORS[0])
    ax.invert_yaxis()
    ax.set_xlabel(unit)
    ax.margins(x=.25)
    ax.bar_label(bars, fmt='%.2f', padding=3, fontsize=8)


def boxes(ax: Any, arrays: list[list[float]], labels: list[str], title: str, unit: str) -> None:
    ax.set_title(title, loc='left', fontsize=11, weight='bold')
    valid = [(a, f'{label}\nn={len(a)}') for a, label in zip(arrays, labels) if a]
    if valid:
        ax.boxplot([v[0] for v in valid], showfliers=False)
        ax.set_xticks(range(1, len(valid) + 1), [v[1] for v in valid])
        ax.set_ylabel(unit)
    else:
        ax.text(.5, .5, 'Not observed', ha='center', transform=ax.transAxes)


def draw_condition(record: dict[str, Any], directory: Path, key: str) -> dict[str, str]:
    import matplotlib.pyplot as plt

    paths = {}
    header = f"{record['session']} / {record['pass']} | contained tasks n={record['cohort_n']}"
    def save(fig: Any, category: str, note: str) -> None:
        fig.suptitle(header, fontsize=14, weight='bold', x=.02, ha='left')
        fig.text(.02, .02, note, fontsize=9, color='#475569', va='bottom')
        fig.tight_layout(rect=(.01, .08, .99, .94), h_pad=3, w_pad=4)
        name = f'{key}-{category}.svg'
        save_svg(fig, directory / name)
        plt.close(fig)
        paths[category] = name

    fig, axes = plt.subplots(2, 3, figsize=(17, 10))
    wall = record['task_wall_seconds']
    total = sum(wall.values())
    horizontal(axes[0, 0], list(wall), [100 * x / total for x in wall.values()] if total else [],
               f"Task time composition (timed n={record['time_task_n']})", '% of summed task wall time')
    cache = record['cache_mean_tokens']
    horizontal(axes[0, 1], ['Cached input', 'Uncached input', 'Output'] if cache else [], cache or [],
               f"Context cache (valid requests {record['cache_request_n']}/{record['request_n']})", 'Mean tokens per valid request')
    stages = record['stage_sums_seconds'] or []
    denom = sum(stages)
    horizontal(axes[0, 2], ['Prefill', 'Decode'] if denom else [],
               [100 * v / denom for v in stages] if denom else [],
               f"LLM stages (exact requests {record['stage_request_n']}/{record['request_n']})", '% of summed request (prefill + decode)')
    data = record['summary']
    rates = data.get('throughput', {})
    axes[1, 0].axis('off')
    rate = data.get('output_tok_s_gpu')
    gpu_text = f'{rate:.2f}' if rate is not None else 'Not observed'
    ts = number(rates.get('task_completions_per_s'))
    axes[1, 0].text(0, .94, f'Output tok/s/GPU: {gpu_text}', fontsize=16, weight='bold')
    lines = [f"Task/s: {ts:.3f}" if ts is not None else 'Task/s: unknown',
             f"Input / task: {mean(record['input_tokens']):.1f}" if record['input_tokens'] else 'Input / task: unknown',
             f"Output / task: {mean(record['output_tokens']):.1f}" if record['output_tokens'] else 'Output / task: unknown',
             f"Application K: {data.get('application_k')}; GPUs observed: {data.get('observed_gpu_count')}",
             f"Router: {data.get('router_policy') or 'not recorded in this export'}"]
    axes[1, 0].text(0, .77, '\n\n'.join(lines), fontsize=10, va='top')
    capacity = capacity_rows(data)
    running, kv = {}, {}
    for label, gauges in capacity:
        for metric, name in [('vllm:num_requests_running', 'running'), ('vllm:num_requests_waiting', 'waiting')]:
            value = nonnegative((gauges.get(metric) or {}).get('mean'))
            if value is not None:
                running[f'{label} {name}'] = value
        for stat in ('mean', 'max'):
            value = nonnegative((gauges.get('vllm:kv_cache_usage_perc') or {}).get(stat))
            if value is not None and value <= 1:
                kv[f'{label} {stat}'] = 100 * value
    horizontal(axes[1, 1], list(running), list(running.values()), 'Actual request supply', 'Sampled mean request count')
    horizontal(axes[1, 2], list(kv), list(kv.values()), 'KV capacity pressure', '% engine KV cache usage')
    save(fig, 'runtime', 'Task intervals are clipped unions; overlap is counted once. LLM client time includes queue/network.\n'
         'Stages exclude queue/network and are NOT GPU-exclusive time. Uncached input may include recomputation. Missing telemetry is not zero.\n'
         'Running requests are not measured decode sequences. Replica means require full-window samples; KV fractions are never summed.')

    fig, axes = plt.subplots(2, 3, figsize=(17, 10))
    names, shapes = record['tool_names'], record['tool_shapes']
    horizontal(axes[0, 0], list(names), list(names.values()), 'Tool call types', 'Tool calls')
    horizontal(axes[0, 1], list(shapes), list(shapes.values()), 'Graph work types (Bash analogue)', 'Tool calls')
    ranked = sorted((r for r in record['tool_rankings'] if r['mean_ms'] is not None), key=lambda r: r['mean_ms'], reverse=True)[:3]
    horizontal(axes[0, 2], [f"{r['shape']} (n={r['latency_n']})" for r in ranked],
               [r['mean_ms'] for r in ranked], 'Top 3 by mean tool latency', 'Mean elapsed ms')
    for i, row in enumerate(ranked):
        size = f"{row['mean_bytes']:.0f}" if row['mean_bytes'] is not None else 'unknown'
        axes[0, 2].text(.02, -.25 - i * .07, f"{row['shape']}: mean return {size} bytes (n={row['bytes_n']})",
                        transform=axes[0, 2].transAxes, fontsize=8)
    ranked = sorted((r for r in record['tool_rankings'] if r['error_fraction'] is not None),
                    key=lambda r: (r['error_fraction'], r['classified_n']), reverse=True)[:3]
    horizontal(axes[1, 0], [f"{r['shape']} ({r['errors']}/{r['classified_n']})" for r in ranked],
               [100 * r['error_fraction'] for r in ranked], 'Top 3 by observed error rate', '% errors / classified outcomes')
    ax = axes[1, 1]
    labels = ['Schema discovery', 'Graph query', 'Query after error', 'Other']
    totals = [sum(q.values()) for q in record['activity_quartiles']]
    bottom = [0.0] * 4
    for i, label in enumerate(labels):
        heights = [100 * q.get(label, 0) / n if n else 0 for q, n in zip(record['activity_quartiles'], totals)]
        ax.bar(range(4), heights, bottom=bottom, label=label, color=COLORS[i])
        bottom = [a + b for a, b in zip(bottom, heights)]
    ax.set_xticks(range(4), [f'Q{i + 1}\nn={n}' for i, n in enumerate(totals)])
    ax.set_title('Activity over LLM-call progress', loc='left', fontsize=11, weight='bold')
    ax.set_ylabel('% of linked calls in each quartile')
    ax.legend(fontsize=8, loc='upper center', bbox_to_anchor=(.5, -.18), ncol=2)
    axes[1, 2].axis('off')
    axes[1, 2].text(0, .95, 'Interpretation / coverage', fontsize=12, weight='bold')
    notes = [f"Total tool calls: {record['tool_n']}",
             f"Unknown activity parent: {record['activity_unknown_n']}",
             'Quartiles use LLM-call ordinal, not time.',
             'Top-3 entries are graph work types.',
             'Unknown result classes leave error denominator.',
             'Return size is UTF-8 bytes, not tokens.',
             'Error rate 0% does not imply answer correctness.']
    axes[1, 2].text(0, .83, '\n\n'.join(notes), fontsize=10, va='top')
    save(fig, 'tool', f"Parent linkage provenance: {dict(record['parent_sources'])}.\n"
         'Valid empty rows are not failures. Activity is a rule-based proxy, not inferred intent; all graph queries are read-only.')

    fig, axes = plt.subplots(2, 3, figsize=(17, 10))
    boxes(axes[0, 0], [record['llm_calls'], record['linked_tool_rounds']], ['LLM calls', 'Linked tool rounds'],
          'Agent execution counts', 'Count per task')
    boxes(axes[0, 1], [record['peak_tokens'], record['input_tokens']], ['Peak request input', 'Sum of request inputs'],
          'Resident-length proxy vs accumulated demand', 'Tokens per task')
    boxes(axes[1, 0], [record['peak_final'], record['peak_other']], ['Final termination', 'Other termination'],
          'Peak context by termination (accuracy unscored)', 'Peak input tokens')
    ax = axes[1, 1]
    progress = sorted((int(k), v) for k, v in record['context_progress'].items())
    if progress:
        import numpy as np
        x = [p[0] for p in progress]
        ax.plot(x, [np.median(p[1]) for p in progress], marker='o', label='p50')
        ax.fill_between(x, [np.percentile(p[1], 25) for p in progress],
                        [np.percentile(p[1], 75) for p in progress], alpha=.2, label='p25-p75')
        ax.set_xticks(x, [f'{i}\nn={len(v)}' for i, v in progress], fontsize=8)
        ax.legend(fontsize=8)
    ax.set_title('Context growth by LLM call', loc='left', fontsize=11, weight='bold')
    ax.set_xlabel('LLM call ordinal; surviving requests n')
    ax.set_ylabel('Input tokens per request')
    kinds = record['summary'].get('output_kinds', {})
    observed = {k: n for k, v in kinds.items() if (n := nonnegative(v.get('tokens'))) is not None}
    horizontal(axes[0, 2], list(observed), list(observed.values()),
               f'Generated tokens by response kind ({len(observed)}/{len(kinds)} known)', 'Observed output tokens (contained cohort)')
    horizontal(axes[1, 2], list(record['termination']), list(record['termination'].values()),
               'Task termination reasons', 'Tasks')
    save(fig, 'agent', 'LLM calls differ from paper tool-bearing turns. Linked tool rounds count unique parent calls; missing parents give a lower bound.\n'
         'Boxes: median/IQR, whiskers 1.5 IQR, outliers hidden. Final is not judged success. Later calls have survivor selection.\n'
         'Output kinds classify entire responses, not isolated Cypher/reasoning tokens. Peak input is not measured KV residency.')
    return paths


def render(sources: list[Path], output: Path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    matplotlib.rcParams.update({'svg.fonttype': 'none', 'font.family': 'DejaVu Sans',
                               'axes.spines.top': False, 'axes.spines.right': False,
                               'svg.hashsalt': 'agentic-os-chartbook-v1'})
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite chartbook: {output}')
    output.mkdir(parents=True)
    records, provenance = [], []
    for source in sources:
        path = source / 'characterization.json'
        summary = json.loads(path.read_text())
        tables = {}
        for name in ('tasks', 'requests', 'tools'):
            tables[name], table_path = read_csv(source, name)
            provenance.append({'source': f'{source.name}/{table_path.name}', 'sha256': hashlib.sha256(table_path.read_bytes()).hexdigest()})
        provenance.append({'source': f'{source.name}/{path.name}', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for entry in summary['passes']:
            filtered = {name: [r for r in rows if r['pass'] == entry['pass']] for name, rows in tables.items()}
            record = characterize(entry, **filtered)
            record['session'] = source.name
            key = re.sub(r'[^a-zA-Z0-9_-]', '-', f'{source.name}-{entry["pass"]}')
            record['figures'] = draw_condition(record, output, key)
            records.append(record)
    # Same-session K curves; no cross-host pooling, missing rates stay missing.
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    for source in sources:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            match = re.fullmatch(r'(base|mtp)(-r\d+)?-k\d+', record['pass'])
            if record['session'] == source.name and match:
                label = ('Baseline' if match[1] == 'base' else 'MTP') + (match[2] or '')
                groups[label].append(record)
        for label, group in sorted(groups.items()):
            selected = sorted(group, key=lambda r: r['summary']['application_k'])
            for ax, metric, title in zip(axes, ['output_tok_s_gpu', 'task_completions_per_s'], ['Output tok/s/GPU', 'Task/s']):
                pairs = [(r['summary']['application_k'], number(r['summary'].get(metric) if metric == 'output_tok_s_gpu'
                          else r['summary'].get('throughput', {}).get(metric))) for r in selected]
                pairs = [(k, v) for k, v in pairs if v is not None]
                if pairs:
                    ax.plot([p[0] for p in pairs], [p[1] for p in pairs], marker='o', label=f'{source.name}: {label}')
                ax.set_title(title)
                ax.set_xlabel('Application concurrency K')
                ax.grid(alpha=.2)
    for ax in axes:
        if ax.lines:
            ax.legend(fontsize=8)
        else:
            ax.text(.5, .5, 'No comparable K sweep in this source',
                    ha='center', va='center', transform=ax.transAxes)
    fig.suptitle('Measured concurrency response — single runs, no confidence intervals')
    fig.tight_layout()
    save_svg(fig, output / 'throughput-overview.svg')
    fig.savefig(output / 'throughput-overview.png', dpi=160)
    plt.close(fig)
    (output / 'chart-data.json').write_text(json.dumps({'schema_version': 1, 'inputs': provenance, 'conditions': records}, indent=2, allow_nan=False) + '\n')
    options = ''.join(f'<option value="{i}">{html.escape(r["session"] + " / " + r["pass"])}</option>' for i, r in enumerate(records))
    payload = json.dumps([{'session': r['session'], 'pass': r['pass'], 'figures': r['figures'],
                          'cohort_n': r['cohort_n'], 'request_n': r['request_n'], 'stage_request_n': r['stage_request_n']} for r in records]).replace('<', '\\u003c')
    page = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Graph Agentic RAG — Characterization charts</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#f3f6fa;color:#142236;max-width:1250px;margin:auto;padding:24px}h1,h2{line-height:1.25}section,.intro{background:white;border:1px solid #dce4ed;border-radius:12px;padding:24px;margin:20px 0}img{width:100%;height:auto}select{padding:10px;max-width:100%;font:inherit}.controls{position:sticky;top:0;background:#f3f6faf5;padding:14px 0;z-index:2}.note{color:#475569;font-size:14px}a{color:#175acb}nav{display:flex;gap:20px;flex-wrap:wrap}</style>
<h1>Graph Agentic RAG: 세 관점의 실행 분석</h1>
<div class="intro">주지표는 <b>output tok/s/GPU</b>이며 task/s와 task당 입력·출력을 함께 봅니다.
<p><a href="https://arxiv.org/pdf/2605.26297">Agentic AI Workload Characteristics</a>의 Runtime / Tool / Agent 구성을 Graph RAG에 적용했습니다. 논문의 수치를 재사용한 차트가 아니라 실제 저장된 실험 결과의 재분석입니다.</p>
<p class="note">Throughput은 공통 측정 창의 engine 카운터와 관측 GPU 수를 사용합니다. 아래 행동·시간 분포는 그 창 안에서 시작하고 끝난 task만 포함합니다. 느린 조건은 완료 표본이 적어 선택 편향이 큽니다. 실험별 단일 실행이며 조건 간 정확도 동등성은 검증되지 않았습니다. 이전 세션의 하드웨어·아티팩트 한계는 각 원본 보고서에 유지됩니다.</p>
<a href="chart-data.json">차트 수치·입력 SHA256</a> · <a href="README.md">측정 정의 / 한계</a>
</div><section><h2>동시성에 따른 처리량</h2><img src="throughput-overview.svg" alt="Baseline and MTP throughput versus concurrency"><a href="throughput-overview.png">PNG 다운로드</a></section>
<div class="controls"><label for="condition">실험 조건 </label><select id="condition">OPTIONS</select>
<nav><a href="#runtime">1. Runtime</a><a href="#tool">2. Tool</a><a href="#agent">3. Agent</a></nav><div id="coverage" class="note"></div></div>
<section id="runtime"><h2>1. Runtime Performance Characterization</h2><p>Task 시간 구성 · prefix cache · prefill/decode · 생성 처리량</p><img id="runtime-img" alt="Runtime breakdown"><a id="runtime-download">SVG 다운로드</a></section>
<section id="tool"><h2>2. Tool Call Characterization</h2><p>Graph 작업 종류 · 가장 느린 호출 · 오류율 · 실행 진행도별 활동</p><img id="tool-img" alt="Tool call breakdown"><a id="tool-download">SVG 다운로드</a></section>
<section id="agent"><h2>3. Agent Execution Characterization</h2><p>LLM 호출과 tool round · context 수요 · 종료 유형별 분포 · 턴별 context 증가</p><img id="agent-img" alt="Agent execution breakdown"><a id="agent-download">SVG 다운로드</a></section>
<script>const conditions=PAYLOAD;const select=document.getElementById('condition');function update(){const r=conditions[Number(select.value)];document.getElementById('coverage').textContent=`${r.session} / ${r.pass}: contained tasks ${r.cohort_n}; exact prefill/decode ${r.stage_request_n}/${r.request_n} requests`;for(const kind of ['runtime','tool','agent']){document.getElementById(kind+'-img').src=r.figures[kind];const a=document.getElementById(kind+'-download');a.href=r.figures[kind];a.download=r.figures[kind];}history.replaceState(null,'','#condition='+select.value);}select.addEventListener('change',update);const m=location.hash.match(/^#condition=(\d+)$/);if(m&&Number(m[1])<conditions.length)select.value=m[1];update();</script></html>'''
    (output / 'index.html').write_text(page.replace('OPTIONS', options).replace('PAYLOAD', payload))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.source, args.output)


if __name__ == '__main__':
    main()
