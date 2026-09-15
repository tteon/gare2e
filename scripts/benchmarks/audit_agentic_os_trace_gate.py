#!/usr/bin/env python3
"""Fail closed on missing/ambiguous parent joins; no content or temporal matching."""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from summarize_agentic_os_factors import read_rows
from summarize_agentic_os_otel_stages import STAGE_KEYS

REQUIRED_STAGES = ('queue_s', 'ttft_s', 'prefill_s', 'decode_s')


def audit(client: Path, server: Path, router: Path | None, *, replicas: int,
          require_all_backends: bool = False) -> dict[str, Any]:
    clients = [s for s in read_rows(client) if str(s.get('name', '')).startswith('chat ')]
    servers: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    routes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for span in read_rows(server):
        if span.get('name') == 'llm_request':
            servers[(span.get('trace_id'), span.get('parent_span_id'))].append(span)
    if router is not None:
        for route in read_rows(router):
            routes[(route.get('trace_id'), route.get('parent_span_id'))].append(route)
    matched = complete = routed = 0
    backends: set[int] = set()
    unique_clients = {(c.get('trace_id'), c.get('span_id')) for c in clients}
    valid_ids = all(isinstance(t, str) and isinstance(s, str)
                    and re.fullmatch('[0-9a-f]{32}', t) and re.fullmatch('[0-9a-f]{16}', s)
                    and int(t, 16) and int(s, 16) for t, s in unique_clients)
    for client_span in clients:
        key = (client_span.get('trace_id'), client_span.get('span_id'))
        spans = servers.get(key, [])
        if len(spans) == 1:
            matched += 1
            attrs = spans[0].get('attributes', {})
            values = [next((attrs[k] for k in STAGE_KEYS[stage] if k in attrs), None)
                      for stage in REQUIRED_STAGES]
            complete += all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            and math.isfinite(v) and v >= 0 for v in values)
        selected = routes.get(key, [])
        if len(selected) == 1 and selected[0].get('status') == 200:
            backend = selected[0].get('replica')
            if isinstance(backend, int) and 0 <= backend < replicas:
                routed += 1
                backends.add(backend)
    count = len(clients)
    passed = bool(count and valid_ids and len(unique_clients) == count and matched == count and complete == count)
    if replicas > 1:
        passed = passed and routed == count
        if require_all_backends:
            passed = passed and backends == set(range(replicas))
    return {'schema_version': 'agentic_os.trace_admission.v1', 'passed': passed,
            'client_requests': count, 'unique_client_requests': len(unique_clients),
            'exact_server_matches': matched, 'complete_stage_requests': complete,
            'exact_router_matches': routed, 'observed_backend_indices': sorted(backends),
            'expected_replicas': replicas, 'require_all_backends': require_all_backends,
            'required_stages': list(REQUIRED_STAGES),
            'scope': 'exact W3C parent IDs only; request times are not exclusive GPU time'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path, required=True)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--router', type=Path)
    parser.add_argument('--replicas', type=int, required=True)
    parser.add_argument('--require-all-backends', action='store_true')
    parser.add_argument('--wait-s', type=float, default=15)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.replicas < 1 or not 0 <= args.wait_s <= 60:
        parser.error('replicas must be positive and wait within 0..60 seconds')
    deadline = time.monotonic() + args.wait_s
    while True:
        try:
            result = audit(args.client, args.server, args.router, replicas=args.replicas,
                           require_all_backends=args.require_all_backends)
        except json.JSONDecodeError:
            # The live receiver may be midway through appending one JSONL row.
            result = {'schema_version': 'agentic_os.trace_admission.v1', 'passed': False,
                      'error_class': 'IncompleteOrInvalidJsonl'}
        if result['passed'] or time.monotonic() >= deadline:
            break
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as out:
        out.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['passed'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
