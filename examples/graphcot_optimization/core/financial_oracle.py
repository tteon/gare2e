"""Independent traversal oracle; keeps archived six-tool oracle unchanged."""
from collections import defaultdict
from parameter_oracle import Oracle as BaseOracle, content_hash
from financial_tools import SPECS
class Oracle(BaseOracle):
    def __init__(self,snapshot,workspace):
        super().__init__(snapshot,workspace)
        self.signins=defaultdict(list)
        for e in snapshot['edges']:
            if e['type']=='SIGN_IN' and e['src'] in self.nodes and e['dst'] in self.nodes:
                if 'Medium' in self.nodes[e['src']]['labels']:
                    self.signins[e['src']].append((e['dst'],e['props']['ts']))
    def answer(self,name,p):
        cid=SPECS[name][0]
        if cid.startswith('L'):return super().answer('query_'+cid.lower(),p)
        a=self.accounts.get(p['account'])
        if a is None:return []
        if cid=='P1':
            paths=set()
            for b,r in self.adj[a]:
                if b==a or not (r['amount']>=p['min_amount'] and p['start_ts']<=r['ts']<p['end_ts']):continue
                for c,t in self.adj[b]:
                    if c in (a,b) or 'Account' not in self.nodes[c]['labels']:continue
                    if t['amount']>=p['min_amount'] and p['start_ts']<=t['ts']<p['end_ts'] and 0<=t['ts']-r['ts']<=p['max_gap_seconds']:
                        paths.add((p['account'],self.props[b]['acct_no'],self.props[c]['acct_no']))
            return [dict(source=x,intermediary=y,destination=z) for x,y,z in sorted(paths)][:p['limit']]
        shared=defaultdict(set)
        for m,entries in self.signins.items():
            eligible={b for b,ts in entries if p['start_ts']<=ts<p['end_ts']}
            if a in eligible:
                for b in eligible-{a}:
                    if 'Account' in self.nodes[b]['labels']:shared[self.props[b]['acct_no']].add(m)
        return [dict(account=b,shared_media=len(media)) for b,media in sorted(shared.items())][:p['limit']]
