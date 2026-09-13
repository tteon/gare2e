from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import httpx
import pytest

from seocho.eval.agentic_graph_agents import (
    LLMRequestTimer, PrivateTrace, _PRIVATE_MODEL_CONTEXT, _PRIVATE_BRANCH_CONTEXT,
    OpenAICompatibleNativeToolModel, run_gtsqa_text2sparql_react,
)
from seocho.eval.private_trace_store import PrivateTraceStore, call_samples
from test_agentic_graph_agents import _case, _Executor


def config(tmp_path: Path) -> dict:
    return {'spool_root': str(tmp_path), 'bucket': 'test',
            'authorized_prefix': 'traces', 'prefix': 'traces/private/calls',
            'flush_max_objects': 1}


def request() -> dict:
    return {'model': 'test-model', 'messages': [{'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': 'private question'}], 'tools': []}


def response() -> dict:
    return {'id': 'response-1', 'choices': [{'message': {'role': 'assistant',
        'content': 'private answer', 'reasoning_content': 'provided reasoning'},
        'finish_reason': 'stop', 'token_ids': [3, 4]}], 'prompt_token_ids': [1, 2],
        'usage': {'prompt_tokens': 2, 'completion_tokens': 2, 'total_tokens': 4}}


def finish(trace: PrivateTrace) -> str:
    call = trace.begin_model_call(role='single_agent', system_instructions='system',
        input=request()['messages'], tools=[], model_settings={})
    trace.finish_model_call(call, response=response(), status_code=200,
        raw_request=request(), raw_response=response(),
        wire_request=json.dumps(request()), wire_response=json.dumps(response()), finish_reason='stop')
    return call


class S3:
    def __init__(self) -> None:
        self.objects = {}
        self.calls = 0
        self.fail = False

    def put_object(self, **kwargs):
        assert not {'ACL', 'GrantRead', 'GrantFullControl'} & kwargs.keys()
        self.calls += 1
        if self.fail:
            raise RuntimeError('credential-secret-canary')
        self.objects[kwargs['Key']] = kwargs['Body']

    def get_object(self, **kwargs):
        return {'Body': io.BytesIO(self.objects[kwargs['Key']])}


def test_spool_recovery_retry_checksum_and_private_permissions(tmp_path):
    client = S3()
    store = PrivateTraceStore(config(tmp_path), client=client)
    trace = store.trace({'run_id': 'r', 'case_id': '7'}, case=_case(),
        dataset_sha256='d', model='m', condition='eval')
    finish(trace)
    journal = next(tmp_path.glob('*.journal'))
    with journal.open('ab') as out:
        out.write(b'{"torn":')
    payload = store.seal(journal)
    assert payload
    assert payload.stat().st_mode & 0o777 == 0o600
    assert journal.stat().st_mode & 0o777 == 0o600
    samples = [json.loads(x) for x in payload.read_text().splitlines()]
    assert len(samples) == 1 and not samples[0]['training_eligible']
    client.fail = True
    assert store.flush()['failed'] == 1
    assert 'credential-secret-canary' not in next(tmp_path.glob('*.failure.json')).read_text()
    client.fail = False
    assert store.flush()['verified'] == 1
    calls = client.calls
    assert store.flush()['verified'] == 0 and calls == client.calls
    assert store.seal(journal) == payload
    assert len(list(tmp_path.glob('*.jsonl'))) == 1
    payload.write_bytes(b'corrupted')
    payload.with_suffix('.uploaded').unlink()
    assert store.flush()['failed'] == 1 and calls == client.calls


def test_split_leak_and_prefix_rejected(tmp_path):
    cfg = config(tmp_path)
    cfg['selection'] = {'1': {'split': 'train', 'graph_sha256': 'shared'},
                        '2': {'split': 'validation', 'graph_sha256': 'shared'}}
    with pytest.raises(ValueError, match='crosses'):
        PrivateTraceStore(cfg)
    cfg['selection'] = {}
    cfg['prefix'] = 'traces/public'
    with pytest.raises(ValueError, match='private prefix'):
        PrivateTraceStore(cfg)


def test_eval_and_missing_exact_tokens_never_promoted():
    trace = PrivateTrace({'run_id': 'r', 'case_id': '7'})
    finish(trace)
    for split in ['eval', 'test', 'validation', 'unknown']:
        sample = call_samples(trace.events, {'source_split': split, 'split': 'train'})[0]
        assert not sample['training_eligible']
    trace.events[0]['http_response']['prompt_token_ids'] = None
    sample = call_samples(trace.events, {'source_split': 'train', 'split': 'train'})[0]
    assert 'endpoint_token_ids_unavailable' in sample['ineligible_reasons']


def test_manager_branch_and_parent_ids():
    trace = PrivateTrace({'run_id': 'r', 'case_id': '7'})
    manager = finish(trace)
    tool = trace.begin_tool_call(role='manager', name='retrieve_graph_evidence', arguments={}, native_call_id='native')
    token = _PRIVATE_BRANCH_CONTEXT.set({'role': 'query_specialist', 'branch_id': tool,
        'parent_event_id': tool, 'parent_call_id': manager})
    try:
        specialist = finish(trace)
    finally:
        _PRIVATE_BRANCH_CONTEXT.reset(token)
    final = finish(trace)
    events = {e['event_id']: e for e in trace.events}
    assert events[specialist]['actor_role'] == 'query_specialist'
    assert events[specialist]['parent_call_id'] == manager
    assert events[specialist]['branch_id'] == tool
    assert events[final]['parent_call_id'] == manager


