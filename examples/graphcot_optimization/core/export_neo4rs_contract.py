"""Export trusted templates/schema only; never requests or expected answers."""
import argparse,json,hashlib
from pathlib import Path
from financial_tools import SPECS
OUTPUTS={
'L1':{'account':'integer'},'L2':{'account':'integer','min_hops':'integer'},
'L3':{'accounts':'integer_array'},'L4':{'account':'integer','senders':'integer','total_amount':'integer'},
'L5':{'account_a':'integer','account_b':'integer'},'L6':{'account_a':'integer','account_b':'integer','total_amount':'integer'},
'P1':{'source':'integer','intermediary':'integer','destination':'integer'},'A1':{'account':'integer','shared_media':'integer'}}
def export(dataset,query_dir):
    tools={}
    for name,(cid,description,props) in SPECS.items():
        variants={str(n):(query_dir/f'L2-hops{n}.cypher').read_text().strip() for n in range(1,4)} if cid=='L2' else {'default':(query_dir/f'{cid}.cypher').read_text().strip()}
        tools[name]={'properties':props,'outputs':OUTPUTS[cid],'queries':variants}
    return {'version':'gug-neo4rs-contract-v1','dataset_id':dataset['dataset_id'],'snapshot_sha256':dataset['snapshot_sha256'],'workspace':dataset['workspace'],'tools':tools}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--queries',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    data=export(json.loads(Path(a.dataset).read_text()),Path(a.queries))
    with open(a.out,'x') as f:json.dump(data,f,indent=2)
    print(hashlib.sha256(Path(a.out).read_bytes()).hexdigest())
