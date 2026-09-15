"""lab 020 guided decoding: per grammar pass — turns by constraint kind (TPOT, latency, output tokens, truncations at max_tokens), first-compile latency, zero-row events.

usage: python3 summarize_agentic_os_grammar.py <run-dir>
"""
import glob, json, sys, statistics, collections
R = sys.argv[1]
def p50(v): return round(statistics.median(v), 1) if v else None
for pas in sorted(p.rsplit('run-', 1)[1] for p in glob.glob(f'{R}/run-*grammar*')):
    cs = []
    for f in glob.glob(f'{R}/run-{pas}/e2e/*/shard-*/attempts/*/run-receipt.json'): cs += json.load(open(f)).get('cases', [])
    kinds = collections.defaultdict(lambda: {'n': 0, 'tpot': [], 'lat': [], 'out': [], 'trunc': 0})
    first_constrained = []
    terminals = collections.Counter(); zero_by_case = []
    for c in cs:
        seen_c = False
        for t in c.get('turns') or []:
            k = ('constrained' if t.get('constrained') else 'free') + '/' + str(t.get('output_kind'))
            d = kinds[k]; d['n'] += 1
            o = int(t.get('output_tokens') or 0); lat = float(t.get('latency_ms') or 0)
            if o and lat: d['tpot'].append(lat / o)
            d['lat'].append(lat); d['out'].append(o)
            if o >= 512: d['trunc'] += 1
            if t.get('constrained') and not seen_c: first_constrained.append(lat); seen_c = True
        terminals[c.get('terminal')] += 1
        tools = [e for e in c.get('tools') or [] if e.get('name') == 'execute_cypher']
        zero_by_case.append(sum(1 for e in tools if e.get('outcome') == 'ok' and int(e.get('rows') or 0) == 0))
    print(f"\n## {pas}: cases={len(cs)} answered={sum(1 for c in cs if c.get('answer_status')=='answered')} terminals={dict(terminals)}")
    for k, d in sorted(kinds.items()):
        print(f"  {k:22s} turns={d['n']:4d} tpot_p50={p50(d['tpot'])} ms  latency_p50={p50(d['lat'])} ms  out_tok_p50={p50(d['out'])}  truncated@512={d['trunc']}")
    fc = sorted(first_constrained)
    print(f"  first constrained turn latency: min={round(fc[0]) if fc else None} p50={p50(fc)} max={round(fc[-1]) if fc else None} ms; zero-row events per case p50={p50(zero_by_case)} sum={sum(zero_by_case)}")
