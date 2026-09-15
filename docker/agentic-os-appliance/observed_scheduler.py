"""Opt-in, content-free observer for the pinned vLLM 0.27.1 scheduler.

The upstream scheduler retains every scheduling decision. CPU wall intervals
and schedule-to-output latency are not GPU kernel execution time.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from vllm.v1.core.sched.async_scheduler import AsyncScheduler


def scheduled_counts(requests: dict[str, Any], output: Any) -> dict[str, int]:
    """Called before upstream advances computed-token positions.

    A sequence may span prompt and decode positions in the same iteration;
    count it in both categories and expose mixed_sequences explicitly.
    Recomputed output positions remain decode positions, not new prompt.
    """
    prompt_seqs = decode_seqs = mixed = prompt_tokens = 0
    for rid, count in output.num_scheduled_tokens.items():
        req = requests[rid]
        prompt = min(count, max(0, req.num_prompt_tokens - req.num_computed_tokens))
        prompt_tokens += prompt
        prompt_seqs += prompt > 0
        decode_seqs += count > prompt
        mixed += 0 < prompt < count
    return dict(scheduled_sequences=len(output.num_scheduled_tokens),
                prompt_sequences=prompt_seqs, decode_sequences=decode_seqs,
                mixed_sequences=mixed, scheduled_prompt_tokens=prompt_tokens,
                scheduled_tokens=sum(output.num_scheduled_tokens.values()),
                draft_tokens_scheduled=sum(map(len, output.scheduled_spec_decode_tokens.values())))


def block_counts(pool: Any) -> dict[str, int]:
    """Disjoint physical block counts, excluding the reserved null block."""
    active = cached = empty = 0
    for block in pool.blocks:
        if block.is_null:
            continue
        if block.ref_cnt > 0:
            active += 1
        elif block.block_hash is not None or pool.cached_block_hashes_by_block.get(block.block_id):
            cached += 1
        else:
            empty += 1
    return dict(active_blocks=active, reclaimable_cached_blocks=cached,
                free_uncached_blocks=empty, usable_blocks=active + cached + empty)


class ObservedAsyncScheduler(AsyncScheduler):
    """Preserve async scheduling; record one engine event, never per TP rank."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        import vllm
        if vllm.__version__ != '0.27.1':
            raise RuntimeError('observer requires the preflighted vLLM 0.27.1 image')
        root = Path(os.environ['SEOCHO_ENGINE_OBSERVER_DIR'])
        root.mkdir(parents=True, exist_ok=True)
        self._observe_fd = os.open(root / f'engine-{os.getpid()}.jsonl',
                                   os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._observe_lock = threading.Lock()
        self._observe_step = 0
        self._observe_pending: dict[int, dict[str, Any]] = {}
        self._observe_last_kv = 0.0
        self._observe_last_schedule: float | None = None
        self._emit(dict(kind='init', version=vllm.__version__, async_scheduling=self.scheduler_config.async_scheduling))
        manager = self.structured_output_manager
        original = manager._create_grammar

        def create_grammar(*args: Any, **kwargs: Any) -> Any:
            start, cpu = time.perf_counter(), time.thread_time()
            try:
                return original(*args, **kwargs)
            finally:
                self._emit(dict(kind='grammar_compile', wall_s=time.perf_counter() - start,
                                thread_cpu_s=time.thread_time() - cpu))

        manager._create_grammar = create_grammar

    def _emit(self, row: dict[str, Any]) -> None:
        payload = (json.dumps(dict(schema_version='seocho.engine_step.v1',
                   unix_s=time.time(), engine_pid=os.getpid(), **row), separators=(',', ':')) + '\n').encode()
        with self._observe_lock:
            if os.write(self._observe_fd, payload) != len(payload):
                raise RuntimeError('partial engine observation write')

    def _update_after_schedule(self, output: Any) -> None:
        self._observe_counts = scheduled_counts(self.requests, output)
        super()._update_after_schedule(output)

    def schedule(self, *args: Any, **kwargs: Any) -> Any:
        start, cpu = time.perf_counter(), time.thread_time()
        output = super().schedule(*args, **kwargs)
        finished = time.perf_counter()
        self._observe_step += 1
        row = dict(kind='schedule', step_id=self._observe_step, **self._observe_counts,
                   scheduler_wall_s=finished - start, scheduler_thread_cpu_s=time.thread_time() - cpu,
                   schedule_interval_s=None if self._observe_last_schedule is None else start - self._observe_last_schedule,
                   running_sequences=len(self.running), waiting_sequences=len(self.waiting) + len(self.skipped_waiting),
                   preempted_sequences=len(output.preempted_req_ids or ()))
        self._observe_last_schedule = start
        if output.total_num_scheduled_tokens:
            self._observe_pending[id(output)] = dict(step_id=self._observe_step, scheduled_at=start)
        self._emit(row)
        if finished - self._observe_last_kv >= 1:
            scan_start = time.perf_counter()
            self._emit(dict(kind='kv_blocks', **block_counts(self.kv_cache_manager.block_pool),
                            scan_wall_s=time.perf_counter() - scan_start))
            self._observe_last_kv = finished
        return output

    def get_grammar_bitmask(self, output: Any) -> Any:
        start, cpu = time.perf_counter(), time.thread_time()
        value = super().get_grammar_bitmask(output)
        if output.has_structured_output_requests:
            self._emit(dict(kind='grammar_bitmask', step_id=self._observe_pending.get(id(output), {}).get('step_id'),
                            wall_s=time.perf_counter() - start, thread_cpu_s=time.thread_time() - cpu,
                            produced=value is not None))
        return value

    def update_from_output(self, output: Any, model_output: Any) -> Any:
        start, cpu = time.perf_counter(), time.thread_time()
        result = super().update_from_output(output, model_output)
        entry = self._observe_pending.pop(id(output), None)
        if entry is not None:
            committed = sum(len(item.new_token_ids) for group in result.values() for item in group.outputs)
            self._emit(dict(kind='output', step_id=entry['step_id'], committed_output_tokens=committed,
                            schedule_to_output_s=time.perf_counter() - entry['scheduled_at'],
                            update_wall_s=time.perf_counter() - start, update_thread_cpu_s=time.thread_time() - cpu))
        return result
