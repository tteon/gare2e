#!/usr/bin/env python3
"""Fail-closed live R570/A100 CUDA13 probe before model or database startup."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


def probe() -> dict[str, Any]:
    import torch

    result: dict[str, Any] = {'schema_version': 'agentic_os.cuda_compat_probe.v1',
        'started_unix_s': time.time(), 'passed': False, 'torch_cuda': torch.version.cuda,
        'enabled': os.environ.get('VLLM_ENABLE_CUDA_COMPATIBILITY'),
        'library_path': os.environ.get('VLLM_CUDA_COMPATIBILITY_PATH')}
    smi = subprocess.run(['nvidia-smi', '--query-gpu=driver_version,name', '--format=csv,noheader'],
                         capture_output=True, text=True, check=True, timeout=15)
    devices = [line.strip().split(', ', 1) for line in smi.stdout.splitlines() if line.strip()]
    result['devices'] = [{'driver': d, 'name': n} for d, n in devices]
    if len(devices) != 2 or any(not d.startswith('570.') or 'A100' not in n for d, n in devices):
        raise RuntimeError('registered compatibility probe requires exactly two R570 A100 GPUs')
    if result['enabled'] != '1' or not str(torch.version.cuda).startswith('13.0'):
        raise RuntimeError('CUDA compatibility environment or toolkit mismatch')
    count = torch.cuda.device_count()
    if count != 2:
        raise RuntimeError('CUDA did not expose exactly two devices')
    for index in range(count):
        with torch.cuda.device(index):
            a = torch.ones((32, 32), dtype=torch.bfloat16, device=f'cuda:{index}')
            b = a @ a
            torch.cuda.synchronize(index)
            if not bool(torch.all(b == 32).item()):
                raise RuntimeError('device matrix multiply check failed')
    result['peer_access'] = [torch.cuda.can_device_access_peer(0, 1), torch.cuda.can_device_access_peer(1, 0)]
    if not all(result['peer_access']):
        raise RuntimeError('bidirectional GPU peer access is unavailable')
    mappings = Path('/proc/self/maps').read_text()
    result['loaded_libcuda'] = sorted({line.split()[-1] for line in mappings.splitlines() if '/libcuda.so' in line})
    if not result['loaded_libcuda'] or not all('/compat/' in p for p in result['loaded_libcuda']):
        raise RuntimeError('CUDA compatibility driver library was not actually loaded')
    cuda = ctypes.CDLL('libcuda.so.1')
    version = ctypes.c_int()
    if cuda.cuDriverGetVersion(ctypes.byref(version)):
        raise RuntimeError('driver version query failed')
    result['cuda_driver_api_version'] = version.value
    if version.value < 13000:
        raise RuntimeError('loaded CUDA driver API is below 13.0')
    result.update(passed=True, finished_unix_s=time.time(), scope='live device/peer smoke; not throughput or full vLLM compatibility evidence')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = probe()
    except Exception as exc:
        result = {'schema_version': 'agentic_os.cuda_compat_probe.v1', 'passed': False,
                  'error_type': type(exc).__name__, 'error': str(exc)[:400]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
