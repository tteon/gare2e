#!/usr/bin/env python3
"""Launch the approved lab 024 queue with existing lifecycle/ownership gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True, help='Local data workspace; code comes from this checkout')
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--preflight', type=Path, required=True)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--offer-id', type=int, help='Omit to admit a fresh candidate immediately before creation')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--continuation', action='store_true', help='Approved lab-s23: five remaining cells, max $5.40 / 90 min')
    parser.add_argument('--replica-audit', action='store_true', help='Lab025 prepared TP2/sticky/task-rr queue; max $3.40 / 60 min')
    parser.add_argument('--cuda-forward-compat', action='store_true', help='Pinned image built-in CUDA13 libraries on R570 A100 only')
    parser.add_argument('--runtime-audit', action='store_true', help='Lab026 sequential engine/saturation/context stages; max $12.60 / 210 min')
    parser.add_argument('--runtime-completion', action='store_true', help='One paired K sweep then all context cells; max7.50USD after4.992318 spent')
    parser.add_argument('--runtime-recovery', action='store_true', help='Lab026 stage2/3 after worker repair; max9.80USD retaining2.706425 already spent')
    args = parser.parse_args()
    args.runtime_recovery = args.runtime_recovery or args.runtime_completion
    args.runtime_audit = args.runtime_audit or args.runtime_recovery
    if sum((args.replica_audit, args.continuation, args.runtime_audit)) > 1:
        parser.error('choose one continuation scope')
    if args.cuda_forward_compat and not (args.continuation or args.replica_audit or args.runtime_audit):
        parser.error('forward compatibility is registered only for the A100 continuation')
    lifetime_s = 7500 if args.runtime_completion else 9800 if args.runtime_recovery else 12600 if args.runtime_audit else 3600 if args.replica_audit else 5400 if args.continuation else 13000
    max_dph = 3.4 if args.replica_audit else 3.6
    a100_only = args.continuation or args.replica_audit or args.runtime_audit
    label = 'seocho-agentic-os-lab-s25c' if args.runtime_completion else 'seocho-agentic-os-lab-s25b' if args.runtime_recovery else 'seocho-agentic-os-lab-s25' if args.runtime_audit else 'seocho-agentic-os-lab-s24' if args.replica_audit else 'seocho-agentic-os-lab-s23' if args.continuation else 'seocho-agentic-os-lab-s22'
    repo = Path(__file__).resolve().parents[3]
    bootstrap = args.workspace / '.seocho/benchmarks/agentic-os/colocated-appliance/bootstrap'
    strata = args.workspace / '.seocho/benchmarks/agentic-os/strata-v2'
    uploads = [
        (repo / 'scripts/benchmarks/bootstrap_agentic_os_appliance.sh', '/workspace/bootstrap/bootstrap_agentic_os_appliance.sh'),
        (bootstrap / 'dozerdb-runtime.tar.gz', '/workspace/bootstrap/dozerdb-runtime.tar.gz'),
        (args.bundle, '/workspace/bootstrap/seocho-source.tar.gz'),
        *[(bootstrap / 'grbench-import-v1' / name, '/workspace/input/' + name) for name in ('grbench-nodes.csv.gz', 'grbench-rels.csv.gz', 'grbench-import-manifest.json')],
        (args.workspace / '.seocho/datasets/graph-cot/data/processed_data/maple/Physics/data.json', '/workspace/input/grbench-questions.json'),
        *[(strata / name, '/workspace/input/strata-' + name) for name in ('grbench-discovery-records.jsonl', 'grbench-tool-schema.json', 'grbench-single-schedule-o1.json')],
    ]
    uploads[-1] = (uploads[-1][0], '/workspace/input/strata-grbench-single-schedule.json')
    for local, _ in uploads:
        if not local.is_file():
            raise ValueError('missing upload: ' + str(local))
    preflight = json.loads(args.preflight.read_text())
    if not preflight.get('passed') or preflight.get('source_bundle_sha256') != hashlib.sha256(args.bundle.read_bytes()).hexdigest():
        raise ValueError('preflight does not verify this bundle')
    env = dict(VLLM_MODEL='Qwen/Qwen3.6-27B', VLLM_MODEL_REVISION='6a9e13bd6fc8f0983b9b99948120bc37f49c13e9',
        VLLM_TENSOR_PARALLEL_SIZE='2', VLLM_MAX_NUM_SEQS='128', VLLM_MAX_MODEL_LEN='32768',
        VLLM_EXTRA_ARGS='--gpu-memory-utilization 0.92 --max-model-len 32768 --max-num-batched-tokens 8192',
        SEOCHO_GRBENCH_IMPORT_DIR='/workspace/input', SEOCHO_GRBENCH_WORKSPACE_ID='grbench-maple-physics',
        DOZERDB_HEAP_INITIAL_SIZE='8g', DOZERDB_HEAP_MAX_SIZE='8g', DOZERDB_PAGECACHE_SIZE='24g',
        SEOCHO_REPO_ROOT='/workspace/seocho', SEOCHO_APPLIANCE_INPUT='/workspace/input', SEOCHO_APPLIANCE_ARTIFACTS='/artifacts',
        SEOCHO_SPLIT_GATE_MAIN='0', SEOCHO_DISABLE_GPU_SAMPLER='1', SEOCHO_OTLP_ENDPOINT='http://127.0.0.1:4317', SEOCHO_OTEL_FILE_RECEIVER='1')
    if args.cuda_forward_compat:
        if not preflight.get('checks', {}).get('cuda_compat_libraries', {}).get('passed'):
            raise ValueError('forward compatibility requires exact-image library preflight')
        env.update(VLLM_ENABLE_CUDA_COMPATIBILITY='1', VLLM_CUDA_COMPATIBILITY_PATH='/usr/local/cuda-13.0/compat',
                   LD_LIBRARY_PATH='/usr/local/cuda-13.0/compat:/usr/local/nvidia/lib64:/usr/local/cuda/lib64:/usr/local/nvidia/lib',
                   SEOCHO_CUDA_COMPAT_PROBE='1')
    remote = ['timeout', '--signal=TERM', '6900' if args.runtime_completion else '8600' if args.runtime_recovery else '11400' if args.runtime_audit else '2700' if args.replica_audit else '4200' if args.continuation else '12000', 'env', *[f'{k}={v}' for k, v in env.items()],
        'bash', '/workspace/bootstrap/bootstrap_agentic_os_appliance.sh', 'python3',
        '/workspace/seocho/scripts/benchmarks/run_agentic_os_throughput_session.py']
    if args.continuation:
        remote += ['--continuation', '--max-session-s', '3000', '--reserve-s', '600']
    if args.replica_audit:
        remote += ['--replica-audit', '--max-session-s', '2300', '--reserve-s', '300']
    if args.runtime_completion:
        remote += ['--runtime-recovery', '--saturation-repetitions', '1', '--max-session-s', '6100', '--reserve-s', '300']
    elif args.runtime_recovery:
        remote += ['--runtime-recovery', '--max-session-s', '7400', '--reserve-s', '600']
    elif args.runtime_audit:
        remote += ['--runtime-audit', '--max-session-s', '9900', '--reserve-s', '900']
    command = [sys.executable, str(repo / 'scripts/benchmarks/vast_colocated_agentic_os_run.py'),
        '--offer-query', 'num_gpus=2 rentable=true gpu_ram>=79 bw_nvlink>0 ' + ('cuda_max_good=12.8' if args.cuda_forward_compat else 'cuda_max_good>=13.0'),
        '--gpu-name-regex', 'A100.*SXM' if a100_only else 'A100|H100|H200', '--require-nvlink', '--min-num-gpus', '2', '--min-gpu-total-ram', '160000',
        '--min-effective-cpu', '32' if a100_only else '16', '--min-cpu-ram-mb', '65536', '--min-reliability', '0.98',
        '--exclude-host-id', '3497', '--exclude-host-id', '60897', '--exclude-host-id', '258806',
        '--image', 'vllm/vllm-openai:v0.27.1', '--disk-gb', '200', '--label', label,
        '--max-dph', str(max_dph), '--startup-timeout-s', '900', '--collect-timeout-s', '900', '--collect-retries', '1',
        '--collect-bulk', 'essential-only', '--max-runtime-s', str(lifetime_s), '--remote-artifacts', '/artifacts',
        '--local-artifacts', str(args.run_root), '--remote-command', shlex.join(remote)]
    for local, destination in uploads:
        command += ['--upload', str(local.resolve()) + '=' + destination]
    if args.offer_id is not None:
        command += ['--offer-id', str(args.offer_id)]
    print(json.dumps({'label': label, 'offer_id': args.offer_id, 'max_dph': max_dph, 'cuda_forward_compat': args.cuda_forward_compat,
        'max_runtime_s': lifetime_s, 'worst_hourly_cost_usd': lifetime_s / 3600 * max_dph, 'upload_count': len(uploads), 'run_root': str(args.run_root)}), flush=True)
    if args.dry_run:
        return 0
    result = subprocess.run(['vastai', 'show', 'instances', '--raw'], capture_output=True, text=True, timeout=60, check=True)
    instances = json.loads(result.stdout)
    ours = [i['id'] for i in instances if str(i.get('label') or '').startswith('seocho-agentic-os-')]
    if ours:
        raise RuntimeError('prior owned instance may exist; launch refused: ' + str(ours))
    os.umask(0o077)
    return subprocess.run(command, check=False, timeout=lifetime_s + 1500).returncode


if __name__ == '__main__':
    raise SystemExit(main())
