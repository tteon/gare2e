"""Opt-in private graph-call spool. Never exports telemetry or credentials.

One immutable JSONL object per episode; each row is one physical HTTP attempt
(or an explicitly incomplete call). Checkpoint journals survive interruption.
Network I/O happens only in flush(), outside model requests.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .agentic_os_artifacts import canonical_digest
from .experiment_observability import experiment_identity

_LOG = logging.getLogger(__name__)
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(body)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _ids(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(type(x) is int and x >= 0 for x in value)


def call_samples(events: list[dict[str, Any]], provenance: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep raw calls and tool events; eligibility never depends on QA accuracy."""
    samples = []
    tool_events = [e for e in events if e['event'] == 'tool_call']
    for event in events:
        if event['event'] != 'model_call':
            continue
        attempts = event.get('http_attempts') or [{
            'request': event.get('http_request'), 'response': event.get('http_response'),
            'status_code': event.get('status_code'),
            **{k: v for k, v in event.items() if k.startswith('wire_')},
        }]
        for index, attempt in enumerate(attempts):
            request = attempt.get('request') or {}
            response = attempt.get('response') or {}
            request = request if isinstance(request, dict) else {}
            response = response if isinstance(response, dict) else {}
            choices = response.get('choices')
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            prompt_ids, output_ids = response.get('prompt_token_ids'), choice.get('token_ids')
            reasons = list(provenance.get('ineligible_reasons', []))
            for field in ('dataset_sha256', 'case_sha256', 'graph_sha256', 'selection_sha256'):
                if not _SHA.fullmatch(str(provenance.get(field, ''))):
                    reasons.append(field + '_missing')
            for field in ('source_dataset', 'source_revision', 'seed_group'):
                if not provenance.get(field):
                    reasons.append(field + '_missing')
            pin = provenance.get('serving', {})
            for field in ('tokenizer_sha256', 'template_sha256', 'source_sha256'):
                if not _SHA.fullmatch(str(pin.get(field, ''))):
                    reasons.append(field + '_missing')
            if not re.fullmatch(r'[0-9a-f]{40,64}', str(pin.get('model_revision', ''))) or pin.get('model') != request.get('model'):
                reasons.append('model_pin_missing_or_mismatched')
            if provenance.get('frozen_exclusion_verified') is not True:
                reasons.append('frozen_exclusion_unverified')
            if provenance.get('source_split') != 'train' or provenance.get('split') != 'train':
                reasons.append('not_train_split')
            if event.get('status') != 'ok' or attempt.get('status_code') != 200:
                reasons.append('failed_or_incomplete_call')
            if not request.get('messages') or not isinstance(choice.get('message'), dict):
                reasons.append('missing_messages_or_response')
            if not (_ids(prompt_ids) and _ids(output_ids)):
                reasons.append('endpoint_token_ids_unavailable')
            for side in ('request', 'response'):
                wire = attempt.get(f'wire_{side}_json')
                if not isinstance(wire, str) or hashlib.sha256(wire.encode()).hexdigest() != attempt.get(f'wire_{side}_sha256'):
                    reasons.append(f'exact_{side}_unavailable')
                else:
                    try:
                        if json.loads(wire) != (request if side == 'request' else response):
                            reasons.append(f'exact_{side}_mismatch')
                    except ValueError:
                        reasons.append(f'exact_{side}_invalid_json')
            usage = response.get('usage')
            usage = usage if isinstance(usage, dict) else {}
            if _ids(prompt_ids) and usage.get('prompt_tokens') != len(prompt_ids):
                reasons.append('prompt_token_count_mismatch')
            if _ids(output_ids) and usage.get('completion_tokens') != len(output_ids):
                reasons.append('response_token_count_mismatch')
            message = choice.get('message')
            emitted_calls = message.get('tool_calls') or [] if isinstance(message, dict) else []
            if not isinstance(emitted_calls, list):
                reasons.append('invalid_native_tool_calls')
                emitted_calls = []
            for tool in emitted_calls:
                if not isinstance(tool, dict) or not tool.get('id') or not any(e.get('native_call_id') == tool.get('id') and 'raw_tool_result' in e for e in tool_events):
                    reasons.append('tool_result_unavailable')
            if not response.get('usage') or not choice.get('finish_reason'):
                reasons.append('usage_or_finish_reason_unavailable')
            sample_id = f"{event['event_id']}:attempt:{index}"
            samples.append({
                'schema_version': 'seocho.private_graph_call.v1',
                'sample_id': sample_id, 'call_id': event['event_id'],
                'parent_call_id': event.get('parent_call_id'),
                'trace_id': attempt.get('trace_id', event.get('trace_id')),
                'span_id': attempt.get('span_id', event.get('span_id')),
                'branch_id': event.get('branch_id'),
                'retry_of_sample_id': f"{event['event_id']}:attempt:{index - 1}" if index else None,
                'attempt_index': index, 'event': event, 'http_attempt': attempt,
                'tool_events': tool_events, 'provenance': dict(provenance),
                'messages': request.get('messages'), 'tools': request.get('tools'),
                'assistant': choice.get('message'), 'usage': response.get('usage'),
                'finish_reason': choice.get('finish_reason'),
                'prompt_token_ids': prompt_ids, 'response_token_ids': output_ids,
                'training_eligible': not reasons, 'ineligible_reasons': sorted(set(reasons)),
            })
    return samples


