"""Restore an owned empty Neo4j from canonical JSON; never reuse old lifecycle PIDs."""
from __future__ import annotations

import argparse
from collections import defaultdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import urlsplit
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'core'))
from export_neo4rs_contract import export  # noqa: E402

LABELS = {'Entity', 'Person', 'Company', 'Account', 'Loan', 'Medium'}
RELATIONS = {'OWN', 'GUARANTEE', 'INVEST', 'APPLY', 'DEPOSIT', 'REPAY', 'TRANSFER', 'WITHDRAW', 'SIGN_IN'}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, default=str)+'\n')


def items(snapshot: Path, section: str) -> Iterator[dict]:
    import ijson
    with snapshot.open('rb') as stream:
        yield from ijson.items(stream, section+'.item', use_float=True)


def checked_dataset(dataset: Path, snapshot: Path) -> dict:
    meta = read(dataset)
    if meta['schema_version'] != 'gug-finbench-logical-v1' or sha(snapshot) != meta['snapshot_sha256']:
        raise ValueError('snapshot/schema mismatch')
    return meta


def node_valid(node: dict, workspace: str) -> None:
    labels = set(node['labels'])
    if not labels <= LABELS or 'Entity' not in labels or len(labels) != 2:
        raise ValueError('unsupported canonical labels')
    if node['props'].get('id') != node['eid'] or node['props'].get('_workspace_id') != workspace:
        raise ValueError('node identity/scope mismatch')


def edge_valid(edge: dict, workspace: str) -> None:
    if edge['type'] not in RELATIONS or edge['props'].get('_workspace_id') != workspace:
        raise ValueError('unsupported relationship/scope')
    if not isinstance(edge['props'].get('_source_row'), int) or not edge['props'].get('_source_file'):
        raise ValueError('missing source-row identity')


def batches(stream: Iterable[dict], n: int) -> Iterator[list[dict]]:
    current = []
    for value in stream:
        current.append(value)
        if len(current) == n:
            yield current
            current = []
    if current:
        yield current


