#!/usr/bin/env python3
"""Two-call serving diagnostics with actual graph-corpus token prefixes.

Responses do not control queries or subsequent prompts; quality is unscored.
The existing workload wrapper supplies GPU, host and server metric samples.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from prepare_agentic_os_throughput import write_new
from run_direct_react_text2cypher import _post, _usage_receipt, error_usage_receipt, llm_http_timeout_s
from seocho.eval.agentic_os_trace import case_trace


def corpus_tokens(path: Path, tokenizer: Any, minimum: int = 2_000_000) -> tuple[list[int], int]:
    """Read real records once; no repeated padding or invented evidence."""
    tokens: list[int] = []
    rows = 0
    with gzip.open(path, 'rt', newline='') as stream:
        for record in csv.DictReader(stream):
            tokens.extend(tokenizer.encode(json.dumps(record, ensure_ascii=False) + '\n', add_special_tokens=False))
            rows += 1
            if len(tokens) >= minimum:
                return tokens, rows
    raise ValueError('graph corpus lacks the registered distinct-token working set')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--k', type=int, required=True)
    parser.add_argument('--input-tokens', type=int, required=True)
    parser.add_argument('--locality', choices=('shared', 'distributed'), required=True)
    parser.add_argument('--reuse-delay-s', type=float, default=0)
    parser.add_argument('--duration-s', type=float, default=90)
    parser.add_argument('--warmup-s', type=float, default=15)
    args = parser.parse_args()
    if args.k not in (1, 8, 64) or args.input_tokens not in (8192, 24576) or args.reuse_delay_s < 0:
        parser.error('outside registered context-pressure bounds')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision, local_files_only=True)
    tokens, row_count = corpus_tokens(args.corpus, tokenizer)
    source_sha = hashlib.sha256(args.corpus.read_bytes()).hexdigest()
    args.root.mkdir(parents=True, exist_ok=True)
    write_new(args.root / 'context-contract.json', dict(source_sha256=source_sha, source_rows=row_count,
              available_tokens=len(tokens), input_tokens=args.input_tokens, k=args.k, locality=args.locality,
              reuse_delay_s=args.reuse_delay_s, output_tokens=128, calls_per_task=2,
              http_timeout_s=llm_http_timeout_s(),
              evidence_class='fixed_corpus_serving_diagnostic', quality='unscored', graph_queries_executed=0))
    lock = threading.Lock()
    counter = 0
    failure = threading.Event()
    began = time.time()
    measurement_start = began + args.warmup_s
    deadline = measurement_start + args.duration_s
    lifecycle: list[dict[str, Any]] = []

    def worker() -> None:
        nonlocal counter
        while time.time() < deadline and not failure.is_set():
            with lock:
                index = counter
                counter += 1
            scope = os.getenv('SEOCHO_EXPERIMENT_RUN_ID')
            prefix = f'{scope}:' if scope else ''
            run_id = f'{prefix}{args.root.parents[2].name}-task-{index}'
            # Shared uses the same actual corpus prefix. Distributed rotates
            # through 64 nonoverlapping 24K regions regardless of K; the next
            # call within a task reuses its prefix after the prescribed gap.
            offset = 0 if args.locality == 'shared' else (index % 64) * 24576
            prompt = tokens[offset:offset + args.input_tokens]
            if len(prompt) != args.input_tokens:
                raise ValueError('truncated corpus prefix')
            started = time.time()
            clock = time.perf_counter()
            turns: list[dict[str, Any]] = []
            terminal = 'replayed'
            with case_trace(run_id, str(index)) as trace:
                try:
                    for ordinal in range(2):
                        if ordinal:
                            time.sleep(args.reuse_delay_s)
                        request_start = time.perf_counter()
                        response, latency = _post(args.base_url + '/v1/completions',
                            dict(model=args.model, prompt=prompt, max_tokens=128, ignore_eos=True,
                                 temperature=0, seed=0))
                        row = _usage_receipt(response, latency)
                        row.update(started_elapsed_ms=(request_start - clock) * 1000,
                                   span_id=response.get('_seocho_span_id'))
                        turns.append(row)
                        if row['input_tokens'] != args.input_tokens or row['output_tokens'] != 128:
                            raise ValueError('server token accounting does not match fixed lengths')
                except Exception as exc:
                    if hasattr(exc, '_seocho_latency_ms'):
                        turns.append(error_usage_receipt(exc, clock))
                    terminal = type(exc).__name__
                    failure.set()
                finished = time.time()
                case = dict(run_id=run_id, case_id=str(index), task_id=run_id, agent_id='fixed_corpus_replay',
                    workspace_id='grbench-maple-physics', arm='S1', cache_phase='warm',
                    started_unix_s=started, trace=trace.receipt(), turns=turns, tools=[], terminal=terminal,
                    answer_status='not_evaluated', success=terminal == 'replayed',
                    time=dict(wall_ms=(finished - started) * 1000,
                              llm_ms=sum(r['latency_ms'] for r in turns), tool_ms=0))
                write_new(args.root / 'attempts' / run_id / 'run-receipt.json', dict(cases=[case]))
            receipt = dict(run_id=run_id, runner_started_unix_s=started,
                           runner_finished_unix_s=finished, runner_exit_code=0 if terminal == 'replayed' else 2)
            with lock:
                lifecycle.append(receipt)
                with (args.root / 'coordinator-receipts.jsonl').open('a') as stream:
                    stream.write(json.dumps(receipt) + '\n')

    with ThreadPoolExecutor(max_workers=args.k) as executor:
        futures = [executor.submit(worker) for _ in range(args.k)]
        for future in futures:
            future.result()
    write_new(args.root / 'coordinator-window.json', dict(window_status='available', workers=args.k,
              started_unix_s=measurement_start, finished_unix_s=deadline,
              execution_digest=hashlib.sha256(json.dumps(vars(args), default=str, sort_keys=True).encode()).hexdigest()))
    return 2 if failure.is_set() or not lifecycle else 0


if __name__ == '__main__':
    raise SystemExit(main())
