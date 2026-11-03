"""Recovery safety contracts; these are not live throughput measurements."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('graphcot_recovery', ROOT/'examples/graphcot_optimization/recovery.py')
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def test_streaming_preserves_large_ids_and_month_boundary(tmp_path: Path) -> None:
    value = 4768749330309850952
    snapshot = tmp_path/'snapshot.json'
    snapshot.write_text(json.dumps({'nodes':[{'props':{'acct_no':value}}]}))
    assert next(recovery.items(snapshot,'nodes'))['props']['acct_no'] == value
    b = recovery.bucket({'src':'Account:1','dst':'Account:2','props':{'ts':1735689600.0}})
    assert b['start'] == 1735689600
    assert b['end'] == 1738368000


def test_hash_and_scope_reject_corruption(tmp_path: Path) -> None:
    snapshot = tmp_path/'snapshot.json'; snapshot.write_text('{}')
    dataset = tmp_path/'dataset.json'
    dataset.write_text(json.dumps({'schema_version':'gug-finbench-logical-v1','snapshot_sha256':'bad'}))
    with pytest.raises(ValueError, match='mismatch'):
        recovery.checked_dataset(dataset,snapshot)
    with pytest.raises(ValueError, match='scope'):
        recovery.node_valid({'eid':'1','labels':['Entity','Account'],'props':{'id':'1','_workspace_id':'foreign'}},'owned')


def test_model_path_and_bytes_are_verified(tmp_path: Path) -> None:
    model = tmp_path/'model'; model.mkdir(); (model/'config.json').write_text('{}')
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'config.json':{'bytes':2,'sha256':recovery.sha(model/'config.json')}}))
    assert recovery.check_model(model,manifest)['verified']
    (model/'config.json').write_text('bad')
    with pytest.raises(ValueError, match='differs'):
        recovery.check_model(model,manifest)
    manifest.write_text(json.dumps({'../escape':{}}))
    with pytest.raises(ValueError, match='unsafe'):
        recovery.check_model(model,manifest)


def test_stage_copies_before_patching_and_exports_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path/'local'
    for directory in ['scripts','configs','queries','handoff/sf1-vllm-v1/queries']:
        (source/directory).mkdir(parents=True)
    original = "import argparse,asyncio,json,time,uuid,hashlib,statistics,logging,random\nauth=None,max_connection_pool_size=1\nbase_url='http://127.0.0.1:18000'\n"
    script = source/'scripts/handoff_isolation_eval.py'; script.write_text(original)
    paths = {'local':str(source),'snapshot':'snapshot','dataset':str(source/'dataset.json')}
    (source/'dataset.json').write_text('{}')
    for name in ['frozen32','hard40']:
        directory = tmp_path/name; directory.mkdir()
        (directory/'requests.json').write_text('[{"id":"x","tool":"tool","arguments":{},"prompt":"private"}]')
        paths[name] = str(directory)
    monkeypatch.setattr(recovery,'locate',lambda _: {'paths':paths})
    monkeypatch.setattr(recovery,'export',lambda *_: {'contract':'fixture'})
    out = tmp_path/'stage'
    assert recovery.stage(tmp_path,out)['ready']
    assert script.read_text() == original
    assert "os.environ['NEO4J_PASSWORD']" in (out/'scripts/handoff_isolation_eval.py').read_text()
    assert 'prompt' not in json.loads((out/'workloads/hard40/db-cases.json').read_text())[0]
    with pytest.raises(FileExistsError):
        recovery.stage(tmp_path,out)