def bucket(edge: dict) -> dict:
    date = dt.datetime.fromtimestamp(edge['props']['ts'], dt.timezone.utc)
    begin = dt.datetime(date.year, date.month, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(date.year+1, 1, 1, tzinfo=dt.timezone.utc) if date.month == 12 else dt.datetime(date.year, date.month+1, 1, tzinfo=dt.timezone.utc)
    start = int(begin.timestamp())
    return dict(src=edge['src'], dst=edge['dst'], bucket=edge['src']+':'+str(start),
                start=start, end=int(end.timestamp()), props=edge['props'])


def driver() -> Any:
    from neo4j import GraphDatabase
    uri = os.environ['NEO4J_URI']
    if urlsplit(uri).username or urlsplit(uri).password:
        raise ValueError('URI must not contain credentials; use separate environment variables')
    return GraphDatabase.driver(uri, auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']),
                                max_connection_pool_size=4, fetch_size=1000)


def version_and_schema(db: Any, expected: str) -> dict:
    with db.session() as session:
        component = session.run('CALL dbms.components() YIELD name,versions,edition RETURN name,versions,edition').data()
        if not any(expected in c['versions'] for c in component):
            raise ValueError('Neo4j version differs from --expected-version')
        indexes = session.run('SHOW INDEXES YIELD name,type,entityType,labelsOrTypes,properties,state RETURN *').data()
        constraints = session.run('SHOW CONSTRAINTS YIELD name,type,entityType,labelsOrTypes,properties RETURN *').data()
    return dict(components=component, indexes=indexes, constraints=constraints)


def locate(restored: Path) -> dict:
    local = restored / 'local'
    base = local/'results/physical_scale_a/sf1'
    paths = dict(local=local, snapshot=base/'graph-snapshot.json', dataset=base/'dataset.json',
                 frozen32=local/'results/neo4rs_memory_e2e_20261102_a/workloads/frozen32',
                 hard40=local/'results/neo4rs_memory_e2e_20261102_a/workloads/hard40',
                 frozen_queries=local/'handoff/sf1-vllm-v1/queries',
                 model_manifest=local/'results/neo4rs_memory_e2e_20261102_a/checkpoint-restoration-manifest.json',
                 model_owner=local/'results/neo4rs_memory_e2e_20261102_a/checkpoint-owner.json')
    for path in paths.values():
        if not path.exists():
            raise ValueError('required restored artifact absent: '+str(path))
    meta = checked_dataset(paths['dataset'], paths['snapshot'])
    return dict(paths={k:str(v.resolve()) for k,v in paths.items()}, dataset_id=meta['dataset_id'],
                workspace=meta['workspace'], snapshot_sha256=meta['snapshot_sha256'],
                canonical_nodes=meta['nodes'], canonical_edges=meta['edges'])


def import_graph(args: argparse.Namespace) -> dict:
    meta = checked_dataset(args.dataset, args.snapshot)
    out = args.output
    out.mkdir(parents=True, exist_ok=False)
    result = dict(stage='started', snapshot_sha256=meta['snapshot_sha256'], nodes=0, edges=0)
    save(out/'status.json', result)
    try:
        with driver() as db:
            db.verify_connectivity()
            save(out/'server-before.json', version_and_schema(db, args.expected_version))
            with db.session() as session:
                if session.run('MATCH(n) RETURN count(n) AS n').single()['n']:
                    raise ValueError('target database is not empty; refusing import/resume/overwrite')
                session.run('CREATE CONSTRAINT entity_id IF NOT EXISTS FOR(n:Entity) REQUIRE n.id IS UNIQUE').consume()
                for batch in batches(items(args.snapshot, 'nodes'), args.batch):
                    groups = defaultdict(list)
                    for node in batch:
                        node_valid(node, meta['workspace'])
                        groups[tuple(sorted(node['labels']))].append(node['props'])
                    for labels, rows in groups.items():
                        query = 'UNWIND $rows AS row CREATE(n:'+':'.join(labels)+') SET n=row RETURN count(n) AS n'
                        count = session.run(query, rows=rows).single()['n']
                        if count != len(rows):
                            raise ValueError('node import count mismatch')
                        result['nodes'] += count
                for batch in batches(items(args.snapshot, 'edges'), args.batch):
                    groups = defaultdict(list)
                    for edge in batch:
                        edge_valid(edge, meta['workspace'])
                        groups[edge['type']].append(edge)
                    for kind, rows in groups.items():
                        query = ('UNWIND $rows AS row MATCH(a:Entity {id:row.src}),(b:Entity {id:row.dst}) '
                                 'CREATE(a)-[r:'+kind+']->(b) SET r=row.props RETURN count(r) AS n')
                        count = session.run(query, rows=rows).single()['n']
                        if count != len(rows):
                            raise ValueError('edge endpoint/count mismatch; partial import retained')
                        result['edges'] += count
                    save(out/'status.json', result)
                session.run('CREATE INDEX account_anchor IF NOT EXISTS FOR(n:Account) ON(n._workspace_id,n.acct_no)').consume()
                session.run('CALL db.awaitIndexes(300)').consume()
                if result['nodes'] != meta['nodes'] or result['edges'] != meta['edges']:
                    raise ValueError('canonical counts differ from manifest')
                if args.sidecar:
                    session.run('CREATE CONSTRAINT bucket_id IF NOT EXISTS FOR(b:TransferBucket) REQUIRE b.id IS UNIQUE').consume()
                    for batch in batches((e for e in items(args.snapshot, 'edges') if e['type']=='TRANSFER'), args.batch):
                        rows = [bucket(edge) for edge in batch]
                        query = ('UNWIND $rows AS row MATCH(a:Entity {id:row.src}),(b:Entity {id:row.dst}) '
                                 'MERGE(bucket:TransferBucket {id:row.bucket}) '
                                 'SET bucket.start_ts=row.start,bucket.end_ts=row.end,bucket._workspace_id=$w,bucket._projection_owner=$owner '
                                 'MERGE(a)-[:HAS_TRANSFER_BUCKET]->(bucket) '
                                 'CREATE(bucket)-[r:BUCKET_TRANSFER]->(b) SET r=row.props RETURN count(r) AS n')
                        if session.run(query,rows=rows,w=meta['workspace'],owner=str(out.resolve())).single()['n'] != len(rows):
                            raise ValueError('sidecar import count mismatch')
                    session.run('CALL db.awaitIndexes(300)').consume()
            save(out/'server-after.json', version_and_schema(db, args.expected_version))
        result.update(stage='complete', sidecar=args.sidecar,
                      note='Logical reimport; original physical layout/cache/transaction history not reproduced.')
    except BaseException as exc:
        result.update(stage='failed', error_type=type(exc).__name__)
        save(out/'status.json', result)
        raise
    save(out/'status.json', result)
    return result


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def audit(args: argparse.Namespace) -> dict:
    """Compare every canonical property/parallel edge, and optional sidecar, via disk-backed keys."""
    from neo4j import Query
    meta = checked_dataset(args.dataset, args.snapshot)
    args.output.mkdir(parents=True, exist_ok=False)
    store = sqlite3.connect(args.output/'expected.sqlite3')
    store.execute('CREATE TABLE expected(kind TEXT,k TEXT,h TEXT,seen INTEGER DEFAULT 0,PRIMARY KEY(kind,k))')

    def expect(kind: str, key: str, value: object, dedup: bool = False) -> None:
        verb = 'INSERT OR IGNORE' if dedup else 'INSERT'
        store.execute(verb+' INTO expected(kind,k,h) VALUES(?,?,?)', (kind,key,canonical_hash(value)))

    def match(kind: str, key: str, value: object) -> None:
        changed = store.execute('UPDATE expected SET seen=1 WHERE kind=? AND k=? AND h=? AND seen=0',
                                (kind,key,canonical_hash(value))).rowcount
        if changed != 1:
            raise ValueError('unknown/duplicate/property-mismatched '+kind+' row')

    try:
        for node in items(args.snapshot, 'nodes'):
            node_valid(node, meta['workspace'])
            expect('node',node['eid'],dict(labels=sorted(node['labels']),props=node['props']))
        for edge in items(args.snapshot, 'edges'):
            edge_valid(edge, meta['workspace'])
            key = json.dumps([edge['props']['_source_file'],edge['props']['_source_row']])
            expect('edge',key,edge)
            if args.sidecar and edge['type'] == 'TRANSFER':
                b = bucket(edge)
                expect('bucket',b['bucket'],dict(id=b['bucket'],start_ts=b['start'],end_ts=b['end'],_workspace_id=meta['workspace']),True)
                expect('has',json.dumps([b['src'],b['bucket']]),dict(src=b['src'],dst=b['bucket'],props={}),True)
                expect('bucket_edge',key,dict(src=b['bucket'],dst=b['dst'],props=b['props']))
        store.commit()
        counts = dict(store.execute('SELECT kind,count(*) FROM expected GROUP BY kind'))
        if counts.get('node') != meta['nodes'] or counts.get('edge') != meta['edges']:
            raise ValueError('expected source counts do not match manifest')
        with driver() as db:
            schema = version_and_schema(db,args.expected_version)
            with db.session() as session:
                for row in session.run(Query('MATCH(n:Entity) RETURN n.id AS id,labels(n) AS labels,properties(n) AS props',timeout=args.audit_timeout)):
                    match('node',row['id'],dict(labels=sorted(row['labels']),props=row['props']))
                for row in session.run(Query('MATCH(a:Entity)-[r]->(b:Entity) RETURN a.id AS src,b.id AS dst,type(r) AS type,properties(r) AS props',timeout=args.audit_timeout)):
                    data = row.data()
                    key = json.dumps([data['props']['_source_file'],data['props']['_source_row']])
                    match('edge',key,data)
                if args.sidecar:
                    for row in session.run(Query('MATCH(b:TransferBucket) RETURN b.id AS id,properties(b) AS props',timeout=args.audit_timeout)):
                        props = row['props']
                        props.pop('_projection_owner',None)
                        match('bucket',row['id'],props)
                    for row in session.run(Query('MATCH(a:Entity)-[r:HAS_TRANSFER_BUCKET]->(b:TransferBucket) RETURN a.id AS src,b.id AS dst,properties(r) AS props',timeout=args.audit_timeout)):
                        match('has',json.dumps([row['src'],row['dst']]),row.data())
                    for row in session.run(Query('MATCH(a:TransferBucket)-[r:BUCKET_TRANSFER]->(b:Entity) RETURN a.id AS src,b.id AS dst,properties(r) AS props',timeout=args.audit_timeout)):
                        key = json.dumps([row['props']['_source_file'],row['props']['_source_row']])
                        match('bucket_edge',key,row.data())
                total_nodes = session.run('MATCH(n) RETURN count(n) AS n').single()['n']
                total_edges = session.run('MATCH()-[r]->() RETURN count(r) AS n').single()['n']
                if total_nodes != counts['node']+counts.get('bucket',0) or total_edges != counts['edge']+counts.get('has',0)+counts.get('bucket_edge',0):
                    raise ValueError('unexpected graph content outside expected scope')
        if store.execute('SELECT count(*) FROM expected WHERE seen=0').fetchone()[0]:
            raise ValueError('expected records missing')
        required = {'entity_id':(['Entity'],['id']), 'account_anchor':(['Account'],['_workspace_id','acct_no'])}
        if args.sidecar:
            required['bucket_id'] = (['TransferBucket'],['id'])
        indexes = {i['name']:i for i in schema['indexes'] if i['type']!='LOOKUP'}
        if set(indexes) != set(required):
            raise ValueError('missing or unexpected indexes; baseline differs')
        for name,(labels,properties) in required.items():
            value = indexes[name]
            if (value['state'],value['type'],value['entityType'],value['labelsOrTypes'],value['properties']) != ('ONLINE','RANGE','NODE',labels,properties):
                raise ValueError('index definition differs from baseline')
        constraints = {c['name']:c for c in schema['constraints']}
        if set(constraints) != ({'entity_id','bucket_id'} if args.sidecar else {'entity_id'}):
            raise ValueError('constraint set differs from baseline')
        for name,value in constraints.items():
            if value['type']!='UNIQUENESS' or value['properties']!=['id'] or value['labelsOrTypes']!=required[name][0]:
                raise ValueError('constraint definition differs from baseline')
        lookups = [i for i in schema['indexes'] if i['type']=='LOOKUP' and i['state']=='ONLINE']
        if sorted(i['entityType'] for i in lookups) != ['NODE','RELATIONSHIP']:
            raise ValueError('token lookup indexes differ from baseline')
        result = dict(validated=True,snapshot_sha256=meta['snapshot_sha256'],counts=counts,
                      total_nodes=total_nodes,total_edges=total_edges,schema=schema,
                      normalization='Only sidecar _projection_owner ignored; canonical labels/properties and parallel multiplicity exact.')
        save(args.output/'audit.json',result)
        return result
    finally:
        store.close()


def stage(restored: Path, output: Path) -> dict:
    paths = locate(restored)['paths']
    source = Path(paths['local'])
    output.mkdir(parents=True,exist_ok=False)
    for name in ['scripts','configs','queries','handoff']:
        shutil.copytree(source/name,output/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    script = output/'scripts/handoff_isolation_eval.py'
    before = script.read_text()
    auth = "auth=(os.environ['NEO4J_USER'],os.environ['NEO4J_PASSWORD'])"
    replacements = [("import argparse,asyncio,json,time,uuid,hashlib,statistics,logging,random", "import argparse,asyncio,json,time,uuid,hashlib,statistics,logging,random,os"),
                    ("auth=None,max_connection_pool_size=1",auth+",max_connection_pool_size=1"),
                    ("base_url='http://127.0.0.1:18000'","base_url=os.environ.get('GUG_LLM_BASE_URL','http://127.0.0.1:18000')")]
    after = before
    for old,new in replacements:
        if after.count(old) != 1:
            raise ValueError('archived evaluator source differs; refusing blind patch')
        after = after.replace(old,new)
    script.write_text(after)
    shutil.copytree(Path(paths['frozen32']),output/'workloads/frozen32')
    shutil.copytree(Path(paths['hard40']),output/'workloads/hard40')
    for workload in ['frozen32','hard40']:
        records = read(output/f'workloads/{workload}/requests.json')
        save(output/f'workloads/{workload}/db-cases.json',[{k:r[k] for k in ['id','tool','arguments']} for r in records])
    meta = read(Path(paths['dataset']))
    save(output/'contract.json',export(meta,output/'handoff/sf1-vllm-v1/queries'))
    result = dict(ready=True,original_evaluator_sha256=hashlib.sha256(before.encode()).hexdigest(),
                  staged_evaluator_sha256=sha(script),snapshot=paths['snapshot'],
                  changes=['Authenticated canary via NEO4J_USER/PASSWORD','LLM base URL via GUG_LLM_BASE_URL','os import'],
                  note='Independent copied stage, no restored original rewritten. Not a full portable controller/ownership guard.')
    save(output/'staging.json',result)
    return result


def check_model(model: Path, manifest: Path) -> dict:
    records = read(manifest)
    for name,record in records.items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('unsafe model filename')
        path = model/name
        if not path.is_file() or path.stat().st_size != record['bytes'] or sha(path) != record['sha256']:
            raise ValueError('model file differs from pinned manifest: '+name)
    return dict(verified=True,files=len(records),weight_shards=sum(n.endswith('.safetensors') for n in records))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    q = sub.add_parser('locate');q.add_argument('--restored',required=True,type=Path)
    q = sub.add_parser('stage');q.add_argument('--restored',required=True,type=Path);q.add_argument('--output',required=True,type=Path)
    q = sub.add_parser('check-model');q.add_argument('--model',required=True,type=Path);q.add_argument('--manifest',required=True,type=Path)
    for name in ['import','audit']:
        q = sub.add_parser(name)
        q.add_argument('--snapshot',required=True,type=Path);q.add_argument('--dataset',required=True,type=Path)
        q.add_argument('--output',required=True,type=Path);q.add_argument('--sidecar',action='store_true')
        q.add_argument('--expected-version',default='5.26.0')
        q.add_argument('--batch',type=int,default=500);q.add_argument('--audit-timeout',type=int,default=900)
    a = p.parse_args()
    if a.command=='locate':
        result=locate(a.restored)
    elif a.command=='stage':
        result=stage(a.restored,a.output)
    elif a.command=='check-model':
        result=check_model(a.model,a.manifest)
    elif a.command=='import':
        if a.batch<1:p.error('--batch must be positive')
        result=import_graph(a)
    else:
        result=audit(a)
    print(json.dumps(result,indent=2,default=str))


if __name__=='__main__':
    main()
