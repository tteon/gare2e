#!/usr/bin/env python3
"""Bounded per-backend observation; preserve fractions instead of summing them."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from seocho.eval.agentic_os_observability import VllmMetricsClient, VllmMetricsSampler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pass-root', type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, default=1800)
    parser.add_argument('--stop-file', type=Path)
    args = parser.parse_args()
    if args.max_seconds <= 0:
        parser.error('max seconds must be positive')
    start = time.time()
    deadline = time.monotonic() + args.max_seconds
    # Never create the pass root before the registered session creates it.
    while not args.pass_root.is_dir() and time.monotonic() < deadline:
        time.sleep(1)
    if not args.pass_root.is_dir():
        return 2
    output = args.pass_root / 'workload'
    output.mkdir(exist_ok=True)
    receipt = {'schema_version': 'agentic_os.replica_observation.v1', 'started_unix_s': start,
               'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'scope': 'per-backend fractions; two additional metrics GETs per sampling cycle',
               'interval_s': 1, 'max_seconds': args.max_seconds}
    samplers = [VllmMetricsSampler(output / f'replica-{i}-metrics.jsonl',
                client=VllmMetricsClient(f'http://127.0.0.1:{8001+i}/metrics', timeout_s=.5),
                require_available=False) for i in range(2)]
    cycles = 0
    while (time.monotonic() < deadline and not (args.pass_root / 'pass-receipt.json').exists()
           and not (args.stop_file and args.stop_file.exists())):
        began = time.monotonic()
        for sampler in samplers:
            sampler.sample_once()
        cycles += 1
        time.sleep(max(0, 1 - (time.monotonic() - began)))
    receipt.update(finished_unix_s=time.time(), cycles=cycles,
                   pass_finished=(args.pass_root / 'pass-receipt.json').exists(),
                   stop_requested=bool(args.stop_file and args.stop_file.exists()))
    (output / 'replica-observer.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
