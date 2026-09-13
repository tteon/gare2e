#!/usr/bin/env python3
"""Prepare balanced backlog schedules and content-free length replay inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


def write_new(path: Path, rows: Any, *, jsonl: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        if jsonl:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            json.dump(rows, stream, indent=2)
            stream.write('\n')


def backlog_schedule(source: dict[str, Any], *, repeats: int, arm: str = 'S1') -> dict[str, Any]:
    if repeats < 1:
        raise ValueError('repeats must be positive')
    original = [e for e in source['entries'] if e['arm'] == arm and e['cache_phase'] in ('warm', 'none')]
    if not original:
        raise ValueError('no eligible warm entries')
    if len({str(e['qid']) for e in original}) != len(original):
        raise ValueError('source must have one entry per case for the selected arm')
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in original:
        groups.setdefault(entry.get('stratum', 'unknown'), []).append(entry)
    rng = random.Random(0)
    for group in groups.values():
        rng.shuffle(group)
    ordered = [group[i] for i in range(max(map(len, groups.values())))
               for _, group in sorted(groups.items()) if i < len(group)]
    entries = []
    for repetition in range(repeats):
        for entry in ordered:
            entries.append({**entry, 'repetition': repetition,
                            'paired_unit': f"{entry['qid']}:{entry['seed']}:{entry['cache_phase']}:{repetition}"})
    return {**source, 'entries': entries, 'throughput_preparation': {
        'source_digest': hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
        'repeats': repeats, 'ordering': 'seed0-stratum-interleave',
        'purpose': 'backlog supply; repetitions are not independent quality samples'}}


def length_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.rglob('run-receipt.json')):
        receipt = json.loads(path.read_text())
        for case in receipt.get('cases', []):
            for index, turn in enumerate(case.get('turns', [])):
                ins, outs = turn.get('input_tokens'), turn.get('output_tokens')
                if isinstance(ins, int) and isinstance(outs, int) and ins > 0 and outs > 0:
                    rows.append({'case_id': case['case_id'], 'turn': index,
                                 'input_tokens': ins, 'output_tokens': outs})
    return rows


def capture_index(root: Path) -> list[dict[str, str]]:
    rows = []
    for path in sorted(root.rglob('replay.private.jsonl')):
        receipt = path.with_name('run-receipt.json')
        if not receipt.exists():
            continue
        cases = json.loads(receipt.read_text()).get('cases', [])
        events = [json.loads(line) for line in path.read_text().splitlines()]
        if len(cases) != 1 or cases[0].get('terminal') == 'llm_error':
            continue
        llm = [event for event in events if event['kind'] == 'llm']
        if not llm or any(not isinstance(e.get('output_tokens'), int) or e['output_tokens'] < 1 for e in llm):
            continue
        rows.append({'qid': str(cases[0]['case_id']), 'trace_path': str(path.resolve()),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if not rows or len({row['qid'] for row in rows}) != len(rows):
        raise ValueError('capture must contain nonempty, unique successful case traces')
    return rows


def synthetic_prompt(tokenizer: Any, target: int, identity: str) -> str:
    prefix = f"Synthetic request {hashlib.sha256(identity.encode()).hexdigest()[:16]}:"
    count = len(tokenizer.encode(prefix, add_special_tokens=False))
    if count > target:
        prefix = ' x'
        count = len(tokenizer.encode(prefix, add_special_tokens=False))
    prompt = prefix + ' hello' * max(0, target - count)
    for _ in range(8):
        actual = len(tokenizer.encode(prompt, add_special_tokens=False))
        if actual == target:
            return prompt
        prompt = tokenizer.decode(tokenizer.encode(prompt, add_special_tokens=False)[:target]) if actual > target else prompt + ' hello' * (target - actual)
    raise ValueError('cannot exactly reproduce target prompt token count')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('schedule', 'lengths', 'capture-index', 'synthetic'))
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=40)
    parser.add_argument('--tokenizer')
    parser.add_argument('--revision')
    parser.add_argument('--schedule', type=Path)
    parser.add_argument('--schedule-output', type=Path)
    args = parser.parse_args()
    if args.mode == 'schedule':
        write_new(args.output, backlog_schedule(json.loads(args.input.read_text()), repeats=args.repeats))
    elif args.mode == 'lengths':
        rows = length_rows(args.input)
        if not rows:
            raise ValueError('no observed request lengths')
        write_new(args.output, rows, jsonl=True)
    elif args.mode == 'capture-index':
        rows = capture_index(args.input)
        write_new(args.output, rows, jsonl=True)
        if args.schedule_output:
            if not args.schedule:
                parser.error('--schedule-output requires --schedule')
            schedule = json.loads(args.schedule.read_text())
            selected = {row['qid'] for row in rows}
            schedule['entries'] = [entry for entry in schedule['entries'] if str(entry['qid']) in selected]
            write_new(args.schedule_output, backlog_schedule(schedule, repeats=args.repeats))
    else:
        if not args.tokenizer or not args.revision:
            raise ValueError('synthetic replay requires a pinned tokenizer')
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, revision=args.revision, local_files_only=True)
        rows = [json.loads(line) for line in args.input.read_text().splitlines()]
        write_new(args.output, [{'prompt': synthetic_prompt(tokenizer, row['input_tokens'], str(i)),
                                'output_tokens': row['output_tokens']} for i, row in enumerate(rows)], jsonl=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
