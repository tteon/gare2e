"""Versioned eight-tool business registry. Actual arguments are bound; dataset scope is trusted context."""
import copy
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'financial-tools-v1'

def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()

def integer(lo, hi, description):
    return {'type': 'integer', 'minimum': lo, 'maximum': hi, 'description': description}

ACCOUNT = integer(0, 2**63-1, 'Account identifier from the user request.')
AMOUNT = {'type': 'number', 'minimum': 0, 'maximum': 1e15, 'description': 'Inclusive minimum transfer amount in stored dataset units.'}
LIMIT = integer(1, 100, 'Maximum rows to return, after the documented ordering.')
START = integer(0, 2**63-1, 'Inclusive Unix timestamp in seconds.')
END = integer(1, 2**63-1, 'Exclusive Unix timestamp in seconds; must exceed start_ts.')
SPECS = {
 'list_transfer_recipients': ('L1', 'Find distinct direct outgoing transfer recipients of account with amount >= min_amount. Order by recipient account; return account.', dict(account=ACCOUNT,min_amount=AMOUNT,limit=LIMIT)),
 'trace_transfer_reachability': ('L2', 'Find outgoing transfer reachable accounts within max_hops (1 to 3), exclude starting account. Return account and minimum min_hops; order by account.', dict(account=ACCOUNT,max_hops=integer(1,3,'Maximum transfer hop count.'),limit=LIMIT)),
 'trace_time_ordered_fund_paths': ('L3', 'Find simple paths of exactly three outgoing transfers from account. Every edge meets min_amount and [start_ts,end_ts); timestamps must be nondecreasing. Return accounts arrays ordered lexicographically.', dict(account=ACCOUNT,min_amount=AMOUNT,start_ts=START,end_ts=END,limit=LIMIT)),
 'find_fan_in_accounts': ('L4', 'Find recipients with at least min_senders DISTINCT senders during [start_ts,end_ts). Exclude self transfers. Return account, senders and total_amount; order by account.', dict(min_senders=integer(1,10000000,'Minimum distinct senders.'),start_ts=START,end_ts=END,limit=LIMIT)),
 'find_linked_account_pairs': ('L5', 'Find relationship-linked account pairs: a<b, transfer a to b, DISTINCT owners p and q with guarantee p to q, and a shared sign-in medium. Restrict lower account a to inclusive [account_min,account_max]. Return account_a,account_b ordered by these keys.', dict(account_min=ACCOUNT,account_max=ACCOUNT,limit=LIMIT)),
 'rank_linked_account_flows': ('L6', 'Rank relationship-linked pairs (same relationship rules as find_linked_account_pairs) by BIDIRECTIONAL transfer sum, excluding either account with loan repayments. Restrict lower account a to inclusive [account_min,account_max]. Return account_a,account_b,total_amount; descending total then ascending IDs, top_k rows.', dict(account_min=ACCOUNT,account_max=ACCOUNT,top_k=LIMIT)),
 'trace_rapid_pass_through': ('P1', 'Find simple two-transfer paths from account through an intermediary to a different destination. Both amounts >= min_amount and timestamps in [start_ts,end_ts). Second timestamp minus first must be between 0 and max_gap_seconds inclusive. Return DISTINCT source, intermediary, destination ordered by these IDs. Investigation candidates, not proof of wrongdoing.', dict(account=ACCOUNT,min_amount=AMOUNT,start_ts=START,end_ts=END,max_gap_seconds=integer(0,31536000,'Inclusive maximum delay in seconds between transfers.'),limit=LIMIT)),
 'find_shared_access_accounts': ('A1', 'Find other accounts sharing a Medium with account, with both SIGN_IN timestamps in [start_ts,end_ts). Return DISTINCT account, shared_media count of distinct Medium nodes; order by account. Medium is a recorded access medium, not necessarily an IP or device; shared access does not prove common ownership.', dict(account=ACCOUNT,start_ts=START,end_ts=END,limit=LIMIT)),
}

@dataclass(frozen=True)
class Dataset:
    dataset_id: str
    workspace: str
    snapshot_sha256: str
    schema_version: str
    physical_scale: object

    @classmethod
    def load(cls, path, snapshot):
        d=json.loads(Path(path).read_text())
        if d['schema_version'] != 'gug-finbench-logical-v1':
            raise ValueError('unsupported_dataset_schema')
        if sha(Path(snapshot).read_bytes()) != d['snapshot_sha256']:
            raise ValueError('dataset_snapshot_hash_mismatch')
        return cls(**{k:d[k] for k in cls.__dataclass_fields__})

class Registry:
    def __init__(self, dataset, query_dir=None):
        self.dataset=dataset
        self.query_dir=Path(query_dir or ROOT/'queries/financial_tools')

    def schemas(self):
        return [{'type':'function','function':{'name':name,'description':desc,'parameters':{'type':'object','properties':copy.deepcopy(props),'required':list(props),'additionalProperties':False}}} for name,(_,desc,props) in SPECS.items()]

    def bind(self, name, arguments, version='reference-v1'):
        if name not in SPECS: raise ValueError('unknown_tool')
        cid,_,props=SPECS[name]
        if not isinstance(arguments,dict) or set(arguments)!=set(props): raise ValueError('argument_keys')
        for k,spec in props.items():
            v=arguments[k]
            valid=type(v) is int if spec['type']=='integer' else type(v) in (int,float)
            if not valid or not math.isfinite(v) or not spec['minimum']<=v<=spec['maximum']:
                raise ValueError('argument_value:'+k)
        if 'start_ts' in arguments and arguments['start_ts']>=arguments['end_ts']: raise ValueError('time_order')
        if 'account_min' in arguments and arguments['account_min']>arguments['account_max']: raise ValueError('account_range')
        if version not in ['reference-v1','rewrite-v1'] or (version=='rewrite-v1' and cid not in ['L5','L6']): raise ValueError('query_version')
        variant=f'{cid}-hops{arguments["max_hops"]}' if cid=='L2' else cid
        if version=='rewrite-v1':variant+='-rewrite'
        query=(self.query_dir/(variant+'.cypher')).read_text().strip()
        params={**arguments,'workspace':self.dataset.workspace}
        # max_hops selects a finite, reviewed template; it is never concatenated into Cypher.
        params.pop('max_hops',None)
        return {'tool':name,'scenario':cid,'arguments':copy.deepcopy(arguments),'query':query,'parameters':params,'query_version':version,'query_variant':variant,'query_sha256':sha(query),'registry_version':VERSION,'dataset_id':self.dataset.dataset_id,'snapshot_sha256':self.dataset.snapshot_sha256}


def execute(driver, bound):
    """No expected-answer or requested-case arguments: dispatch uses only bound actual call."""
    import time
    from neo4j import Query, READ_ACCESS
    t=time.monotonic()
    with driver.session(database='neo4j',default_access_mode=READ_ACCESS) as session:
        result=session.run(Query(bound['query'],timeout=15),bound['parameters'])
        run_return=time.monotonic()
        rows=[r.data() for r in result]
        summary=result.consume()
    end=time.monotonic()
    return rows,{'start':t,'end':end,'db_ms':1000*(end-t),'run_return_ms':1000*(run_return-t),'consume_ms':1000*(end-run_return),'server_available_ms':summary.result_available_after,'server_consumed_ms':summary.result_consumed_after,'rows':len(rows)}
