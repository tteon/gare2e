#!/usr/bin/env python3
"""Bounded, pre-staged lab 024 pass queue on an already admitted appliance."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from prepare_agentic_os_throughput import backlog_schedule, capture_index, write_new
from summarize_agentic_os_factors import summarize


def plan() -> list[dict[str, Any]]:
    baseline = '--max-num-batched-tokens,8192'
    mtp = baseline + ',--speculative-config,{"method":"qwen3_5_mtp","num_speculative_tokens":2}'
    passes: list[dict[str, Any]] = [
        dict(id='gate', k=1, limit=1), dict(id='capture', k=8, capture=True),
        dict(id='fresh-k8', k=8, runner='fresh'),
        *[dict(id=f'base-k{k}', k=k) for k in (8, 16, 32, 64)],
        *[dict(id=f'mtp-k{k}', k=k, serving=mtp) for k in (8, 16, 32, 64)],
        *[dict(id=f'replay-{mode}', k=64, lane='grbench-replay', replay=mode) for mode in ('serving-only', 'tool-stub', 'real-db')],
        *[dict(id=f'block-{block}', k=64, serving=baseline + f',--block-size,{block}') for block in (32, 128)],
        dict(id='chunk-2048', k=64, serving='--max-num-batched-tokens,2048'),
        dict(id='chunk-8192', k=64),
        *[dict(id=f'grammar-k8-cpu16-r{ratio}', k=8, cpu=16, lane='grbench-grammar', ratio=ratio) for ratio in (0.0, 0.5, 1.0)],
        *[dict(id=f'grammar-cpu{cpu}-r{ratio}', k=64, cpu=cpu, lane='grbench-grammar', ratio=ratio) for cpu in (16, 4) for ratio in (0.0, 0.5, 1.0)],
        dict(id='tp2-control', k=64), dict(id='replicas2', k=64, replicas=2),
    ]
    return [{**{'lane': 'grbench-single', 'serving': baseline, 'cpu': None, 'replicas': 1, 'runner': 'persistent'}, **p} for p in passes]


def run(command: list[str], log: Path, *, timeout: int) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('xb') as stream:
        log.chmod(0o600)
        try:
            return subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            return 124


def pids(pattern: str) -> list[int]:
    result = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True, timeout=10, check=False)
    return [int(value) for value in result.stdout.split() if value.isdigit()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('/workspace/seocho'))
    parser.add_argument('--input', type=Path, default=Path('/workspace/input'))
    parser.add_argument('--artifacts', type=Path, default=Path('/artifacts'))
    parser.add_argument('--duration-s', type=float, default=90)
    parser.add_argument('--max-session-s', type=int, default=10500)
    parser.add_argument('--print-plan', action='store_true')
    args = parser.parse_args()
    if args.print_plan:
        print(json.dumps(plan(), indent=2))
        return 0
    os.umask(0o077)
    os.environ['NEO4J_PASSWORD'] = Path(os.environ.get('SEOCHO_APPLIANCE_SECRET_FILE', '/workspace/appliance.secret')).read_text().strip()
    os.environ.update(VLLM_MAX_NUM_SEQS='128', VLLM_MAX_MODEL_LEN='32768', VLLM_TENSOR_PARALLEL_SIZE='2',
                      VLLM_REPLICAS='1', SEOCHO_ROUTER_POLICY='sticky', VLLM_READY_ATTEMPTS='400')
    scripts = args.repo / 'scripts/benchmarks'
    source = json.loads((args.input / 'strata-grbench-single-schedule.json').read_text())
    cohort = backlog_schedule(source, repeats=1)
    write_new(args.input / 'throughput-cohort.json', cohort)
    gate_entries = [entry for entry in cohort['entries'] if str(entry['qid']) == '105']
    if len(gate_entries) != 1:
        raise ValueError('registered live gate case 105 is missing or ambiguous')
    write_new(args.input / 'throughput-gate.json', {**cohort, 'entries': gate_entries})
    write_new(args.input / 'throughput-backlog.json', backlog_schedule(source, repeats=40))
    write_new(args.artifacts / 'session-summary/registered-plan.json', {'passes': plan(), 'duration_s': args.duration_s,
        'max_session_s': args.max_session_s, 'context': '32768 cap; observed lengths reported separately',
        'db_cache': 'warm intent; no OS cache flush; capacities fixed', 'quality': 'recorded separately; replay has no answer-quality evaluation',
        'high_load_k': 64, 'high_load_is_saturation': 'only if measured K curve establishes plateau'})
    discovery = args.input / 'throughput-discovery.json'
    code = run([sys.executable, str(scripts / 'build_agentic_os_discovery_cache.py'),
        '--records', str(args.input / 'strata-grbench-discovery-records.jsonl'),
        '--tool-schema', str(args.input / 'strata-grbench-tool-schema.json'),
        '--graph-digest', 'bc267591dc3d7aaf8c46262e1effa24b2cc2362b66f967d9f686ca70413efa5e',
        '--base-url', 'http://127.0.0.1:8000', '--model', os.environ['VLLM_MODEL'], '--out', str(discovery)],
        args.artifacts / 'discovery-build.private.log', timeout=300)
    if code:
        return code
    began = time.monotonic()
    shape: tuple[Any, ...] | None = None
    for spec in plan():
        if time.monotonic() - began > args.max_session_s - 1500:
            write_new(args.artifacts / 'session-summary/budget-stop.json', {'next_pass': spec['id'], 'elapsed_s': time.monotonic() - began})
            return 4
        root = args.artifacts / ('run-' + spec['id'])
        root.mkdir()
        next_shape = (spec['serving'], spec['cpu'], spec['replicas'])
        if next_shape != shape:
            os.environ.update(VLLM_EXTRA_ARGS=spec['serving'], VLLM_REPLICAS=str(spec['replicas']),
                SEOCHO_VLLM_CPU_COUNT=str(spec['cpu']) if spec['cpu'] else '', SEOCHO_VLLM_RECEIPT_DIR=str(root / 'vllm-restart'))
            code = run(['bash', str(args.repo / 'docker/agentic-os-appliance/restart_vllm.sh')], root / 'restart.private.log', timeout=1100)
            if code:
                write_new(root / 'pass-receipt.json', {'pass_id': spec['id'], 'exit_code': code, 'failed_stage': 'restart', 'config': spec})
                return code
            shape = next_shape
        server_pids, db_pids = pids('vllm serve'), pids(r'org\.neo4j|neo4j\.server')
        if len(server_pids) != spec['replicas'] or not db_pids:
            raise RuntimeError('service PID scope is ambiguous or missing')
        os.environ['DOZERDB_PID'] = str(min(db_pids))
        coordinator = root / 'e2e' / (spec['lane'] + '-tool-false-S1') / 'shard-dynamic'
        replay = spec['lane'] == 'grbench-replay'
        schedule = 'throughput-replay-schedule.json' if replay else 'throughput-gate.json' if spec.get('limit') else 'throughput-cohort.json' if spec.get('capture') else 'throughput-backlog.json'
        command = [sys.executable, str(scripts / 'run_agentic_os_schedule.py'), '--lane', spec['lane'],
            '--schedule', str(args.input / schedule), '--dataset', str(args.input / ('throughput-replay-index.private.jsonl' if replay else 'grbench-questions.json')),
            '--discovery-cache', str(discovery), '--run-root', str(coordinator), '--base-url', 'http://127.0.0.1:8000',
            '--model', os.environ['VLLM_MODEL'], '--enable-thinking', 'false', '--condition', 'tool_auto',
            '--max-turns', '6', '--max-output-tokens', '2048', '--token-budget', '16384', '--only-arm', 'S1',
            '--grbench-workspace-id', 'grbench-maple-physics', '--db-pid', str(min(db_pids)),
            '--gpu-sampling', 'appliance', '--dynamic-queue', '--workers', str(spec['k']), '--runner-mode', spec['runner'],
            '--steady-state-warmup-s', '10']
        if spec.get('limit'):
            command += ['--max-attempts', str(spec['limit'])]
        elif spec.get('capture'):
            command += ['--record-replay']
        else:
            command += ['--measurement-duration-s', str(args.duration_s)]
        if replay:
            command += ['--replay-mode', spec['replay']]
        if spec['lane'] == 'grbench-grammar':
            command += ['--constrained', 'xgrammar-ontology', '--constrained-ratio', str(spec['ratio'])]
        wrapper = [sys.executable, str(scripts / 'run_agentic_os_colocated_workload.py'), '--artifacts', str(root / 'workload'),
            '--role-pid', f'dozerdb={min(db_pids)}']
        for index, pid in enumerate(sorted(server_pids)):
            wrapper += ['--role-pid', f'{"vllm" if index == 0 else "vllm_replica" + str(index)}={pid}']
        code = run(wrapper + ['--workload', *command], root / 'pass.private.log', timeout=1200)
        export_code = run([sys.executable, str(scripts / 'export_agentic_os_otel.py'), str(coordinator),
            '--jsonl', str(root / 'otel/spans.jsonl')], root / 'export.private.log', timeout=120)
        receipt = {'pass_id': spec['id'], 'exit_code': code, 'export_exit_code': export_code, 'config': spec}
        if not code and spec.get('limit'):
            cases = [c for p in coordinator.glob('attempts/*/run-receipt.json') for c in json.loads(p.read_text()).get('cases', [])]
            gate_ok = len(cases) == 1 and bool(cases[0].get('trace', {}).get('trace_id')) and any(t.get('name') == 'execute_cypher' and t.get('outcome') == 'ok' for t in cases[0].get('tools', []))
            receipt['live_graph_gate_passed'] = gate_ok
            if not gate_ok or export_code:
                code = 3
        elif not code and spec.get('capture'):
            rows = capture_index(coordinator)
            write_new(args.input / 'throughput-replay-index.private.jsonl', rows, jsonl=True)
            qids = {r['qid'] for r in rows}
            replay_source = {**cohort, 'entries': [e for e in cohort['entries'] if str(e['qid']) in qids]}
            write_new(args.input / 'throughput-replay-schedule.json', backlog_schedule(replay_source, repeats=40))
            receipt['captured_cases'] = len(rows)
            receipt['excluded_cases'] = len(cohort['entries']) - len(rows)
        elif not code:
            factors = summarize(root)
            write_new(root / 'otel/factors.json', factors)
            write_new(root / 'otel/throughput.json', factors['throughput'])
            receipt['throughput_status'] = factors['throughput']['status']
        receipt['exit_code'] = code
        write_new(root / 'pass-receipt.json', receipt)
        print(json.dumps({'pass_id': spec['id'], 'exit_code': code, 'elapsed_s': round(time.monotonic() - began)}), flush=True)
        if code:
            return code
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