class PrivateTraceStore:
    def __init__(self, config: Mapping[str, Any], *, client: Any = None) -> None:
        self.config = dict(config)
        self.identity = experiment_identity()
        self.root = Path(config['spool_root'])
        if self.identity:
            self.root = self.root.joinpath(*self.identity.values())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.bucket = str(config['bucket'])
        self.prefix = str(config['prefix']).strip('/')
        # A dedicated descendant of the already-authorized telemetry prefix.
        allowed = str(config['authorized_prefix']).strip('/')
        if not allowed or not self.prefix.startswith(allowed + '/private/'):
            raise ValueError('private prefix must be below authorized_prefix/private/')
        if any(part in {'', '.', '..'} for part in self.prefix.split('/')):
            raise ValueError('invalid private prefix segment')
        if self.identity:
            self.prefix = '/'.join([allowed, 'private', *self.identity.values(), 'calls'])
        self.client = client
        self.max_objects = int(config.get('flush_max_objects', 20))
        self.max_seconds = float(config.get('flush_max_seconds', 30))
        if not 1 <= self.max_objects <= 1000 or not 0 < self.max_seconds <= 60:
            raise ValueError('flush limits must be 1..1000 objects and 0..60 seconds')
        self.selection = config.get('selection', {})
        assignments: dict[tuple[str, str], str] = {}
        for case_id, row in self.selection.items():
            split = row.get('split', 'unknown')
            for key, value in [('case', case_id), ('graph', row.get('graph_sha256')), ('seed_group', row.get('seed_group'))]:
                if value is None:
                    continue
                group = (key, str(value))
                if group in assignments and assignments[group] != split:
                    raise ValueError('case/graph/seed-group crosses dataset splits')
                assignments[group] = split

    def checkpoint(self, journal_id: str, event: Mapping[str, Any], provenance: Mapping[str, Any]) -> None:
        """Append event revisions; a torn final line is ignored only on recovery."""
        path = self.root / (journal_id + '.journal')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'ab') as out:
            out.write(_bytes({'event': event, 'provenance': provenance}) + b'\n')
            out.flush()
            os.fsync(out.fileno())

    def seal(self, journal: Path) -> Path | None:
        events: dict[str, dict[str, Any]] = {}
        provenance: dict[str, Any] = {}
        lines = journal.read_bytes().splitlines(keepends=True)
        for line in lines:
            if not line.endswith(b'\n'):
                break
            row = json.loads(line)
            events[row['event']['event_id']] = row['event']
            provenance = row['provenance']
        samples = call_samples(list(events.values()), provenance)
        if not samples:
            return None
        body = b''.join(_bytes(sample) + b'\n' for sample in samples)
        sha = hashlib.sha256(body).hexdigest()
        target = self.root / (sha + '.jsonl')
        _write(target, body)
        manifest = {
            'schema_version': 'seocho.private_graph_manifest.v1',
            'sha256': sha, 'bytes': len(body), 'sample_count': len(samples),
            'experiment': self.identity, 'object_key': f'{self.prefix}/{sha}.jsonl',
            'trace_ids': sorted({s['trace_id'] for s in samples if s.get('trace_id')}),
            'training_eligible_count': sum(s['training_eligible'] for s in samples),
            'samples': [{'sample_id': s['sample_id'], 'sha256': hashlib.sha256(_bytes(s)).hexdigest()} for s in samples],
        }
        _write(target.with_suffix('.manifest.json'), _bytes(manifest))
        return target

    def flush(self) -> dict[str, int]:
        """Idempotent bounded upload + download/SHA verification; retain failures.

        The deadline bounds admission of objects. An in-flight SDK operation is
        additionally bounded by connect/read timeouts and disabled SDK retries.
        """
        result = {'verified': 0, 'failed': 0, 'pending': 0}
        deadline = time.monotonic() + self.max_seconds
        for path in sorted(self.root.glob('*.jsonl')):
            if path.with_suffix('.uploaded').exists():
                continue
            if result['verified'] + result['failed'] >= self.max_objects or time.monotonic() >= deadline:
                result['pending'] += 1
                continue
            try:
                if self.client is None:
                    import boto3
                    from botocore.config import Config
                    self.client = boto3.client('s3', config=Config(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1}))
                body = path.read_bytes()
                sha = hashlib.sha256(body).hexdigest()
                if sha != path.stem:
                    raise ValueError('local payload checksum mismatch')
                manifest_path = path.with_suffix('.manifest.json')
                manifest = json.loads(manifest_path.read_bytes())
                if manifest['sha256'] != sha or manifest['bytes'] != len(body):
                    raise ValueError('local manifest mismatch')
                for suffix, data in [('.jsonl', body), ('.manifest.json', manifest_path.read_bytes())]:
                    key = f'{self.prefix}/{sha}{suffix}'
                    self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                        ContentType='application/json', Metadata={'sha256': hashlib.sha256(data).hexdigest()})
                    stream = self.client.get_object(Bucket=self.bucket, Key=key)['Body']
                    try:
                        if hashlib.sha256(stream.read()).digest() != hashlib.sha256(data).digest():
                            raise ValueError('remote checksum mismatch')
                    finally:
                        stream.close()
                _write(path.with_suffix('.uploaded'), _bytes({'sha256': sha, 'verified': True}))
                result['verified'] += 1
            except Exception as error:
                # Provider exceptions may embed URLs/credentials; keep class only.
                _write(path.with_suffix('.failure.json'), _bytes({'error_class': type(error).__name__, 'time': time.time()}))
                result['failed'] += 1
        return result

    def trace(self, correlation: Mapping[str, Any], *, case: Any, dataset_sha256: str, model: str, condition: str) -> Any:
        from .agentic_graph_agents import PrivateTrace
        selected = self.selection.get(str(case.case_id), {})
        row = {key: selected[key] for key in ('source_split', 'split', 'graph_sha256', 'case_sha256',
            'dataset_sha256', 'source_dataset', 'source_revision', 'source_row_sha256',
            'source_shard_sha256', 'seed_group') if key in selected}
        graph_sha = canonical_digest([edge.to_triple() for edge in case.graph_edges])
        case_sha = canonical_digest({'question': case.question, 'graph_sha256': graph_sha})
        reasons = []
        for field, actual in [('graph_sha256', graph_sha), ('case_sha256', case_sha), ('dataset_sha256', dataset_sha256)]:
            if row.get(field) != actual:
                reasons.append(field + '_unverified')
        if not row.get('seed_group'):
            reasons.append('seed_group_missing')
        frozen = self.config.get('frozen_case_ids')
        if not isinstance(frozen, list) or not frozen:
            reasons.append('frozen_exclusion_unverified')
        elif str(case.case_id) in {str(x) for x in frozen}:
            reasons.append('frozen_evaluation_case')
        serving = self.config.get('serving_provenance', {})
        pin = {key: serving[key] for key in ('model', 'model_revision', 'tokenizer_sha256',
            'template_sha256', 'source_sha256') if key in serving}
        if pin.get('model') != model or not re.fullmatch(r'[0-9a-f]{40,64}', str(pin.get('model_revision', ''))):
            reasons.append('model_revision_unverified')
        for field in ('tokenizer_sha256', 'template_sha256', 'source_sha256'):
            if not _SHA.fullmatch(str(pin.get(field, ''))):
                reasons.append(field + '_missing')
        if not row.get('source_revision') or not row.get('source_dataset'):
            reasons.append('source_provenance_missing')
        provenance = {'source_split': 'unknown', 'split': 'unknown', **row, 'serving': pin, 'condition': condition,
            'dataset_sha256': dataset_sha256, 'case_sha256': case_sha, 'graph_sha256': graph_sha,
            'ineligible_reasons': reasons, 'selection_sha256': canonical_digest(self.selection),
            'frozen_exclusion_verified': isinstance(frozen, list) and bool(frozen) and str(case.case_id) not in {str(x) for x in frozen}}
        journal_id = uuid.uuid4().hex
        return PrivateTrace({**correlation, **self.identity, 'capture_id': journal_id, 'workspace_id': case.workspace_id},
            checkpoint=lambda event: self.checkpoint(journal_id, event, provenance))

    def trace_external(self, correlation: Mapping[str, Any]) -> Any:
        """Capture non-GTSQA calls without claiming verified training provenance."""
        from .agentic_graph_agents import PrivateTrace
        journal_id = uuid.uuid4().hex
        provenance = {'source_split': 'unknown', 'split': 'unknown',
                      'ineligible_reasons': ['external_source_provenance_unverified']}
        return PrivateTrace({**correlation, **self.identity, 'capture_id': journal_id},
            checkpoint=lambda event: self.checkpoint(journal_id, event, provenance))

    def finish(self, trace: Any) -> None:
        journal = self.root / (trace.correlation['capture_id'] + '.journal')
        if journal.exists():
            self.seal(journal)


def store_from_env() -> PrivateTraceStore | None:
    path = os.environ.get('SEOCHO_PRIVATE_TRACE_CONFIG')
    if not path:
        return None
    store = PrivateTraceStore(json.loads(Path(path).read_text()))
    def flush_at_exit() -> None:
        result = store.flush()
        _write(store.root / 'delivery-status.json', _bytes(result))
        (_LOG.warning if result['failed'] or result['pending'] else _LOG.info)('Private trace upload: %s', result)
    atexit.register(flush_at_exit)
    return store


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Resume private trace uploads without rerunning inference')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--recover', action='store_true', help='Seal journals only after their writer has stopped')
    args = parser.parse_args()
    store = PrivateTraceStore(json.loads(args.config.read_text()))
    if args.recover:
        for journal in store.root.glob('*.journal'):
            store.seal(journal)
    result = store.flush()
    print(json.dumps(result))
    return 1 if result['failed'] or result['pending'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
