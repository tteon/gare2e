"""Portable teaching CLI. Offline contracts are not live performance evidence."""
from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'core'))
from financial_tools import Dataset, Registry  # noqa: E402
from financial_oracle import Oracle  # noqa: E402
from export_neo4rs_contract import export  # noqa: E402


def synthetic() -> tuple[Dataset, dict]:
    """Tiny edge-case fixture, deliberately not a FinBench scale factor."""
    nodes = [dict(eid=str(n), labels=['Account'],
                  props=dict(acct_no=n, _workspace_id='tutorial')) for n in [1, 2, 3]]
    edges = [dict(src='1', dst='2', type='TRANSFER', props=dict(amount=100, ts=10)),
             dict(src='1', dst='2', type='TRANSFER', props=dict(amount=200, ts=11)),
             dict(src='2', dst='3', type='TRANSFER', props=dict(amount=100, ts=15))]
    snapshot = dict(nodes=nodes, edges=edges)
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return Dataset('synthetic-teaching-only', 'tutorial', digest,
                   'gug-finbench-logical-v1', None), snapshot


def registry(dataset: Dataset, query_set: str) -> Registry:
    return Registry(dataset, ROOT / 'queries' / query_set)


def offline_demo(query_set: str) -> dict:
    dataset, graph = synthetic()
    reg = registry(dataset, query_set)
    arguments = dict(account=1, min_amount=0, limit=5)
    bound = reg.bind('list_transfer_recipients', arguments)
    rows = Oracle(graph, dataset.workspace).answer(bound['tool'], arguments)
    assert rows == [{'account': 2}]
    return dict(mode='offline-contract-demo', performance_evidence=False,
                tools=len(reg.schemas()), bound=bound, oracle_rows=rows,
                note='Parallel transfers still produce one DISTINCT recipient. No DB/LLM called.')


def load_inputs(args: argparse.Namespace) -> tuple[Registry, list[dict], dict]:
    dataset = Dataset.load(args.dataset, args.snapshot)
    reg = registry(dataset, args.query_set)
    graph = json.loads(Path(args.snapshot).read_text())
    oracle = Oracle(graph, dataset.workspace)
    cases = json.loads(Path(args.cases).read_text())
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a nonempty array of {id,tool,arguments}')
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('case ids must be unique')
    jobs = []
    for case in cases:
        bound = reg.bind(case['tool'], case['arguments'])
        expected = oracle.answer(case['tool'], case['arguments'])
        jobs.append(dict(case_id=case['id'], bound=bound, expected=expected))
    provenance = dict(dataset_id=dataset.dataset_id, snapshot_sha256=dataset.snapshot_sha256,
                      query_set=args.query_set,
                      query_sha256=sorted({j['bound']['query_sha256'] for j in jobs}))
    return reg, jobs, provenance


