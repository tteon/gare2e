"""Wire the retained single-session CLI to its existing observer and S3 gate."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

ROOT = Path('/opt/gare2e-pilot/source')
HERE = ROOT / 'docker/agentic-os-appliance/vast-runtime'


def raw_files() -> list[Path]:
    artifacts = Path('/artifacts')
    return sorted([p for pattern in ('observability/resources-*/*.csv', 'observability/resources-*/*.jsonl', 'engine-observer/*.jsonl',
                                     'observability/*.log')
                   for p in artifacts.glob(pattern) if p.is_file() and not p.is_symlink()])


def snapshot_raw(out: Path, offsets: dict[str, int]) -> None:
    """Copy the existing sampler/observer bytes, without inventing ID joins."""
    rows = []
    for source in raw_files():
        start = offsets.get(str(source), 0)
        size = source.stat().st_size
        if size < start or size - start > 1024 ** 3:
            raise ValueError('runtime raw truncated or snapshot budget exceeded')
        target = out / 'runtime-raw' / source.relative_to('/artifacts')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with source.open('rb') as stream:
            header = stream.readline() if source.suffix == '.csv' and start else b''
            stream.seek(start)
            body = stream.read(size - start)
        if len(body) != size - start:
            raise ValueError('runtime raw changed during snapshot')
        target.write_bytes(header + body)
        rows.append(dict(source=str(source), start_byte=start, end_byte=size,
                         csv_header_added=bool(header), sha256=hashlib.sha256(header + body).hexdigest()))
    (out / 'runtime-raw-manifest.json').write_text(json.dumps({
        'scope': 'existing sampler/engine byte ranges during this session; timestamps and IDs require original joins',
        'files': rows}, indent=2))


def context_for(config: dict) -> tuple[Path, dict]:
    out = Path(config['output'])
    if not out.is_absolute() or not out.is_relative_to('/artifacts') or out == Path('/artifacts') or '..' in out.parts or out.exists():
        raise ValueError('fresh absolute /artifacts/<run-id> output required')
    context = json.loads(Path(config['env']['PI_TRACE_CONTEXT_FILE']).read_text())
    if not re.fullmatch('[0-9a-f]{32}', context['trace_id']) or not re.fullmatch('[0-9a-f]{16}', context['root_span_id']):
        raise ValueError('original trace context required')
    for key in ('project_id', 'campaign_id', 'experiment_id', 'run_id'):
        value = context['experiment'][key]
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) or '..' in value:
            raise ValueError('safe experiment identity required')
    if context['experiment']['run_id'] != out.name:
        raise ValueError('trace and archive run identity mismatch')
    return out, context


def native(config_path: str) -> int:
    config = json.loads(Path(config_path).read_text())
    status = subprocess.run(['node', str(ROOT / 'scripts/benchmarks/pi_native/native_case.mjs'), config_path]).returncode
    out = Path(config['output'])
    if (out / 'agent-events.jsonl').is_file():
        export = subprocess.run([sys.executable, str(HERE / 'export_pi_otel.py'), str(out)]).returncode
        if status == 0 and export != 0:
            return export
    return status


def main() -> int:
    os.umask(0o077)
    if sys.argv[1:2] == ['--native']:
        return native(sys.argv[2])
    config_path = sys.argv[1]
    config = json.loads(Path(config_path).read_text())
    out, context = context_for(config)
    env = {**os.environ, **{'SEOCHO_' + k.upper(): v for k, v in context['experiment'].items()},
           'SEOCHO_OBSERVABILITY_ENDPOINT': '127.0.0.1:4317',
           'SEOCHO_VLLM_METRICS_TARGET': '127.0.0.1:8000'}
    exporter = ROOT / 'docker/agentic-os-appliance/observability/vast/export_private_run.py'
    uri = 's3://' + env['GARE2E_S3_BUCKET'] + '/' + env['GARE2E_S3_PREFIX']
    subprocess.run([sys.executable, str(exporter), '--preflight', '--s3-uri', uri], env=env, check=True)
    subprocess.run(['node', str(ROOT / 'scripts/benchmarks/pi_native/native_case.mjs'), config_path, '--check-imports'], env=env, check=True)
    out.mkdir(parents=True, mode=0o700)
    (out / 'trace-context.json').write_text(json.dumps(context))
    # This timeout bounds the retained session plus cleanup; engine settlement
    # still belongs to the existing owner, never inferred from process exit.
    deadline_s = config.get('profile', {}).get('deadline_ms', 120000) / 1000
    offsets = {str(p): p.stat().st_size for p in raw_files()}
    child = None
    interrupted = False

    def stop(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    status, archive = 1, 1
    try:
        if interrupted:
            raise InterruptedError('stopped before workload admission')
        child = subprocess.Popen([
            sys.executable, str(ROOT / 'scripts/benchmarks/run_observed_experiment.py'),
            '--external-collector', '--artifacts', str(out / 'observation'),
            '--timeout-s', str(deadline_s + 15), '--', sys.executable, str(HERE / 'run_pi.py'),
            '--native', config_path,
        ], env=env)
        status = child.wait()
    finally:
        try:
            snapshot_raw(out, offsets)
        except Exception as error:
            (out / 'runtime-raw-gap.json').write_text(json.dumps({'error_class': type(error).__name__}))
            status = status or 1
        if interrupted:
            status = status or 130
        stage = Path('/artifacts/export-staging') / (out.name + '.private.tar.gz')
        try:
            archive = subprocess.run([sys.executable, str(exporter), '--run', str(out),
                                      '--output', str(stage), '--s3-uri', uri], env=env).returncode
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return status if status else archive


if __name__ == '__main__':
    raise SystemExit(main())
