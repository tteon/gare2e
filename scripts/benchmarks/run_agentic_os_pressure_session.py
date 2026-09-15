#!/usr/bin/env python3
"""Registered third stage; run only after the engine and K-sweep gates pass."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from prepare_agentic_os_throughput import write_new
from summarize_agentic_os_factors import summarize


def plan() -> list[dict[str, Any]]:
    cells = [dict(id=f'context-{length}-k{k}-{locality}', input_tokens=length, k=k,
                  locality=locality, delay_s=0)
             for length in (8192, 24576) for k in (8, 64) for locality in ('shared', 'distributed')]
    cells.extend(dict(id=f'context-24576-k64-{locality}-delay2', input_tokens=24576,
                      k=64, locality=locality, delay_s=2) for locality in ('shared', 'distributed'))
    return cells


def execute(args: argparse.Namespace, began: float) -> int:
    from run_agentic_os_throughput_session import control, pids, run
    scripts = args.repo / 'scripts/benchmarks'
    # Restore the same baseline already graph-gated in both K sweeps.
    # Readiness and a fixed-corpus trace/length gate precede this new workload.
    gate_root = args.artifacts / 'run-pressure-gate'
    gate_root.mkdir()
    os.environ.update(VLLM_EXTRA_ARGS='--max-num-batched-tokens,8192', SEOCHO_ENGINE_OBSERVER='1',
                      SEOCHO_VLLM_RECEIPT_DIR=str(gate_root / 'vllm-restart'))
    if time.monotonic() - began > args.max_session_s - args.reserve_s:
        return 4
    code = run(['bash', str(args.repo / 'docker/agentic-os-appliance/restart_vllm.sh')],
               gate_root / 'restart.private.log', timeout=1100)
    if code:
        return code
    servers, databases = pids('vllm serve'), pids(r'org\.neo4j|neo4j\.server')
    if len(servers) != 1 or not databases:
        raise RuntimeError('pressure-stage service ownership is ambiguous')
    write_new(args.artifacts / 'session-summary/context-plan.json', plan())
    gate = dict(id='pressure-gate', input_tokens=8192, k=1, locality='shared', delay_s=0)
    for spec in [gate, *plan()]:
        if time.monotonic() - began > args.max_session_s - args.reserve_s:
            write_new(args.artifacts / 'session-summary/pressure-budget-stop.json', {'next_pass': spec['id']})
            return 4
        root = gate_root if spec is gate else args.artifacts / ('run-' + spec['id'])
        root.mkdir(exist_ok=spec is gate)
        coordinator = root / 'e2e/fixed-corpus-serving/shard-dynamic'
        control('reset_prefix_cache')
        command = [sys.executable, str(scripts / 'run_agentic_os_colocated_workload.py'),
                   '--artifacts', str(root / 'workload'), '--role-pid', f'vllm={servers[0]}',
                   '--role-pid', f'dozerdb={min(databases)}', '--workload', sys.executable,
                   str(scripts / 'run_agentic_os_context_pressure.py'),
                   '--corpus', str(args.input / 'grbench-nodes.csv.gz'), '--root', str(coordinator),
                   '--model', os.environ['VLLM_MODEL'], '--revision', os.environ['VLLM_MODEL_REVISION'],
                   '--k', str(spec['k']), '--input-tokens', str(spec['input_tokens']),
                   '--locality', spec['locality'], '--reuse-delay-s', str(spec['delay_s']),
                   '--duration-s', '15' if spec is gate else str(args.duration_s)]
        code = run(command, root / 'pass.private.log', timeout=900)
        receipt = dict(pass_id=spec['id'], config=spec, evidence_class='fixed_corpus_serving_diagnostic', exit_code=code)
        if not code:
            code = run([sys.executable, str(scripts / 'export_agentic_os_otel.py'), str(coordinator),
                        '--jsonl', str(root / 'otel/spans.jsonl')], root / 'export.private.log', timeout=120)
        if not code:
            factors = summarize(root)
            write_new(root / 'otel/factors.json', factors)
            write_new(root / 'otel/throughput.json', factors['throughput'])
            if factors['throughput']['status'] != 'observed' or factors.get('observed_physical_gpu_count') != 2:
                code = 3
        if not code:
            code = run([sys.executable, str(scripts / 'audit_agentic_os_trace_gate.py'),
                        '--client', str(root / 'otel/spans.jsonl'), '--server', str(args.artifacts / 'otel/collector-spans.jsonl'),
                        '--replicas', '1', '--output', str(root / 'otel/trace-admission.json')],
                       root / 'trace-admission.private.log', timeout=30)
        if not code:
            code = run([sys.executable, str(scripts / 'summarize_agentic_os_engine_steps.py'),
                        '--source', str(gate_root / 'vllm-restart/engine-observer'),
                        '--factors', str(root / 'otel/factors.json'), '--output', str(root / 'otel/engine-steps.json')],
                       root / 'engine-admission.private.log', timeout=60)
        receipt['exit_code'] = code
        write_new(root / 'pass-receipt.json', receipt)
        print(json.dumps(dict(pass_id=spec['id'], exit_code=code)), flush=True)
        if code:
            return code
    return 0
