"""Offline contract gates, never benchmark evidence."""
import importlib.util
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2] / 'examples/graphcot_optimization'
SPEC = importlib.util.spec_from_file_location('graphcot_example', ROOT / 'lab.py')
LAB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAB)


class GraphCoTExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        dataset, self.snapshot = LAB.synthetic()
        self.registry = LAB.registry(dataset, 'financial_tools')

    def test_offline_mode_and_export_integrity(self) -> None:
        self.assertTrue(LAB.verify()['verified'])
        demo = LAB.offline_demo('financial_tools')
        self.assertFalse(demo['performance_evidence'])
        self.assertEqual(demo['oracle_rows'], [{'account': 2}])

    def test_scope_injection_and_numeric_boundaries(self) -> None:
        good = dict(account=1, min_amount=0, limit=5)
        for bad in [dict(good, account=True), dict(good, account=2**63),
                    dict(good, workspace='another-tenant'), dict(good, limit=0)]:
            with self.assertRaises(ValueError):
                self.registry.bind('list_transfer_recipients', bad)
        good['account'] = 2**63-1
        bound = self.registry.bind('list_transfer_recipients', good)
        self.assertEqual(bound['parameters']['account'], 2**63-1)
        self.assertEqual(bound['parameters']['workspace'], 'tutorial')

    def test_fixed_hop_and_time_contract(self) -> None:
        for hops in [0, 4, '1', True]:
            with self.assertRaises(ValueError):
                self.registry.bind('trace_transfer_reachability', dict(account=1, max_hops=hops, limit=5))
        with self.assertRaises(ValueError):
            self.registry.bind('find_shared_access_accounts', dict(account=1, start_ts=10, end_ts=10, limit=5))

    def test_independent_oracle_time_boundary(self) -> None:
        oracle = LAB.Oracle(self.snapshot, 'tutorial')
        args = dict(account=1, min_amount=0, start_ts=10, end_ts=16, max_gap_seconds=5, limit=5)
        self.assertEqual(oracle.answer('trace_rapid_pass_through', args),
                         [dict(source=1, intermediary=2, destination=3)])
        self.assertEqual(oracle.answer('trace_rapid_pass_through', dict(args, end_ts=15)), [])

    def test_all_query_sets_export_eight_tools(self) -> None:
        dataset, _ = LAB.synthetic()
        for variant in ['financial_tools', 'sf1_optimized', 'hard_tools_optimized']:
            contract = LAB.export(vars(dataset), ROOT / 'queries' / variant)
            self.assertEqual(len(contract['tools']), 8)
            self.assertEqual(set(contract['tools']['trace_transfer_reachability']['queries']), {'1', '2', '3'})

    def test_no_cached_failed_arm_time_ranking(self) -> None:
        data = json.loads((ROOT / 'evidence/overview.json').read_text())
        self.assertIsNone(data['memory_seconds']['fanin'][0])
        self.assertEqual(data['memory_success']['fanin'][0], '42/96')

    def test_adapter_failure_remains_in_denominator(self) -> None:
        """Mock transport checks accounting only, not DB compatibility/performance."""
        _, graph = LAB.synthetic()
        contract = LAB.export(vars(self.registry.dataset), self.registry.query_dir)

        class Response:
            def __init__(self, value: dict) -> None:
                self.value = value

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return self.value

        class Client:
            calls = 0

            def __init__(self, **kwargs: object) -> None:
                pass

            def __enter__(self) -> 'Client':
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def get(self, path: str) -> Response:
                return Response(dict(instance_id='test', contract=contract, runtime={'pool': 16}))

            def post(self, path: str, json: dict) -> Response:
                self.calls += 1
                return Response(dict(instance_id='test', status='ok' if self.calls == 1 else 'failed',
                                     rows=[{'account': 2}], timings={}))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / 'snapshot.json'
            snapshot.write_text(json.dumps(graph, sort_keys=True))
            dataset = vars(self.registry.dataset).copy()
            dataset['snapshot_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            contract['snapshot_sha256'] = dataset['snapshot_sha256']
            (root / 'dataset.json').write_text(json.dumps(dataset))
            (root / 'cases.json').write_text(json.dumps([dict(id='one', tool='list_transfer_recipients',
                arguments=dict(account=1, min_amount=0, limit=5))]))
            args = SimpleNamespace(dataset=root/'dataset.json', snapshot=snapshot, cases=root/'cases.json',
                query_set='financial_tools', output=root/'run', repeat=2, concurrency=1, pool=16,
                timeout=15, backend='neo4rs')
            with patch.dict(LAB.os.environ, {'GUG_TOOL_URL': 'http://unused.invalid'}), patch('httpx.Client', Client):
                report = LAB.benchmark_db(args)
            self.assertEqual((report['offered'], report['ok'], report['failed'], report['dropped']), (2, 1, 1, 0))
            records = [json.loads(x) for x in (root/'run/requests.jsonl').read_text().splitlines()]
            self.assertEqual(len({r['occurrence_id'] for r in records}), 2)
            self.assertEqual(records[1]['error_type'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
