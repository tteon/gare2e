"""Numerical observer invariants; these tests make no throughput claim."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest


def observer(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = types.ModuleType('vllm.v1.core.sched.async_scheduler')
    fake.AsyncScheduler = object
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    path = Path(__file__).resolve().parents[2] / 'docker/agentic-os-appliance/observed_scheduler.py'
    spec = importlib.util.spec_from_file_location('observer_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mixed_and_recomputed_decode_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    module = observer(monkeypatch)
    requests = {name: types.SimpleNamespace(num_prompt_tokens=100, num_computed_tokens=pos)
                for name, pos in [('prefill', 0), ('mixed', 99), ('decode', 130)]}
    output = types.SimpleNamespace(num_scheduled_tokens={'prefill': 40, 'mixed': 3, 'decode': 2},
                                   scheduled_spec_decode_tokens={'mixed': [-1, -1]})
    counts = module.scheduled_counts(requests, output)
    assert counts['scheduled_sequences'] == 3
    assert counts['prompt_sequences'] == counts['decode_sequences'] == 2
    assert counts['mixed_sequences'] == 1
    assert counts['scheduled_prompt_tokens'] == 41
    assert counts['scheduled_tokens'] == 45


def test_kv_categories_do_not_double_count_reusable_active_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    module = observer(monkeypatch)
    blocks = [types.SimpleNamespace(block_id=i, is_null=i == 0, ref_cnt=ref, block_hash=hashed)
              for i, (ref, hashed) in enumerate([(0, None), (2, 'a'), (0, 'b'), (0, None), (0, None)])]
    counts = module.block_counts(types.SimpleNamespace(blocks=blocks, cached_block_hashes_by_block={4: {'partial'}}))
    assert counts == dict(active_blocks=1, reclaimable_cached_blocks=2, free_uncached_blocks=1, usable_blocks=4)


def load_script(name: str) -> Any:
    directory = Path(__file__).resolve().parents[2] / 'scripts/benchmarks'
    spec = importlib.util.spec_from_file_location(name, directory / (name + '.py'))
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kernel_stream_overlap_is_unioned_not_summed() -> None:
    module = load_script('summarize_agentic_os_kernel_profile')
    result = module.intervals_summary([(0, 10), (5, 15), (20, 25)])
    assert result['kernel_union_us'] == 20
    assert result['first_to_last_kernel_us'] == 25
    assert result['gap_fraction'] == pytest.approx(.2)
    assert result['gap_us']['mean'] == 5
    assert not module.intervals_summary([])['observed']


def test_unobserved_engine_fails_gate_and_empty_grammar_is_unknown() -> None:
    module = load_script('summarize_agentic_os_engine_steps')
    result = module.summarize([], 0, 10)
    assert not result['passed']
    assert result['grammar']['grammar_compile']['thread_cpu_s'] is None
    assert result['schedules']['decode_sequences'] is None


def test_saturation_plan_preserves_gate_and_replicates_each_condition() -> None:
    module = load_script('run_agentic_os_throughput_session')
    plan = module.plan(runtime_audit=True)
    assert plan[0]['limit'] == plan[2]['limit'] == 1
    assert not plan[0]['observer'] and plan[2]['observer']
    for name in ('base', 'mtp'):
        cells = [c for c in plan if c['id'].startswith(name) and not c.get('limit')]
        assert [c['k'] for c in cells] == [8, 64, 128, 256, 256, 128, 64, 8]
        assert all(c['observer'] and c['replicas'] == 1 for c in cells)
    with pytest.raises(ValueError):
        module.plan(runtime_audit=True, replica_audit=True)


def test_pressure_corpus_does_not_pad_a_short_dataset(tmp_path: Path) -> None:
    import gzip
    module = load_script('run_agentic_os_context_pressure')
    corpus = tmp_path / 'nodes.csv.gz'
    with gzip.open(corpus, 'wt') as stream:
        stream.write('id,feature_title\n1,actual fixture record\n')
    tokenizer = types.SimpleNamespace(encode=lambda value, **kwargs: list(value.encode()))
    with pytest.raises(ValueError, match='lacks'):
        module.corpus_tokens(corpus, tokenizer, minimum=1000)
    tokens, count = module.corpus_tokens(corpus, tokenizer, minimum=1)
    assert count == 1 and b'actual fixture record' in bytes(tokens)


def test_report_keeps_serving_diagnostic_separate_and_missing_metrics_unknown(tmp_path: Path) -> None:
    import json
    module = load_script('summarize_agentic_os_runtime_pressure')
    for name in ('base-r1-k64', 'context-24576-k64-shared'):
        directory = tmp_path / ('run-' + name)
        directory.mkdir()
        (directory / 'pass-receipt.json').write_text(json.dumps(dict(pass_id=name, exit_code=3)))
    result = module.collect(tmp_path)
    assert [r['category'] for r in result['conditions']] == ['saturation', 'context']
    assert all(r['factors'] == {} for r in result['conditions'])
    assert len(result['source_sha256']) == 2
