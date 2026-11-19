"""Map recorded native pi event boundaries to the existing content-free exporter."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from seocho.eval.agentic_os_otel import SpanRecord, export_jsonl, export_otlp


def spans_for(root: Path) -> list[SpanRecord]:
    context = json.loads((root / 'trace-context.json').read_text())
    events = [json.loads(x) for x in (root / 'agent-events.jsonl').read_text().splitlines() if x]
    identity = json.loads((root / 'identity.json').read_text())
    result = json.loads((root / 'native-result.json').read_text())
    if not events:
        raise ValueError('native event boundaries missing')
    trace, parent = context['trace_id'], context['root_span_id']
    common = {**context['experiment'], 'episode_id': identity['episode_id'],
              'session_id': identity['session_id'], 'runtime': 'pi-sdk-0.80.10',
              'timing_scope': 'actual SDK callback receipt wall clock; not GPU time'}
    if all('observed_monotonic_ns' in e for e in events):
        anchor = events[0]
        anchor_wall = int(anchor['observed_wall_ms']) * 1_000_000
        anchor_mono = int(anchor['observed_monotonic_ns'])
        ns = lambda e: anchor_wall + int(e['observed_monotonic_ns']) - anchor_mono
        common['timing_scope'] = 'SDK callback monotonic duration anchored to first wall-clock sample'
    else:
        ns = lambda e: int(e['observed_wall_ms']) * 1_000_000
        common['timing_scope'] = 'legacy wall-only callback timing; clock adjustment uncertainty'

    root_span = SpanRecord(trace, parent, None, 'pi.episode', 'INTERNAL', ns(events[0]), ns(events[-1]),
                           common, 'ERROR' if result['error'] or result['errors'] else 'OK')
    spans = [root_span]
    active = {}
    for seq, event in enumerate(events):
        kind = event.get('type')
        if kind == 'message_start' and event.get('message', {}).get('role') == 'assistant':
            active['model'] = event
        elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
            start = active.pop('model', None)
            if start is not None:
                usage = event['message'].get('usage', {})
                attrs = {**common, 'gen_ai.operation.name': 'responses', 'gen_ai.request.model': 'Qwen3.8-27B'}
                attrs.update({'pi.usage.' + k: v for k, v in usage.items() if isinstance(v, (int, float))})
                sid = hashlib.sha256(f'{trace}:model:{seq}'.encode()).hexdigest()[:16]
                spans.append(SpanRecord(trace, sid, parent, 'pi.model', 'CLIENT', ns(start), ns(event), attrs))
        elif kind == 'tool_execution_start':
            active['tool:' + event['toolCallId']] = event
        elif kind == 'tool_execution_end':
            start = active.pop('tool:' + event['toolCallId'], None)
            if start is not None:
                sid = hashlib.sha256(f'{trace}:tool:{event["toolCallId"]}'.encode()).hexdigest()[:16]
                attrs = {**common, 'gen_ai.tool.name': event['toolName'], 'tool_call_id': event['toolCallId']}
                spans.append(SpanRecord(trace, sid, parent, 'pi.tool', 'CLIENT', ns(start), ns(event), attrs,
                                        'ERROR' if event.get('isError') else 'OK'))
    return spans


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--endpoint', default='http://127.0.0.1:4317')
    args = parser.parse_args()
    spans = spans_for(args.root)
    destination = args.root / 'otel' / 'spans.jsonl'
    destination.parent.mkdir(exist_ok=True)
    export_jsonl(spans, destination)
    sent = export_otlp(spans, endpoint=args.endpoint, service_name='gare2e-pi')
    (args.root / 'otel-export.json').write_text(json.dumps({'trace_id': spans[0].trace_id,
        'spans': len(spans), 'export_attempted': sent,
        'delivery_verified': False, 'scope': 'recorded native callbacks; no invented request/engine joins'}, indent=2))


if __name__ == '__main__':
    main()
