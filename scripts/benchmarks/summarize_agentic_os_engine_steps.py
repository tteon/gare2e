#!/usr/bin/env python3
"""Audit content-free scheduler observations; never label CPU spans GPU time."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


def distribution(values: list[float]) -> dict[str, float | int] | None:
    values = sorted(values)
    if not values:
        return None
    return dict(n=len(values), mean=statistics.mean(values), p50=statistics.median(values),
                p95=values[min(len(values) - 1, math.ceil(len(values) * .95) - 1)], max=max(values))


def summarize(rows: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    selected = [r for r in rows if start <= r['unix_s'] <= end]
    schedules = [r for r in selected if r['kind'] == 'schedule' and r['scheduled_tokens'] > 0]
    outputs = [r for r in selected if r['kind'] == 'output']
    kv = [r for r in selected if r['kind'] == 'kv_blocks']
    all_ids = [(r['engine_pid'], r['step_id']) for r in rows if r['kind'] == 'schedule']
    output_ids = [(r['engine_pid'], r['step_id']) for r in rows if r['kind'] == 'output']
    observed_ids = set(all_ids)
    valid = bool(schedules and outputs and kv) and len(all_ids) == len(set(all_ids)) and len(output_ids) == len(set(output_ids))
    valid &= all(key in observed_ids for key in output_ids)
    valid &= all(r['scheduled_sequences'] == r['prompt_sequences'] + r['decode_sequences'] - r['mixed_sequences'] for r in schedules)
    valid &= all(r['usable_blocks'] == r['active_blocks'] + r['reclaimable_cached_blocks'] + r['free_uncached_blocks'] for r in kv)
    fields = ('scheduled_sequences', 'decode_sequences', 'prompt_sequences', 'scheduled_tokens',
              'scheduled_prompt_tokens', 'draft_tokens_scheduled', 'scheduler_wall_s',
              'scheduler_thread_cpu_s', 'schedule_interval_s', 'running_sequences', 'waiting_sequences')
    return dict(schema_version='seocho.engine_summary.v1', passed=bool(valid),
                started_unix_s=start, finished_unix_s=end, scheduled_steps=len(schedules), output_steps=len(outputs),
                schedules={key: distribution([r[key] for r in schedules if r[key] is not None]) for key in fields},
                committed_output_tokens=sum(r['committed_output_tokens'] for r in outputs),
                committed_tokens_per_step=distribution([r['committed_output_tokens'] for r in outputs]),
                schedule_to_output_s=distribution([r['schedule_to_output_s'] for r in outputs]),
                kv={key: distribution([r[key] for r in kv]) for key in ('active_blocks', 'reclaimable_cached_blocks', 'free_uncached_blocks', 'usable_blocks', 'scan_wall_s')},
                grammar={kind: {key: distribution([r[key] for r in selected if r['kind'] == kind])
                         for key in ('wall_s', 'thread_cpu_s')} for kind in ('grammar_compile', 'grammar_bitmask')},
                preempted_sequences=sum(r['preempted_sequences'] for r in schedules),
                limitations=['Schedule-to-output overlaps async batches; not exclusive GPU time.',
                             'Decode positions may include recomputation. Grammar wrapper CPU excludes other worker threads.',
                             'KV block counts are engine-level, not summed across TP ranks.'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--factors', type=Path)
    parser.add_argument('--client', type=Path, help='Restrict a one-case gate to its own client interval')
    args = parser.parse_args()
    rows = []
    for path in sorted(args.source.glob('engine-*.jsonl')):
        # A live last write may be incomplete. Preserve bytes; ignore only that
        # unfinished line, never reinterpret malformed complete observations.
        for line in path.read_bytes().splitlines(keepends=True):
            if line.endswith(b'\n'):
                rows.append(json.loads(line))
    start, end = 0., 0.
    if args.factors:
        rate = json.loads(args.factors.read_text())['throughput']
        start, end = rate['started_unix_s'], rate['finished_unix_s']
    elif rows:
        start, end = min(r['unix_s'] for r in rows), max(r['unix_s'] for r in rows)
    if args.client:
        spans = [json.loads(line) for line in args.client.read_text().splitlines() if line.strip()]
        start = min(s['start_unix_ns'] for s in spans) / 1e9
        end = max(s['end_unix_ns'] for s in spans) / 1e9
    result = summarize(rows, start, end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    return 0 if result['passed'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
