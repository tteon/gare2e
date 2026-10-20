#!/usr/bin/env python3
"""Offline, difficulty-filtered benchmark loading; CLI emits content-free references."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
from typing import Any

TASKS = ('gtsqa', 'grbench', 'finbench', 'finder', 'graphrag-bench', 'finance')
LEVELS = ('easy', 'medium', 'hard', 'beginner', 'advanced', 'unlabeled')


def _difficulty_module() -> Any:
    path = Path(__file__).with_name('label_agentic_os_difficulty.py')
    spec = importlib.util.spec_from_file_location('benchmark_difficulty', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def propose_difficulty(question: str) -> dict[str, Any]:
    """Question-only provisional rubric, not graph-hop inference or official labels."""
    q = question.casefold()
    patterns = {
        'lookup': r'^(who|when|where|define|what is|what are|name|list)\b|누구|언제|어디|정의',
        'aggregation': r'\b(count|how many|total|average|sum)\b|몇 개|몇 건|총액|평균|합계',
        'comparison': r'\b(compare|difference|increase|decrease|ratio|percentage|growth)\b|비교|차이|증감|비율|증가율',
        'constraint': r'\b(before|after|between|excluding|except|at least|at most)\b|이전|이후|제외|이상|이하',
        'extremum': r'\b(highest|lowest|largest|smallest|most|least|top \d+)\b|최대|최소|가장|상위',
        'synthesis': r'\b(why|explain|justify|evaluate|synthesize|recommend)\b|왜|설명|평가|종합|추천',
        'multi_source': r'\b(across|multiple documents|both documents|different sources)\b|여러 문서|두 문서|교차',
    }
    signals = {name: bool(re.search(pattern, q)) for name, pattern in patterns.items()}
    score = (int(signals['aggregation']) + int(signals['comparison'])
             + int(signals['constraint']) + 2 * int(signals['extremum'])
             + 2 * int(signals['synthesis']) + int(signals['multi_source']))
    # No detected cue is unknown, never easy by default. Broad lookup cues need review too.
    level = ('hard' if score >= 3 else 'medium' if score else 'easy') if any(signals.values()) else 'unlabeled'
    return {'stratum': level, 'score': score if any(signals.values()) else None,
            'signals': signals, 'review_status': 'needs_review',
            'rubric': 'question-operations-v1', 'uses_gold_or_trace': False}


def apply_difficulty_labels(rows: list[TaskCase], *, infer: bool, labels: Path | None) -> None:
    """Fill only unlabeled cases; explicit reviewed labels take precedence over proposals."""
    annotations = json.loads(labels.read_text(encoding='utf-8')) if labels else {}
    if not isinstance(annotations, dict):
        raise ValueError('labels must be an object keyed by case_id')
    known = {row.case_id for row in rows}
    if set(annotations) - known:
        raise ValueError('labels contain unknown case IDs')
    for row in rows:
        if row.case_id in annotations:
            note = annotations[row.case_id]
            if (not isinstance(note, dict) or note.get('difficulty') not in ('easy', 'medium', 'hard')
                    or not isinstance(note.get('reason'), str) or not note['reason'].strip()
                    or not isinstance(note.get('reviewer'), str) or not note['reviewer'].strip()):
                raise ValueError(f'label needs difficulty, reason and reviewer: {row.case_id}')
            if row.difficulty != 'unlabeled':
                raise ValueError(f'refusing to overwrite existing label: {row.case_id}')
            row.difficulty = note['difficulty']
            row.difficulty_source = 'local-reviewed-v1'
            # Free-text justification stays in the separate local annotation file.
            row.metadata['difficulty_annotation'] = {'review_status': 'reviewed', 'rubric': 'question-operations-v1'}
        elif infer and row.difficulty == 'unlabeled':
            proposal = propose_difficulty(row.payload.question)
            row.difficulty = proposal['stratum']
            row.difficulty_source = 'local-question-heuristic-v1'
            row.metadata['difficulty_annotation'] = proposal


@dataclass
class TaskCase:
    task: str
    case_id: str
    difficulty: str
    difficulty_source: str
    metadata: dict[str, Any]
    payload: Any  # Evaluator-only original case; may contain gold. Never send wholesale to an LLM.

    def reference(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in
                ('task', 'case_id', 'difficulty', 'difficulty_source', 'metadata')}


def load_tasks(task: str, source: str | Path | None = None, *,
               difficulty: str | None = None, limit_per_difficulty: int | None = None,
               seed: int = 0, infer_unlabeled: bool = False,
               labels: str | Path | None = None) -> list[TaskCase]:
    """Load local snapshots only. Filter first, then deterministically sample each stratum.

    GTSQA payloads are row locators: use load_gtsqa_cases(source, case_ids=...)
    after selection to avoid materializing the full graph corpus. Other payloads
    are existing case objects, except FinBench's original question dictionaries.
    """
    if task not in TASKS:
        raise ValueError(f'unknown task: {task}')
    if difficulty is not None and difficulty not in LEVELS:
        raise ValueError(f'unknown difficulty: {difficulty}')
    if limit_per_difficulty is not None and limit_per_difficulty < 1:
        raise ValueError('limit_per_difficulty must be positive')
    if source is None and task != 'finbench':
        raise ValueError(f'{task} requires a local source')
    path = Path(source) if source is not None else Path(__file__).parents[1] / 'finbench/agent_interaction.py'
    rows: list[TaskCase] = []
    if task == 'gtsqa':
        import pyarrow.parquet as pq
        classifier = _difficulty_module()
        columns = ['id', 'n_hops', 'seed_entities', 'sparql_query', 'all_answers_wikidata', 'graph_isomorphism', 'test_type']
        for batch in pq.ParquetFile(path).iter_batches(columns=columns):
            for row in batch.to_pylist():
                tag = classifier.label_gtsqa_row(row)
                rows.append(TaskCase(task, str(row['id']), tag['stratum'], 'repository-structural-v1',
                                     tag, {'source': str(path), 'case_id': int(row['id'])}))
    elif task == 'grbench':
        from seocho.eval.grbench import load_grbench_questions
        classifier = _difficulty_module()
        for case in load_grbench_questions(path):
            tag = classifier.label_grbench_question(case.qid, case.question)
            rows.append(TaskCase(task, case.qid, tag['stratum'] or 'unlabeled',
                                 'repository-template-v1', tag, case))
    elif task == 'finbench':
        # Read the existing literal question set without importing DB/agent runtimes.
        tree = ast.parse(path.read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, ast.AnnAssign)
                 and isinstance(node.target, ast.Name) and node.target.id == 'QUESTIONS']
        if len(nodes) != 1:
            raise ValueError('expected one literal QUESTIONS definition')
        for case in ast.literal_eval(nodes[0].value):
            rows.append(TaskCase(task, case['id'], case['difficulty'], 'repository-question-label',
                                 {'audience': case['audience'], 'requires_graph_binding': True}, case))
    elif task in ('finder', 'finance'):
        from seocho.benchmarking import load_finder_cases, load_finance_cases, classify_finder_scenario
        cases = load_finder_cases(path) if task == 'finder' else load_finance_cases(path)
        for case in cases:
            level = classify_finder_scenario(case) if task == 'finder' else 'unlabeled'
            rows.append(TaskCase(task, case.case_id, level,
                                 'repository-scenario' if task == 'finder' else 'unavailable',
                                 {'category': case.category}, case))
    else:
        from seocho.eval.graphrag_bench import load_question_directory
        cases, _ = load_question_directory(path)
        for case in cases:
            rows.append(TaskCase(task, case.case_id, 'unlabeled', 'unavailable',
                                 case.to_reference_dict(), case))
    seen: set[str] = set()
    for case in rows:
        if not case.case_id or case.case_id in seen or case.difficulty not in LEVELS:
            raise ValueError(f'invalid/duplicate case or difficulty: {case.case_id}')
        seen.add(case.case_id)
    apply_difficulty_labels(rows, infer=infer_unlabeled, labels=Path(labels) if labels else None)
    selected = []
    for level in LEVELS:
        if difficulty is not None and level != difficulty:
            continue
        group = sorted((row for row in rows if row.difficulty == level), key=lambda row: row.case_id)
        if limit_per_difficulty is not None:
            random.Random(f'{seed}:{task}:{level}').shuffle(group)
            group = group[:limit_per_difficulty]
        selected.extend(group)
    if not selected:
        available = dict(Counter(row.difficulty for row in rows))
        raise ValueError(f'empty selection for {task}/{difficulty}; available={available}')
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=TASKS, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--difficulty', choices=LEVELS)
    parser.add_argument('--limit-per-difficulty', type=int)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--infer-unlabeled', action='store_true', help='Propose question-only labels; needs review')
    parser.add_argument('--labels', type=Path, help='Local reviewed annotations keyed by case_id')
    args = parser.parse_args()
    rows = load_tasks(args.task, args.source, difficulty=args.difficulty,
                      limit_per_difficulty=args.limit_per_difficulty, seed=args.seed,
                      infer_unlabeled=args.infer_unlabeled, labels=args.labels)
    source = args.source or Path(__file__).parents[1] / 'finbench/agent_interaction.py'
    paths = sorted(source.glob('*.jsonl')) if source.is_dir() else [source]
    paths += [Path(__file__), Path(__file__).with_name('label_agentic_os_difficulty.py')]
    if args.labels:
        paths.append(args.labels)
    hashes = {}
    for path in paths:
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(block)
        hashes[str(path)] = digest.hexdigest()
    print(json.dumps({'task': args.task, 'seed': args.seed, 'source_sha256': hashes,
                      'counts': dict(Counter(row.difficulty for row in rows)),
                      'cases': [row.reference() for row in rows]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
