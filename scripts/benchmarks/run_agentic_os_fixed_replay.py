#!/usr/bin/env python3
"""Replay frozen request payloads; responses never control the next recorded step.

serving-only skips tools; tool-stub materializes recorded observations; real-db
reexecutes recorded queries through the existing read-safe executor. This isolates
execution substrate overhead, not answer quality or autonomous agent behaviour.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from run_direct_react_text2cypher import _post, _usage_receipt
from seocho.eval.agentic_os_artifacts import Correlation, require_fresh_outputs, write_manifest
from seocho.eval.agentic_os_trace import case_trace
from seocho.eval.grbench_runtime import GRBenchDozerExecutor, QueryPolicyError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('questions', 'receipt-out', 'predictions-out', 'manifest-out', 'discovery-cache', 'host-samples-out'):
        parser.add_argument('--' + name, type=Path, required=name in ('questions', 'receipt-out'))
    for name in ('base-url', 'model', 'qid', 'run-id', 'workspace-id'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--replay-mode', choices=('serving-only', 'tool-stub', 'real-db'), required=True)
    parser.add_argument('--arm', default='S1')
    parser.add_argument('--cache-phase', default='warm')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--repetition', type=int, default=0)
    for name in ('max-turns', 'token-budget', 'max-output-tokens', 'limit', 'db-pid', 'row-cap', 'byte-cap'):
        parser.add_argument('--' + name, type=int)
    for name in ('enable-thinking', 'stage', 'prompt-profile', 'condition'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    require_fresh_outputs((args.receipt_out, args.manifest_out))
    index = [json.loads(line) for line in args.questions.read_text().splitlines() if line.strip()]
    source = next(row for row in index if str(row['qid']) == args.qid)
    path = Path(source['trace_path'])
    if '.private.' not in path.name or hashlib.sha256(path.read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('private replay source digest mismatch')
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    driver = GraphDatabase.driver('bolt://127.0.0.1:7687', auth=('neo4j', os.environ['NEO4J_PASSWORD'])) if args.replay_mode == 'real-db' else None
    turns: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    mismatches = 0
    clock = time.perf_counter()
    terminal = 'replayed'
    with case_trace(args.run_id, args.qid) as trace:
        try:
            for event in events:
                started = time.perf_counter()
                if event['kind'] == 'llm':
                    payload = dict(event['payload'])
                    expected = event.get('output_tokens')
                    if not isinstance(expected, int) or expected < 1:
                        raise ValueError('capture has unknown or empty completion length')
                    payload.update(model=args.model, max_tokens=expected, ignore_eos=True)
                    response, latency = _post(args.base_url.rstrip('/') + '/v1/chat/completions', payload)
                    row = _usage_receipt(response, latency)
                    row['started_elapsed_ms'] = (started - clock) * 1000
                    row['span_id'] = response.get('_seocho_span_id')
                    row['expected_output_tokens'] = expected
                    mismatches += int(row.get('output_tokens') != expected)
                    turns.append(row)
                elif event['kind'] == 'tool' and args.replay_mode != 'serving-only':
                    if event['workspace_id'] != args.workspace_id:
                        raise ValueError('replay workspace mismatch')
                    observation = event['observation']
                    outcome, code = event['outcome'], event.get('error_code')
                    detail: dict[str, Any] = {}
                    if driver is not None and event['name'] == 'execute_cypher':
                        executor = GRBenchDozerExecutor(driver, database="neo4j", workspace_id=args.workspace_id,
                            row_cap=event['row_cap'], byte_cap=event['byte_cap'],
                            require_evidence_columns=False, require_anchor=True)
                        try:
                            observation, detail = executor.execute_rows(event['query'])
                            outcome, code = 'ok', None
                        except QueryPolicyError as exc:
                            outcome, code = 'rejected', exc.code
                            observation = json.dumps({'error_code': code})
                        except Exception:
                            outcome, code = 'rejected', 'database_or_runtime_error'
                            observation = json.dumps({'error_code': code})
                        mismatches += int(outcome != event['outcome'] or code != event.get('error_code'))
                    # Exercise bounded observation materialization for both tool modes.
                    materialized = json.dumps({'observation': observation}, ensure_ascii=False)
                    tools.append({**detail, 'name': event['name'], 'outcome': outcome,
                                  'error_code': code, 'result_bytes': len(materialized.encode()),
                                  'started_elapsed_ms': (started - clock) * 1000,
                                  'latency_ms': (time.perf_counter() - started) * 1000,
                                  'recorded_observation_matches': observation == event['observation']})
        except Exception as exc:
            terminal = type(exc).__name__
        finally:
            if driver is not None:
                driver.close()
    wall = (time.perf_counter() - clock) * 1000
    case = {**Correlation(run_id=args.run_id, case_id=args.qid, arm=args.arm, seed=args.seed,
            repetition=args.repetition, cache_phase=args.cache_phase, role='fixed_replay').receipt_fields(),
            'trace': trace.receipt(), 'turns': turns, 'tools': tools, 'terminal': terminal,
            'success': terminal == 'replayed', 'answer_status': 'not_evaluated',
            'time': {'wall_ms': wall, 'llm_ms': sum(row['latency_ms'] for row in turns),
                     'tool_ms': sum(row['latency_ms'] for row in tools)},
            'replay_mismatches': mismatches}
    write_manifest(args.receipt_out, {'schema_version': 'agentic_os.fixed_replay.v1',
        'config': {'model': args.model, 'workspace_id': args.workspace_id, 'mode': args.replay_mode,
                   'source_sha256': source['sha256'], 'run_id': args.run_id, 'arm': args.arm}, 'cases': [case]})
    if args.manifest_out:
        write_manifest(args.manifest_out, {'schema_version': 'agentic_os.run_manifest.v1',
                       'run_id': args.run_id, 'lane': 'GRBench-FixedReplay', 'mode': args.replay_mode})
    return 0 if terminal == 'replayed' and mismatches == 0 else 2


if __name__ == '__main__':
    raise SystemExit(main())
