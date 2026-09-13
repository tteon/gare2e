"""Serving-arm comparison for a queue-mode run (lab 020 session A, lab 022): one row per pass.

Reads run-*/e2e/*/shard-*/attempts/*/run-receipt.json (content-free), the
vllm-restart receipts, queue-receipt.jsonl and, when present, the appliance's
/metrics scrape (e2e/spec-decode-metrics.json, JSON-ish lines) to give, per
pass: answered by stratum, policy rejections, turns / queries, output tokens,
per-case wall p50, per-turn TPOT (ms per output token), prefix-cache reuse
(cached / prompt tokens), restart readiness, and speculative acceptance
(accepted / draft tokens within the pass window).

usage: python3 summarize_agentic_os_serving_arms.py <run-dir> [markdown-out]
"""
import collections, glob, json, re, statistics, sys, pathlib
R = sys.argv[1]
ST = str(pathlib.Path(__file__).resolve().parents[2] / '.seocho/benchmarks/agentic-os/strata-v2')
stratum = {json.loads(l)['qid']: json.loads(l)['stratum'] for l in open(f'{ST}/grbench-cases.jsonl') if l.strip()}
try:
    stratum.update({json.loads(l)['qid']: json.loads(l)['stratum'] for l in open(f'{ST}/heldout-grbench-cases.jsonl') if l.strip()})
except FileNotFoundError:
    pass
def p50(v): return round(statistics.median(v), 2) if v else None
queue = {}
for l in open(f'{R}/queue-receipt.jsonl'):
    if l.strip():
        q = json.loads(l); queue[q['pass_id']] = q
# spec-decode scrape: {ts, metric{labels}, value} lines with unescaped inner quotes -> regex
scrape = collections.defaultdict(list)
for f in glob.glob(f'{R}/e2e/spec-decode-metrics.json'):
    for l in open(f):
        m = re.match(r'\{"ts":([0-9.]+),"metric":"([^{"]+)(?:\{[^}]*\})?","value":([0-9.e+-]+)\}', l.strip())
        if m: scrape[m.group(2)].append((int(m.group(1)), float(m.group(3))))
def window_delta(metric, start, end):
    pts = [(t, v) for t, v in scrape.get(metric, []) if start <= t <= end]
    return (pts[-1][1] - pts[0][1]) if len(pts) >= 2 else None
rows = []
for pdir in sorted(glob.glob(f'{R}/run-*')):
    pas = pdir.rsplit('run-', 1)[1]
    q = queue.get(pas, {}); end = float(q.get('finished_unix_s') or 0); start = end - float(q.get('wall_s') or 0)
    restart = {}
    for f in glob.glob(f'{pdir}/vllm-restart*/vllm-restart-receipt.json'):
        restart = json.load(open(f))
    cases = []
    for f in glob.glob(f'{pdir}/e2e/*/shard-*/attempts/*/run-receipt.json'):
        d = json.load(open(f)); cfg = d.get('config') or {}
        for c in d.get('cases') or []:
            c['_config'] = cfg; cases.append(c)
    if not cases: continue
    answered = collections.Counter(); n = collections.Counter(); codes = collections.Counter()
    turns, out_tok, in_tok, cached, wall, tpot, queries, zero = [], [], [], [], [], [], [], 0
    for c in cases:
        st = stratum.get(str(c.get('qid')), '?'); n[st] += 1
        if c.get('answer_status') == 'answered': answered[st] += 1
        ts = c.get('turns') or []
        turns.append(len(ts)); out_tok.append(sum(int(t.get('output_tokens') or 0) for t in ts)); in_tok.append(sum(int(t.get('input_tokens') or 0) for t in ts))
        cached.append(sum(int(t.get('cached_tokens') or 0) for t in ts))
        for t in ts:
            if t.get('output_tokens') and t.get('latency_ms'): tpot.append(float(t['latency_ms']) / int(t['output_tokens']))
        wall.append(float((c.get('time') or {}).get('wall_ms') or sum(float(t.get('latency_ms') or 0) for t in ts)) / 1000)
        tools = [e for e in c.get('tools') or [] if e.get('name') == 'execute_cypher']
        queries.append(len(tools)); zero += sum(1 for e in tools if e.get('outcome') == 'ok' and int(e.get('rows') or 0) == 0)
        for e in tools:
            if e.get('outcome') != 'ok': codes[e.get('error_code') or e.get('policy_code') or 'error'] += 1
    model = (cases[0]['_config'] or {}).get('model'); constrained = (cases[0]['_config'] or {}).get('constrained')
    drafts = window_delta('vllm:spec_decode_num_drafts_total', start, end); dtok = window_delta('vllm:spec_decode_num_draft_tokens_total', start, end); atok = window_delta('vllm:spec_decode_num_accepted_tokens_total', start, end)
    gen = window_delta('vllm:generation_tokens_total', start, end)
    rows.append({'pass': pas, 'model': (model or '').split('/')[-1], 'constrained': constrained, 'extra_args': ' '.join(restart.get('extra_args') or [])[:60], 'ready_s': restart.get('ready_after_s'),
                 'cases': sum(n.values()), 'answered e/m/h': f"{answered['easy']}/{answered['medium']}/{answered['hard']}", 'answered': sum(answered.values()),
                 'rejections': dict(codes) or '-', 'zero_rows': zero, 'queries_p50': p50(queries), 'turns_p50': p50(turns), 'out_tok_sum': sum(out_tok), 'in_tok_sum': sum(in_tok),
                 'cached_share': round(sum(cached) / max(1, sum(in_tok)), 2), 'wall_p50_s': p50(wall), 'tpot_p50_ms': p50(tpot), 'pass_wall_s': q.get('wall_s'),
                 'gen_tok_window': gen, 'drafts': drafts, 'draft_tok': dtok, 'accepted_tok': atok, 'accept_rate': round(atok / dtok, 3) if dtok else None, 'acc_len': round(atok / drafts, 2) if drafts else None})
cols = ['pass', 'model', 'constrained', 'extra_args', 'ready_s', 'cases', 'answered e/m/h', 'answered', 'rejections', 'zero_rows', 'queries_p50', 'turns_p50', 'out_tok_sum', 'in_tok_sum', 'cached_share', 'wall_p50_s', 'tpot_p50_ms', 'pass_wall_s', 'gen_tok_window', 'drafts', 'draft_tok', 'accepted_tok', 'accept_rate', 'acc_len']
md = '| ' + ' | '.join(cols) + ' |\n|' + '---|' * len(cols) + '\n' + ''.join('| ' + ' | '.join(str(r.get(k)) for k in cols) + ' |\n' for r in rows)
print(md)
if len(sys.argv) > 2: open(sys.argv[2], 'w').write(md)
