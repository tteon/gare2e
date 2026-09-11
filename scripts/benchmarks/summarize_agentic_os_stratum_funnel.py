"""Per pass x lane x stratum retrieval funnel, graph-DB call latency, policy-rejection codes and
unanchored_scan recovery from a queue-mode run (run-receipt.json + receipts.jsonl; content-free fields only).

    python3 scripts/benchmarks/summarize_agentic_os_stratum_funnel.py <run-root> <strata-dir>

Writes <run-root>/session-summary/stratum-funnel.md and prints it."""
import json,glob,collections,sys,statistics
R=sys.argv[1]; ST=sys.argv[2]
CODES=('unanchored_scan','missing_workspace_scope','missing_evidence_columns','unsafe_call_forbidden','missing_grbench_node_label','unsupported_query_shape','write_clause_forbidden','missing_return')
strat={}
for lane in ['grbench','gtsqa']:
    for l in open(f'{ST}/{lane}-cases.jsonl'):
        c=json.loads(l); strat[(lane,str(c['qid']))]=c['stratum']
def walk_strings(o):
    if isinstance(o,dict):
        for k,v in o.items():
            if k in ('observation','preview','message','answer','question','query','evidence'): continue
            yield from walk_strings(v)
    elif isinstance(o,list):
        for v in o: yield from walk_strings(v)
    elif isinstance(o,str): yield o
def graph_calls(c):
    out=[]
    for t in (c.get('tools') or []) if isinstance(c.get('tools'),list) else []:
        if 'execute' in str(t.get('name','')) and isinstance(t.get('latency_ms'),(int,float)): out.append((t['latency_ms'],str(t.get('outcome'))))
    tl=c.get('tools') if isinstance(c.get('tools'),dict) else {}
    for t in tl.get('events') or []:
        if 'execute' in str(t.get('name','')) and isinstance(t.get('latency_ms'),(int,float)): out.append((t['latency_ms'],str(t.get('outcome'))))
    for e in c.get('events') or []:
        if 'execute' in str(e.get('name','')) and 'tool' in str(e.get('event','')) and isinstance(e.get('latency_ms'),(int,float)): out.append((e['latency_ms'],str(e.get('outcome'))))
    return out
def cases_in(lanedir):
    for f in glob.glob(f'{lanedir}/shard-*/attempts/*/run-receipt.json'):
        for c in json.load(open(f)).get('cases',[]): yield c
    for f in glob.glob(f'{lanedir}/shard-*/attempts/*/receipts.jsonl'):
        for l in open(f):
            l=l.strip()
            if l: yield json.loads(l)
rows=[]; codes=collections.Counter(); recover=collections.Counter(); dbcalls=collections.defaultdict(list)
for pdir in sorted(glob.glob(f'{R}/run-*')):
    p=pdir.split('/')[-1][4:]
    for lanedir in sorted(glob.glob(f'{pdir}/e2e/*-tool-*')):
        lane=lanedir.split('/')[-1].rsplit('-tool-',1)[0]; fam='grbench' if lane.startswith('grbench') else 'gtsqa'
        tail=lanedir.split('/')[-1].rsplit('-tool-',1)[1]; arm_sfx=tail.split('-',1)[1] if '-' in tail else 'B0'
        cells=collections.defaultdict(list)
        for c in cases_in(lanedir):
            qid=str(c.get('qid') or c.get('case_id')).split(':')[-1]
            cells[(str(c.get('arm') or arm_sfx), strat.get((fam,qid),'?'))].append(c)
            arm=str(c.get('arm') or arm_sfx)
            for lat,oc in graph_calls(c): dbcalls[(p,lane,arm)].append(lat)
            seen=set()
            for s in walk_strings(c):
                for code in CODES:
                    if code in s: codes[(p,lane,arm,code)]+=1; seen.add(code)
            if 'unanchored_scan' in seen:
                rf=c.get('retrieval_funnel') or {}
                recover[(p,lane,arm,'attempts_seen')]+=1
                if rf.get('query_validated'): recover[(p,lane,arm,'validated_after')]+=1
                if rf.get('nonempty_result'): recover[(p,lane,arm,'nonempty_after')]+=1
        for (arm,s) in sorted(cells):
            rs=cells[(arm,s)]
            if not rs: continue
            n=len(rs); rf=lambda c:(c.get('retrieval_funnel') or {})
            F=lambda k: sum(1 for c in rs if rf(c).get(k))
            trig=sum(1 for c in rs if rf(c).get('retrieval_decision') in (True,'retrieve','triggered','tool') or rf(c).get('graph_tool_selected'))
            answered=sum(1 for c in rs if c.get('answer_status')=='answered')
            def exhausted(c):
                t=str(c.get('terminal_status') or c.get('terminal') or ''); b=c.get('budget') or {}
                return 'exhausted' in t or 'exceeded' in t or bool(b.get('exceeded')) or (b.get('enforcement') in ('exceeded','stop','exhausted'))
            ex=sum(1 for c in rs if exhausted(c))
            walls=sorted(((c.get('time') or {}).get('wall_ms') or (c.get('resource_attribution') or {}).get('critical_path_exclusive_ms',{}).get('wall') or 0) for c in rs); w50=walls[n//2]/1000
            rep=statistics.mean((c.get('repair_count') or 0) for c in rs)
            rows.append((p,lane,arm,s,n,trig,F('query_validated'),F('query_executed'),F('nonempty_result'),answered,ex,round(w50,1),round(rep,2)))
hdr='| pass | lane | arm | stratum | N | triggered | validated | executed | nonempty | answered | budget-exhausted | wall p50 s | repairs/case |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|\n'
md=hdr+''.join('| '+' | '.join(str(x) for x in r)+' |\n' for r in rows)
def pct(v,q): v=sorted(v); return v[min(len(v)-1,int(len(v)*q))]
md+='\n**Graph-DB call latency (ms), pass x lane:** '+'; '.join(f'{k[0]} {k[1]} {k[2]}: n={len(v)} p50={statistics.median(v):.0f} p90={pct(v,0.9):.0f} max={max(v):.0f} (>=15 s: {sum(1 for x in v if x>=15000)})' for k,v in sorted(dbcalls.items()))+'\n'
md+='\n**Policy rejection codes (occurrences in receipts):** '+'; '.join(f'{k[0]} {k[1]} {k[2]} {k[3]}={v}' for k,v in sorted(codes.items()))+'\n'
md+='\n**unanchored_scan recovery (attempts):** '+'; '.join(f'{k[0]} {k[1]} {k[2]} {k[3]}={v}' for k,v in sorted(recover.items()))+'\n'
open(f'{R}/session-summary/stratum-funnel.md','w').write(md); print(md)