def benchmark_db(args: argparse.Namespace) -> dict:
    """Read-only, finite cohort. Explicit transactions, full drain, no app retries."""
    reg, jobs, provenance = load_inputs(args)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    run_id = uuid.uuid4().hex
    requested = [j for _ in range(args.repeat) for j in jobs]
    with ExitStack() as stack:
        if args.backend == 'python':
            from neo4j import GraphDatabase, READ_ACCESS
            driver = stack.enter_context(GraphDatabase.driver(
                os.environ['NEO4J_URI'],
                auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']),
                max_connection_pool_size=args.pool, connection_acquisition_timeout=args.timeout))
            driver.verify_connectivity()
        else:
            import httpx
            client = stack.enter_context(httpx.Client(base_url=os.environ['GUG_TOOL_URL'],
                timeout=args.timeout, trust_env=False,
                limits=httpx.Limits(max_connections=args.pool, max_keepalive_connections=args.pool)))
            response = client.get('/metadata')
            response.raise_for_status()
            metadata = response.json()
            if metadata['contract'] != export(vars(reg.dataset), reg.query_dir):
                raise ValueError('adapter dataset/schema/query contract mismatch')
            if metadata['runtime']['pool'] != args.pool:
                raise ValueError('adapter pool does not match --pool')

        def execute(index_job: tuple[int, dict]) -> dict:
            i, job = index_job
            start = time.monotonic_ns()
            row = dict(run_id=run_id, occurrence_id=f'{run_id}-{i}', case_id=job['case_id'],
                       tool=job['bound']['tool'], submitted_ns=0, started_ns=start,
                       status='failed', retry_count=0)
            try:
                if args.backend == 'neo4rs':
                    response = client.post('/tool', json={
                        'tool': job['bound']['tool'], 'arguments': job['bound']['arguments']})
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get('instance_id') != metadata['instance_id']:
                        raise RuntimeError('adapter instance changed')
                    row['adapter_timings'] = payload.get('timings')
                    if payload.get('status') != 'ok':
                        raise RuntimeError('adapter tool failed')
                    values = payload['rows']
                else:
                    with driver.session(database=args.database, default_access_mode=READ_ACCESS) as session:
                        with session.begin_transaction(timeout=args.timeout) as tx:
                            t = time.monotonic_ns()
                            result = tx.run(job['bound']['query'], job['bound']['parameters'])
                            row['run_return_ns'] = time.monotonic_ns()
                            values = [r.data() for r in result]
                            result.consume()
                            row['drained_ns'] = time.monotonic_ns()
                            tx.commit()
                            row['committed_ns'] = time.monotonic_ns()
                            row['run_start_ns'] = t
                row['correct'] = values == job['expected']
                row['status'] = 'ok' if row['correct'] else 'failed'
                row['result_bytes'] = len(json.dumps(values, separators=(',', ':')).encode())
                row['rows'] = len(values)
                row['evidence_sha256'] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
                if not row['correct']:
                    row['error_type'] = 'oracle_mismatch'
            except Exception as exc:
                row['error_type'] = type(exc).__name__
                # Raw exception text may contain query/connection information; keep it out of exports.
            row['completed_ns'] = time.monotonic_ns()
            return row

        begin = time.monotonic_ns()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = []
            for index_job in enumerate(requested):
                offered = time.monotonic_ns()
                futures.append((executor.submit(execute, index_job), offered))
            records = []
            for future, offered in futures:
                row = future.result()
                row['submitted_ns'] = offered
                row['dispatch_lag_ms'] = (row['started_ns'] - offered) / 1e6
                records.append(row)
        end = time.monotonic_ns()
    ok = sum(r['status'] == 'ok' for r in records)
    report = dict(mode='live-db-finite-cohort', run_id=run_id, **provenance,
                  offered=len(records), ok=ok, failed=len(records)-ok, dropped=0,
                  backend=args.backend, concurrency=args.concurrency, pool=args.pool, batch_seconds=(end-begin)/1e9,
                  warmup_requests=0, warm_state='operator-managed; no cache flush',
                  note='Client+driver+DB timings; not pure server execution or steady capacity.')
    assert report['offered'] == report['ok'] + report['failed'] + report['dropped']
    (out / 'requests.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
    (out / 'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def verify() -> dict:
    source = json.loads((ROOT / 'evidence/source-manifest.json').read_text())
    for record in source['files']:
        path = ROOT / record['package_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], path
    for record in source['derived']:
        path = ROOT / record['package_path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['package_sha256'], path
    for query_set in ['financial_tools', 'sf1_optimized', 'hard_tools_optimized']:
        offline_demo(query_set)
    e = json.loads((ROOT / 'evidence/memory-e2e.json').read_text())
    assert e['measured'] == e['correct'] == 864 and e['failed'] == e['dropped'] == 0
    assert sum(r['offered_per_arm'] * 2 for r in e['comparisons']) == 864
    return dict(verified=True, copied_files=len(source['files']),
                offline_query_sets=3, cached_e2e_outcomes=864,
                note='Integrity/contract checks only; no live performance rerun.')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    for name in ['demo', 'bind', 'export-contract', 'benchmark-db']:
        q = sub.add_parser(name)
        q.add_argument('--query-set', choices=['financial_tools', 'sf1_optimized', 'hard_tools_optimized'], default='financial_tools')
        if name == 'bind':
            q.add_argument('--tool', required=True)
            q.add_argument('--arguments', required=True, help='JSON; keep account identifiers as exact integers')
        if name in ['export-contract', 'benchmark-db']:
            q.add_argument('--dataset', required=True)
            q.add_argument('--snapshot', required=True)
            q.add_argument('--output', required=True)
        if name == 'benchmark-db':
            q.add_argument('--backend', choices=['neo4rs', 'python'], default='neo4rs')
            q.add_argument('--cases', required=True)
            q.add_argument('--database', default='neo4j')
            for flag, default in [('concurrency', 1), ('pool', 16), ('repeat', 1), ('timeout', 15)]:
                q.add_argument('--'+flag, type=int, default=default)
    a = p.parse_args()
    if a.command == 'verify':
        value = verify()
    elif a.command == 'demo':
        value = offline_demo(a.query_set)
    elif a.command == 'bind':
        dataset, _ = synthetic()
        value = registry(dataset, a.query_set).bind(a.tool, json.loads(a.arguments))
    elif a.command == 'export-contract':
        dataset = Dataset.load(a.dataset, a.snapshot)
        value = export(vars(dataset), ROOT / 'queries' / a.query_set)
        with open(a.output, 'x') as f:
            json.dump(value, f, indent=2)
        value = {'exported': True, 'output': a.output}
    else:
        if any(getattr(a, k) < 1 for k in ['concurrency', 'pool', 'repeat', 'timeout']):
            p.error('concurrency, pool, repeat, timeout must be positive')
        value = benchmark_db(a)
    print(json.dumps(value, indent=2))
    if a.command == 'benchmark-db' and value['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
