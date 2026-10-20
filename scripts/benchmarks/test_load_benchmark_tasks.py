"""Offline adapter contract checks; no model/database/network calls."""
import json
from pathlib import Path
import tempfile

from load_benchmark_tasks import load_tasks, propose_difficulty


def test_loaders() -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    assert propose_difficulty('What is revenue?')['stratum'] == 'easy'
    assert propose_difficulty('Compare revenue between years')['stratum'] == 'medium'
    assert propose_difficulty('Explain the difference across multiple documents')['stratum'] == 'hard'
    assert propose_difficulty('Unspecified task')['stratum'] == 'unlabeled'
    assert propose_difficulty('매출 증가율을 비교하고 그 이유를 설명하세요')['stratum'] == 'hard'
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        graph = root / 'gtsqa.parquet'
        pq.write_table(pa.Table.from_pylist([
            dict(id=i, n_hops=h, seed_entities=['Q1'], sparql_query='wdt:P1',
                 all_answers_wikidata=['SECRET_GOLD'], graph_isomorphism='path', test_type=[])
            for i, h in [(1, 1), (2, 2), (3, 4)]]), graph)
        assert [x.difficulty for x in load_tasks('gtsqa', graph)] == ['easy', 'medium', 'hard']
        assert load_tasks('gtsqa', graph, difficulty='hard')[0].case_id == '3'
        assert 'SECRET_GOLD' not in json.dumps([x.reference() for x in load_tasks('gtsqa', graph)])
        gr = root / 'gr.jsonl'
        gr.write_text('\n'.join(json.dumps(x) for x in [
            dict(qid='a', question='Who wrote this paper?', answer='SECRET_GOLD'),
            dict(qid='b', question='Find its closest collaborator', answer='SECRET_GOLD'),
            dict(qid='c', question='Unrecognized task', answer='SECRET_GOLD')]))
        assert {x.difficulty for x in load_tasks('grbench', gr)} == {'easy', 'hard', 'unlabeled'}
        finance = root / 'finance.json'
        finance.write_text(json.dumps([
            dict(id='a', text='document', question='question', expected_answer='SECRET_GOLD', category='Company Overview'),
            dict(id='b', text='document', question='question', expected_answer='SECRET_GOLD', category='Legal')]))
        assert {x.difficulty for x in load_tasks('finder', finance)} == {'beginner', 'advanced'}
        assert {x.difficulty for x in load_tasks('finance', finance)} == {'unlabeled'}
        questions = root / 'questions'
        questions.mkdir()
        for kind in ('FB', 'MC', 'MS', 'OE', 'TF'):
            (questions / f'{kind}.jsonl').write_text(json.dumps({
                'Question': 'synthetic question', 'Answer': 'SECRET_GOLD',
                'Level-1 Topic': 'topic', 'Level-2 Topic': 'subtopic'})+'\n')
        result = load_tasks('graphrag-bench', questions)
        assert len(result) == 5 and all(x.difficulty == 'unlabeled' for x in result)
        assert 'SECRET_GOLD' not in json.dumps([x.reference() for x in result])
        a = load_tasks('finbench', limit_per_difficulty=2, seed=42)
        b = load_tasks('finbench', limit_per_difficulty=2, seed=42)
        assert [x.case_id for x in a] == [x.case_id for x in b]
        assert len(a) == 6
        for kwargs in [dict(difficulty='bogus'), dict(limit_per_difficulty=0), dict(difficulty='beginner')]:
            try:
                load_tasks('finbench', **kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError('invalid/empty selection must fail')
        finance.write_text(json.dumps([dict(id='x', text='SECRET_GOLD', question='What is revenue?', expected_answer='SECRET_GOLD')]))
        assert load_tasks('finance', finance)[0].difficulty == 'unlabeled'
        inferred = load_tasks('finance', finance, infer_unlabeled=True)[0]
        assert inferred.difficulty == 'easy'
        assert inferred.metadata['difficulty_annotation']['review_status'] == 'needs_review'
        assert 'SECRET_GOLD' not in json.dumps(inferred.reference())
        labels = root / 'labels.json'
        labels.write_text(json.dumps({'x': {'difficulty': 'hard', 'reason': 'Multiple sources required', 'reviewer': 'test'}}))
        assert load_tasks('finance', finance, infer_unlabeled=True, labels=labels)[0].difficulty == 'hard'
        labels.write_text(json.dumps({'unknown': {'difficulty': 'easy'}}))
        try:
            load_tasks('finance', finance, labels=labels)
        except ValueError:
            pass
        else:
            raise AssertionError('unknown annotation IDs must fail')
        finance.write_text(json.dumps([dict(id='x', text='d', question='q')]*2))
        try:
            load_tasks('finance', finance)
        except ValueError:
            pass
        else:
            raise AssertionError('duplicate IDs must fail')


if __name__ == '__main__':
    test_loaders()
    print('six loader contracts passed')
