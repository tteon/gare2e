"""Contracts only: real OTel SDK, no claim about GPU or backend performance."""
import asyncio
import json

import pytest

from seocho import tracing
from seocho.eval.agentic_graph_agents import PrivateTrace, _trace_headers
from seocho.eval.experiment_observability import experiment_identity, experiment_span, observe_experiment
from seocho.eval.private_trace_store import PrivateTraceStore


def test_native_ids_parallel_context_privacy_and_storage(monkeypatch, tmp_path):
    trace_exporter = pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace_exporter, 'OTLPSpanExporter', lambda **kwargs: exporter)
    backend = tracing.OTLPBackend()
    tracing.enable_tracing(backend=backend)
    private = PrivateTrace({'run_id': 'run', 'case_id': 'case'})

    @observe_experiment('graphcot.llm')
    async def call(private_trace):
        event_id = private_trace.begin_model_call(role='manager', system_instructions='PRIVATE_CANARY',
            input='PRIVATE_CANARY', tools=[], model_settings={})
        before = tracing.current_span_identity()
        assert before['trace_id'] in _trace_headers()['traceparent']
        await asyncio.sleep(0)
        assert tracing.current_span_identity() == before
        private_trace.finish_model_call(event_id, response={})

    async def run():
        with experiment_span('graphcot.case') as root:
            root_id = root.span_id
            await asyncio.gather(call(private_trace=private), call(private_trace=private))
            try:
                with experiment_span('graphcot.tool') as tool:
                    tool.set_metadata({'tool.result_rows': 3})
                    raise ValueError('PRIVATE_CANARY')
            except ValueError:
                pass
        return root_id

    try:
        root_id = asyncio.run(run())
        backend._provider.force_flush()
        spans = exporter.get_finished_spans()
        ids = {f'{span.context.span_id:016x}' for span in spans}
        assert len(ids) == 4
        assert all(event['span_id'] in ids for event in private.events)
        assert all(f'{span.parent.span_id:016x}' == root_id for span in spans if span.parent)
        assert next(span for span in spans if span.name == 'graphcot.tool').attributes['tool.result_rows'] == 3
        assert 'PRIVATE_CANARY' not in '\n'.join(span.to_json() for span in spans)
        assert not tracing.current_span_identity()
    finally:
        tracing.disable_tracing()
        backend._provider.shutdown()

    monkeypatch.setenv('SEOCHO_EXPERIMENT_OBSERVABILITY', '1')
    for key, value in {'project': 'graphcot', 'campaign': 'campaign', 'experiment': 'schema', 'run': 'run-1'}.items():
        monkeypatch.setenv('SEOCHO_' + key.upper() + '_ID', value)
    store = PrivateTraceStore({'spool_root': str(tmp_path), 'bucket': 'test',
                              'authorized_prefix': 'authorized', 'prefix': 'authorized/private/legacy'})
    assert store.prefix == 'authorized/private/graphcot/campaign/schema/run-1/calls'
    assert store.root == tmp_path / 'graphcot/campaign/schema/run-1'
    for event in private.events:
        store.checkpoint('journal', event, {})
    payload = store.seal(store.root / 'journal.journal')
    manifest = json.loads(payload.with_suffix('.manifest.json').read_text())
    assert manifest['trace_ids'] == [private.events[0]['trace_id']]
    assert manifest['object_key'].startswith(store.prefix)
    monkeypatch.setenv('SEOCHO_RUN_ID', '../escape')
    with pytest.raises(ValueError):
        experiment_identity()


def test_real_http_retry_keeps_wire_and_native_attempt_ids(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from agents import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI
    from seocho.eval.agentic_graph_agents import LLMRequestTimer, run_agents_sdk_agent_tool_episode
    trace_exporter = pytest.importorskip('opentelemetry.exporter.otlp.proto.grpc.trace_exporter')
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace_exporter, 'OTLPSpanExporter', lambda **kwargs: exporter)
    tracing.enable_tracing(backend='otlp')
    requests = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.rfile.read(int(self.headers['Content-Length'])), self.headers.get('traceparent')))
            self.send_response(500 if len(requests) == 1 else 200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                'model': 'fixture', 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': 'done'}}]}).encode())

        def log_message(self, *args):
            pass

    endpoint = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    threading.Thread(target=endpoint.serve_forever, daemon=True).start()
    private = PrivateTrace({'run_id': 'http', 'case_id': '1'})

    async def run():
        timer = LLMRequestTimer()
        async with AsyncOpenAI(base_url=f'http://127.0.0.1:{endpoint.server_port}/v1',
                               api_key='AUTH_CANARY', http_client=timer.http_client()) as client:
            model = OpenAIChatCompletionsModel(model='fixture', openai_client=client)
            await run_agents_sdk_agent_tool_episode(question='PRIVATE_CANARY', model=model,
                execute_query=lambda _: None, schema_discovery=lambda: {}, private_trace=private)

    try:
        asyncio.run(run())
        tracing.flush_tracing()
        spans = exporter.get_finished_spans()
        attempts = next(event for event in private.events if event['event'] == 'model_call')['http_attempts']
        assert len(requests) == len(attempts) == 2
        assert requests[0][0] == requests[1][0]
        assert attempts[0]['span_id'] != attempts[1]['span_id']
        for attempt, (body, header) in zip(attempts, requests):
            assert attempt['wire_request_json'].encode() == body
            assert attempt['span_id'] in header
            assert any(f'{span.context.span_id:016x}' == attempt['span_id'] for span in spans)
        exported = '\n'.join(span.to_json() for span in spans)
        assert 'AUTH_CANARY' not in exported and 'PRIVATE_CANARY' not in exported
    finally:
        tracing.disable_tracing()
        endpoint.shutdown()