@pytest.mark.parametrize('capture_ids', [False, True])
def test_http_retry_bodies_and_no_auth_capture(capture_ids):
    async def run():
        trace = PrivateTrace({'run_id': 'r', 'case_id': '7'})
        timer = LLMRequestTimer(capture_token_ids=capture_ids)
        call = trace.begin_model_call(role='manager', system_instructions=None,
            input=[], tools=[], model_settings={})
        async def handler(req):
            assert json.loads(req.content).get('return_token_ids', False) == capture_ids
            return httpx.Response(200, json=response())
        token = _PRIVATE_MODEL_CONTEXT.set({'trace': trace, 'event_id': call})
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                    event_hooks={'request': [timer.on_request], 'response': [timer.on_response]}) as client:
                for _ in range(2):
                    await client.post('https://example.test/v1/chat/completions', json=request(),
                        headers={'Authorization': 'Bearer AUTH-CANARY'})
            trace.finish_model_call(call, response=response())
        finally:
            _PRIVATE_MODEL_CONTEXT.reset(token)
        assert len(trace.events[0]['http_attempts']) == 2
        assert 'AUTH-CANARY' not in json.dumps(trace.events)
        assert 'private question' not in json.dumps(timer.receipt())
        samples = call_samples(trace.events, {})
        assert len(samples) == 2
        assert samples[1]['retry_of_sample_id'] == samples[0]['sample_id']
        wire = trace.events[0]['http_attempts'][0]['wire_response_json']
        assert json.loads(wire)['choices'][0]['message']['reasoning_content'] == 'provided reasoning'
    asyncio.run(run())


def test_real_runner_with_http_contract_and_exact_tool_results(monkeypatch):
    # Contract-only endpoint replies; these are not collected training examples.
    from contextlib import contextmanager
    trace = PrivateTrace({'run_id': 'r', 'case_id': '7'})
    requests = []
    @contextmanager
    def endpoint(req, **kwargs):
        body = json.loads(req.data)
        requests.append(body)
        message = {'role': 'assistant', 'content': None}
        if len(requests) <= 2:
            name = 'inspect_graph_schema' if len(requests) == 1 else 'execute_sparql'
            args = {} if len(requests) == 1 else {'query': 'SELECT ?s ?p ?o WHERE { ?s ?p ?o }'}
            message['tool_calls'] = [{'id': str(len(requests)), 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(args)}}]
        else:
            message['content'] = 'Beta'
        reply = response()
        reply['choices'][0]['message'] = message
        yield io.BytesIO(json.dumps(reply).encode())
    monkeypatch.setattr('seocho.eval.agentic_graph_agents.urlopen', endpoint)
    model = OpenAICompatibleNativeToolModel('https://example.test', 'test-model',
        private_trace=trace, capture_wire_bytes=True, capture_token_ids=True)
    result = run_gtsqa_text2sparql_react(_case(), model=model, executor_factory=_Executor, private_trace=trace)
    assert result.answer == 'Beta' and len(requests) == 3
    tools = [e for e in trace.events if e['event'] == 'tool_call']
    assert [e['native_call_id'] for e in tools] == ['1', '2']
    sent_results = [m['content'] for m in requests[-1]['messages'] if m['role'] == 'tool']
    assert [e['raw_tool_result'] for e in tools] == sent_results
    assert len(call_samples(trace.events, {})) == 3
    assert 'Which item' not in json.dumps(result.receipt)


