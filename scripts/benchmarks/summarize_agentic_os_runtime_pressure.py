#!/usr/bin/env python3
"""Portable stage1/2/3 numerical export and charts from preserved live receipts."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def collect(root: Path) -> dict[str, Any]:
    conditions, sources = [], {}
    for directory in sorted(root.glob('run-*')):
        receipt = read(directory / 'pass-receipt.json')
        factors = read(directory / 'otel/factors.json')
        engine = read(directory / 'otel/engine-steps.json')
        kernel = read(directory / 'otel/kernel-profile.json')
        admission = read(directory / 'otel/trace-admission.json')
        if not receipt:
            continue
        name = receipt['pass_id']
        category = 'context' if name.startswith('context-') else 'saturation' if name.startswith(('base-r', 'mtp-r')) else 'instrumentation'
        conditions.append(dict(id=name, category=category, receipt=receipt, factors=factors,
                               engine=engine, kernel=kernel, trace_admission=admission))
        for path in directory.glob('otel/*.json'):
            sources[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        sources[str((directory / 'pass-receipt.json').relative_to(root))] = hashlib.sha256((directory / 'pass-receipt.json').read_bytes()).hexdigest()
    return dict(schema_version='seocho.runtime_pressure_report.v1', conditions=conditions, source_sha256=sources)


def render(data: dict[str, Any], output: Path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    plt.rcParams.update({'font.size': 10, 'figure.facecolor': '#f8fafc', 'axes.facecolor': '#ffffff'})
    rows = data['conditions']
    cells = [r for r in rows if r['category'] == 'saturation' and r['factors']]
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    for name, color in [('base', '#2563eb'), ('mtp', '#e76f51')]:
        for rep, style in [(1, '-o'), (2, '--s')]:
            group = sorted([r for r in cells if r['id'].startswith(f'{name}-r{rep}-')], key=lambda r: r['receipt']['config']['k'])
            ks = [r['receipt']['config']['k'] for r in group]
            values = [
                [r['factors'].get('output_tokens_per_s_per_gpu') for r in group],
                [r['factors'].get('throughput', {}).get('task_completions_per_s') for r in group],
                [(r['engine'].get('schedules', {}).get('decode_sequences') or {}).get('mean') for r in group],
                [(r['engine'].get('schedules', {}).get('waiting_sequences') or {}).get('mean') for r in group],
            ]
            for ax, ys in zip(axes.flat, values):
                ax.plot(ks, [np.nan if v is None else v for v in ys], style, color=color, label=f'{name} repeat{rep}')
    for ax, label in zip(axes.flat, ['Output tok/s/GPU', 'Task completions/s', 'Mean scheduled decode sequences', 'Mean engine waiting sequences']):
        ax.set(xlabel='Application K', ylabel=label, xticks=[8, 64, 128, 256])
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.suptitle('Same-host baseline / MTP: application concurrency and actual engine work\nTwo sweeps; engine max_num_seqs=128. Request times overlap.', fontsize=13)
    fig.tight_layout()
    fig.savefig(output / 'saturation.svg')
    plt.close(fig)

    context = [r for r in rows if r['category'] == 'context' and r['factors']]
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    labels = [r['id'].replace('context-', '').replace('distributed', 'dist') for r in context]
    values = [
        [r['factors'].get('output_tokens_per_s_per_gpu') for r in context],
        [r['factors'].get('throughput', {}).get('task_completions_per_s') for r in context],
        [(r['factors'].get('sampled_gauges', {}).get('vllm:kv_cache_usage_perc') or {}).get('max') for r in context],
        [r['factors'].get('counter_deltas', {}).get('vllm:num_preemptions') for r in context],
    ]
    for ax, ys, label in zip(axes.flat, values, ['Output tok/s/GPU', 'Two-call tasks/s', 'Maximum KV utilization fraction', 'Preemptions in common window']):
        ax.bar(range(len(context)), [np.nan if v is None else v for v in ys], color=['#2563eb' if r['receipt']['config']['locality'] == 'shared' else '#e76f51' for r in context])
        ax.set_xticks(range(len(labels)), labels, rotation=40, ha='right', fontsize=8)
        ax.set_ylabel(label)
        ax.grid(axis='y', alpha=.2)
    fig.suptitle('Actual graph-corpus prefixes: length, locality and KV pressure\nFixed 128-token outputs; two calls/task. Serving diagnostics; quality unscored.', fontsize=13)
    fig.tight_layout()
    fig.savefig(output / 'context-pressure.svg')
    plt.close(fig)

    body = []
    for r in rows:
        rate = r['factors'].get('output_tokens_per_s_per_gpu')
        seq = (r['engine'].get('schedules', {}).get('decode_sequences') or {}).get('mean')
        matched = r['trace_admission'].get('exact_server_matches')
        values = [r['id'], r['receipt'].get('exit_code'), None if rate is None else round(rate, 2),
                  None if seq is None else round(seq, 2), matched]
        body.append('<tr>' + ''.join('<td>' + html.escape('unknown' if v is None else str(v)) + '</td>' for v in values) + '</tr>')
    (output / 'index.html').write_text('''<!doctype html><meta charset="utf-8"><title>Engine, saturation and KV pressure</title>
<style>body{font:16px system-ui;margin:2rem auto;max-width:1400px;color:#172033}img{width:100%}td,th{padding:.5rem;text-align:left;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style>
<h1>Engine observations → saturation → context pressure</h1>
<p>Only preserved live receipts are plotted. Unknown values are not zero. Agent comparisons and fixed-corpus serving diagnostics are separate. Detailed numerical factors, engine CPU/grammar/KV and profiler results are in <a href="conditions.json">conditions.json</a>.</p>
<h2>Same-host baseline / MTP</h2><img src="saturation.svg" alt="Throughput and observed engine sequences across K">
<h2>Actual context and prefix locality</h2><img src="context-pressure.svg" alt="Context-pressure throughput and KV measurements">
<h2>Coverage</h2><table><tr><th>Condition</th><th>Exit</th><th>tok/s/GPU</th><th>Mean decode sequences</th><th>Exact server joins</th></tr>'''
        + ''.join(body) + '</table>')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-charts', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    data = collect(args.run_root)
    (args.output / 'conditions.json').write_text(json.dumps(data, indent=2) + '\n')
    if not args.no_charts:
        render(data, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
