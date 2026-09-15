#!/usr/bin/env python3
"""Summarize per-rank GPU kernel interval unions from private torch traces."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from summarize_agentic_os_engine_steps import distribution


def intervals_summary(intervals: list[tuple[float, float]]) -> dict[str, Any]:
    if not intervals:
        return dict(kernel_events=0, observed=False)
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if end < start:
            raise ValueError('negative kernel duration')
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    elapsed = merged[-1][1] - merged[0][0]
    busy = sum(end - start for start, end in merged)
    return dict(kernel_events=len(intervals), observed=elapsed > 0,
                first_to_last_kernel_us=elapsed, kernel_union_us=busy,
                gap_fraction=1 - busy / elapsed if elapsed else None,
                gap_us=distribution([b[0] - a[1] for a, b in zip(merged, merged[1:])]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports = []
    for path in sorted(args.source.glob('*.json.gz')):
        with gzip.open(path, 'rt') as stream:
            events = json.load(stream)['traceEvents']
        devices: dict[str, list[tuple[float, float]]] = {}
        for event in events:
            if event.get('cat') == 'kernel' and event.get('ph') == 'X':
                key = str(event.get('args', {}).get('device', event.get('pid')))
                devices.setdefault(key, []).append((float(event['ts']), float(event['ts']) + float(event['dur'])))
        reports.append(dict(trace_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            devices={key: intervals_summary(value) for key, value in devices.items()}))
    observed_traces = sum(any(d['observed'] for d in r['devices'].values()) for r in reports)
    output = dict(schema_version='seocho.kernel_profile.v1', passed=observed_traces == 2,
                  observed_rank_traces=observed_traces, traces=reports,
                  denominator='Per trace/device first kernel start to last kernel end; overlapping kernels unioned.',
                  limitations=['Profiler changes timing; excluded from throughput.',
                               'Kernel gaps are observed inactivity intervals, not proof of CPU causality.',
                               'No summing output tokens across TP ranks.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(output, stream, indent=2)
    return 0 if output['passed'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
