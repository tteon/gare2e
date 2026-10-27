"""CPU-only installation check: no owner, DB, model, download or GPU launch."""
from __future__ import annotations

import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import tempfile


def main() -> None:
    root = Path('/opt/gare2e-pilot/source')
    versions = {name: metadata.version(name) for name in
                ('torch', 'vllm', 'neo4j', 'boto3', 'openai-agents')}
    assert versions['torch'].split('+')[0] == '2.13.0'
    assert versions['vllm'] == '0.31.0'
    assert versions['neo4j'] == '5.28.2'
    assert versions['boto3'] == '1.40.76'
    assert versions['openai-agents'] == '0.22.1'
    import seocho  # noqa: F401
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / 'config.json'
        config.write_text('{}')
        subprocess.run(['node', str(root / 'scripts/benchmarks/pi_native/native_case.mjs'),
                        str(config), '--check-imports'], check=True)
    subprocess.run(['python', str(root / 'scripts/benchmarks/load_benchmark_tasks.py'),
                    '--help'], check=True, stdout=subprocess.DEVNULL)
    collector = subprocess.check_output(['otelcol-contrib', '--version'], text=True).strip()
    assert '0.119.0' in collector
    print(json.dumps({'scope': 'CPU installation/imports only; no native GPU parity',
                      'versions': versions, 'pi': '0.80.10', 'collector': collector}))


if __name__ == '__main__':
    main()
