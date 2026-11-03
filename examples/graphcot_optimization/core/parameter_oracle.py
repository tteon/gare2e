"""Independent graph traversal oracle for the six tool contracts; no Cypher execution."""
import collections
import json
from financial_tools import sha

def content_hash(snapshot):
    nodes={n['eid']:{'labels':sorted(n['labels']),'props':n['props']} for n in snapshot['nodes']}
    records=[{'node':n} for n in nodes.values()]
    records += [{'src':nodes[e['src']],'dst':nodes[e['dst']],'type':e['type'],'props':e['props']} for e in snapshot['edges']]
    return sha('\n'.join(sorted(json.dumps(r,sort_keys=True) for r in records)))

class Oracle:
    def __init__(self,snapshot,workspace):
        self.nodes={n['eid']:n for n in snapshot['nodes'] if n['props'].get('_workspace_id')==workspace}
        self.props={k:n['props'] for k,n in self.nodes.items()}
        self.accounts={n['props']['acct_no']:k for k,n in self.nodes.items() if 'Account' in n['labels']}
        self.adj=collections.defaultdict(list);self.rels=collections.defaultdict(set)
        for e in snapshot['edges']:
            a,b=e['src'],e['dst']
            if a not in self.nodes or b not in self.nodes:continue
            self.rels[e['type']].add((a,b))
            if e['type']=='TRANSFER':self.adj[a].append((b,e['props']))
        owners=collections.defaultdict(set);media=collections.defaultdict(set)
        for a,b in self.rels['OWN']:owners[b].add(a)
        for a,b in self.rels['SIGN_IN']:
            if 'Medium' in self.nodes[a]['labels']:media[b].add(a)
        self.pairs=[]
        for a,b in self.rels['TRANSFER']:
            if 'Account' not in self.nodes[a]['labels'] or 'Account' not in self.nodes[b]['labels']:continue
            if self.props[a]['acct_no']>=self.props[b]['acct_no']:continue
            if media[a]&media[b] and any(p!=q and (p,q) in self.rels['GUARANTEE'] for p in owners[a] for q in owners[b]):self.pairs.append((a,b))
        self.repay={a for a,b in self.rels['REPAY'] if 'Loan' in self.nodes[b]['labels']}

    def answer(self,name,p):
        cid=name[-2:].upper();nd=self.props;adj=self.adj
        if cid in ['L1','L2','L3'] and p['account'] not in self.accounts:return []
        if cid=='L1':
            a=self.accounts[p['account']]
            rows=[{'account':n} for n in sorted({nd[b]['acct_no'] for b,r in adj[a] if r['amount']>=p['min_amount'] and 'Account' in self.nodes[b]['labels']})]
        elif cid=='L2':
            a=self.accounts[p['account']];seen={a:0};front={a}
            for h in range(1,p['max_hops']+1):
                front={b for a in front for b,r in adj[a] if b not in seen};seen.update({b:h for b in front})
            rows=sorted([{'account':nd[b]['acct_no'],'min_hops':h} for b,h in seen.items() if h>0 and 'Account' in self.nodes[b]['labels']],key=lambda r:r['account'])
        elif cid=='L3':
            paths=set()
            def visit(path,last):
                if len(path)==4:
                    if 'Account' in self.nodes[path[-1]]['labels']:paths.add(tuple(nd[n]['acct_no'] for n in path))
                    return
                for b,r in adj[path[-1]]:
                    if b not in path and r['amount']>=p['min_amount'] and p['start_ts']<=r['ts']<p['end_ts'] and r['ts']>=last:visit(path+[b],r['ts'])
            visit([self.accounts[p['account']]],-1);rows=[{'accounts':list(v)} for v in sorted(paths)]
        elif cid=='L4':
            send=collections.defaultdict(set);tot=collections.Counter()
            for a,edges in adj.items():
                for b,r in edges:
                    if a!=b and 'Account' in self.nodes[a]['labels'] and 'Account' in self.nodes[b]['labels'] and p['start_ts']<=r['ts']<p['end_ts']:send[b].add(a);tot[b]+=r['amount']
            rows=sorted([{'account':nd[b]['acct_no'],'senders':len(s),'total_amount':tot[b]} for b,s in send.items() if len(s)>=p['min_senders']],key=lambda r:r['account'])
        else:
            pairs=[(a,b) for a,b in self.pairs if p['account_min']<=nd[a]['acct_no']<=p['account_max']]
            if cid=='L5':rows=sorted([{'account_a':nd[a]['acct_no'],'account_b':nd[b]['acct_no']} for a,b in pairs],key=lambda r:(r['account_a'],r['account_b']))
            else:
                rows=[]
                for a,b in pairs:
                    if a in self.repay or b in self.repay:continue
                    total=sum(r['amount'] for x,r in adj[a] if x==b)+sum(r['amount'] for x,r in adj[b] if x==a)
                    rows.append({'account_a':nd[a]['acct_no'],'account_b':nd[b]['acct_no'],'total_amount':total})
                rows.sort(key=lambda r:(-r['total_amount'],r['account_a'],r['account_b']))
        return rows[:p.get('limit',p.get('top_k'))]