def test_sdk_manager_specialist_end_to_end_capture():
    from agents import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI
    from seocho.eval.agentic_graph_agents import EvidenceHandoff, run_agents_sdk_agent_tool_episode

    async def run():
        trace = PrivateTrace({'run_id': 'sdk', 'case_id': '7'})
        timer = LLMRequestTimer()
        evidence = EvidenceHandoff.from_rows(rows=[{'s': 'Q1', 'p': 'P1', 'o': 'Q2'}], query='SELECT')
        sent = []
        async def handler(req):
            sent.append(json.loads(req.content))
            step = (len(sent) - 1) % 5 + 1
            message = {'role': 'assistant', 'content': None}
            if step <= 3:
                name, arguments = [
                    ('retrieve_graph_evidence', {'task': 'Find the linked item'}),
                    ('inspect_graph_schema', {}),
                    ('execute_graph_query', {'query': 'SELECT'}),
                ][step - 1]
                message['tool_calls'] = [{'id': f'call-{step}', 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps(arguments)}}]
            else:
                message['content'] = evidence.model_dump_json() if step == 4 else 'Q2'
            reply = response()
            reply.update({'object': 'chat.completion', 'created': 1, 'model': 'test-model'})
            reply['choices'][0].update({'index': 0, 'message': message})
            return httpx.Response(200, json=reply)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                event_hooks={'request': [timer.on_request], 'response': [timer.on_response]}) as http:
            client = AsyncOpenAI(base_url='https://example.test/v1', api_key='AUTH-CANARY', http_client=http)
            model = OpenAIChatCompletionsModel(model='test-model', openai_client=client)
            result = await run_agents_sdk_agent_tool_episode(question='Which item?', model=model,
                execute_query=lambda _: evidence, schema_discovery=lambda: {'relations': ['P1']}, private_trace=trace)
            control = await run_agents_sdk_agent_tool_episode(question='Which item?', model=model,
                execute_query=lambda _: evidence, schema_discovery=lambda: {'relations': ['P1']})
        assert sent[:5] == sent[5:]
        assert result.answer == control.answer == 'Q2'
        calls = [e for e in trace.events if e['event'] == 'model_call']
        assert [e['actor_role'] for e in calls] == ['manager', 'query_specialist', 'query_specialist', 'query_specialist', 'manager']
        tools = [e for e in trace.events if e['event'] == 'tool_call']
        assert [e['native_call_id'] for e in tools] == ['call-1', 'call-2', 'call-3']
        assert all('raw_tool_result' in e for e in tools)
        assert calls[1]['parent_call_id'] == calls[0]['event_id']
        assert calls[-1]['parent_call_id'] == calls[0]['event_id']
        assert 'AUTH-CANARY' not in json.dumps(trace.events)
        assert len(call_samples(trace.events, {})) == 5
    asyncio.run(run())


def test_complete_train_provenance_and_frozen_case_exclusion(tmp_path):
    from seocho.eval.agentic_os_artifacts import canonical_digest
    case = _case()
    graph_sha = canonical_digest([edge.to_triple() for edge in case.graph_edges])
    cfg = config(tmp_path)
    cfg['frozen_case_ids'] = ['99']
    cfg['serving_provenance'] = {'model': 'test-model', 'model_revision': 'a' * 40,
        'tokenizer_sha256': 'a' * 64, 'template_sha256': 'b' * 64, 'source_sha256': 'c' * 64}
    cfg['selection'] = {'7': {'split': 'train', 'source_split': 'train', 'seed_group': 'group-7',
        'source_dataset': 'contract-fixture', 'source_revision': 'a' * 40,
        'dataset_sha256': 'd' * 64, 'graph_sha256': graph_sha,
        'case_sha256': canonical_digest({'question': case.question, 'graph_sha256': graph_sha})}}
    store = PrivateTraceStore(cfg)
    trace = store.trace({'run_id': 'r', 'case_id': '7'}, case=case,
        dataset_sha256='d' * 64, model='test-model', condition='contract_test')
    finish(trace)
    store.finish(trace)
    payload = next(tmp_path.glob('*.jsonl'))
    assert json.loads(payload.read_text())['training_eligible']
    cfg['frozen_case_ids'] = ['7']
    store = PrivateTraceStore(cfg)
    trace = store.trace({'run_id': 'r2', 'case_id': '7'}, case=case,
        dataset_sha256='d' * 64, model='test-model', condition='contract_test')
    finish(trace)
    store.finish(trace)
    assert any('frozen_evaluation_case' in json.loads(p.read_text())['ineligible_reasons'] for p in tmp_path.glob('*.jsonl'))


def test_native_failure_preserves_observed_wire(monkeypatch):
    from contextlib import contextmanager
    trace = PrivateTrace({'run_id': 'r', 'case_id': '7'})
    @contextmanager
    def broken(*args, **kwargs):
        yield io.BytesIO(b'\xffnot-json')
    monkeypatch.setattr('seocho.eval.agentic_graph_agents.urlopen', broken)
    model = OpenAICompatibleNativeToolModel('https://example.test', 'test-model',
        private_trace=trace, capture_wire_bytes=True)
    with pytest.raises(UnicodeDecodeError):
        model.complete(messages=request()['messages'], tools=[])
    event = trace.events[0]
    assert event['status'] == 'error'
    assert event['wire_response_base64']
    assert event['wire_request_json']
    assert not call_samples(trace.events, {})[0]['training_eligible']


def test_partial_manifest_upload_and_bounded_resume(tmp_path):
    class PartialS3(S3):
        def put_object(self, **kwargs):
            if self.calls == 1:
                self.calls += 1
                raise OSError('simulated interruption after payload')
            return super().put_object(**kwargs)
    client = PartialS3()
    store = PrivateTraceStore(config(tmp_path), client=client)
    for run in ['first', 'second']:
        trace = store.trace({'run_id': run, 'case_id': '7'}, case=_case(),
            dataset_sha256='d', model='m', condition='eval')
        finish(trace)
        store.finish(trace)
    assert store.flush() == {'verified': 0, 'failed': 1, 'pending': 1}
    assert len(client.objects) == 1
    assert not list(tmp_path.glob('*.uploaded'))
    assert store.flush() == {'verified': 1, 'failed': 0, 'pending': 1}
    assert store.flush() == {'verified': 1, 'failed': 0, 'pending': 0}
    assert len(client.objects) == 4
