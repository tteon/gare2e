"""Routing/trace contracts only: local HTTP is not vLLM performance evidence."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).parents[2]


def module(path: str):
    sys.path.insert(0, str(ROOT / 'scripts/benchmarks'))
    spec = importlib.util.spec_from_file_location('replica_contract', ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_task_affinity_is_per_execution_bounded_and_never_evicted() -> None:
    m = module('docker/agentic-os-appliance/vllm_router.py')
    router = m.Router(['one', 'two'], 'task-rr', None, max_task_assignments=2)
    body = {'messages': [{'content': 'same question'}]}
    assert router.pick(body, trace_id='a')[0] == 0
    assert router.pick(body, trace_id='b')[0] == 1
    assert router.pick({'messages': []}, trace_id='a')[0] == 0
    with pytest.raises(web.HTTPServiceUnavailable):
        router.pick(body, trace_id='c')
    assert router.pick(body, trace_id='b')[0] == 1
    with pytest.raises(web.HTTPBadRequest):
        router.pick(body)


def test_real_http_proxy_preserves_parent_and_task_affinity(tmp_path: Path) -> None:
    async def scenario() -> None:
        m = module('docker/agentic-os-appliance/vllm_router.py')
        seen = []
        servers = []
        for index in range(2):
            async def endpoint(request: web.Request, backend: int = index) -> web.Response:
                seen.append((backend, request.headers.get('traceparent'), request.headers.get('tracestate')))
                return web.json_response({'choices': []})
            app = web.Application()
            app.router.add_post('/v1/chat/completions', endpoint)
            server = TestServer(app)
            await server.start_server()
            servers.append(server)
        router = m.Router([str(s.make_url('')).rstrip('/') for s in servers], 'task-rr', tmp_path / 'routes.jsonl')
        try:
            async with TestClient(TestServer(m.build_app(router))) as client:
                for task, span in ((1, 1), (2, 2), (1, 3)):
                    parent = f'00-{task:032x}-{span:016x}-01'
                    response = await client.post('/v1/chat/completions',
                        headers={'traceparent': parent, 'tracestate': 'vendor=value'},
                        json={'messages': [{'role': 'user', 'content': 'private test content'}]})
                    assert response.status == 200
                    await response.read()
                response = await client.post('/v1/chat/completions', json={'messages': []})
                assert response.status == 400
            assert [s[0] for s in seen] == [0, 1, 0]
            assert all(s[1] and s[2] == 'vendor=value' for s in seen)
            rows = [json.loads(line) for line in (tmp_path / 'routes.jsonl').read_text().splitlines()]
            assert [r['parent_span_id'] for r in rows] == [f'{s:016x}' for s in (1, 2, 3)]
            assert rows[0]['trace_id'] == rows[2]['trace_id']
            assert 'private test content' not in (tmp_path / 'routes.jsonl').read_text()
            assert 'vendor=value' not in (tmp_path / 'routes.jsonl').read_text()
        finally:
            for server in servers:
                await server.close()
    asyncio.run(scenario())


def test_trace_gate_rejects_missing_ambiguous_or_incomplete_evidence(tmp_path: Path) -> None:
    m = module('scripts/benchmarks/audit_agentic_os_trace_gate.py')
    client, server, router = [tmp_path / (n + '.jsonl') for n in ('client', 'server', 'router')]
    client.write_text(json.dumps({'name': 'chat model', 'trace_id': '1' * 32, 'span_id': '2' * 16}) + '\n')
    attrs = {m.STAGE_KEYS[k][0]: 0.0 for k in m.REQUIRED_STAGES}
    span = {'name': 'llm_request', 'trace_id': '1' * 32, 'parent_span_id': '2' * 16, 'attributes': attrs}
    server.write_text(json.dumps(span) + '\n')
    router.write_text(json.dumps({'trace_id': '1' * 32, 'parent_span_id': '2' * 16, 'status': 200, 'replica': 0}) + '\n')
    assert m.audit(client, server, router, replicas=2)['passed']
    assert not m.audit(client, server, router, replicas=2, require_all_backends=True)['passed']
    server.write_text((json.dumps(span) + '\n') * 2)
    assert not m.audit(client, server, router, replicas=2)['passed']
    span['parent_span_id'] = None
    server.write_text(json.dumps(span) + '\n')
    assert not m.audit(client, server, router, replicas=2)['passed']
    span['parent_span_id'] = '2' * 16
    attrs.pop(m.STAGE_KEYS['decode_s'][0])
    server.write_text(json.dumps(span) + '\n')
    assert not m.audit(client, server, router, replicas=2)['passed']


def test_replica_audit_gates_each_configuration_before_measuring() -> None:
    m = module('scripts/benchmarks/run_agentic_os_throughput_session.py')
    plan = m.plan(replica_audit=True)
    assert len(plan) == 6
    for gate, measure in zip(plan[::2], plan[1::2]):
        assert gate['limit'] == 1 and gate['k'] == 1
        assert measure['k'] == 64 and 'limit' not in measure
        assert (gate['replicas'], gate['router_policy']) == (measure['replicas'], measure['router_policy'])
    assert [p['router_policy'] for p in plan[1::2]] == ['sticky', 'sticky', 'task-rr']
    with pytest.raises(ValueError):
        m.plan(continuation=True, replica_audit=True)
