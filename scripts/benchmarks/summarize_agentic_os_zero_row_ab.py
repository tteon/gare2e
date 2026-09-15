"""lab 021 step 2 (summarize_agentic_os_zero_row_ab.py): zero-row hint on/off A/B from a queue-mode run dir (content-free fields only).

usage: python3 zero_row_ab.py <run-dir> [markdown-out]
Per pass x stratum: cases, answered, zero-row query events, recovery after a
zero-row event (a later execute_cypher in the same case returned rows), repair
turns spent after empties, wall/turn medians, hint receipts (kinds, reads, ms).
"""
import collections, glob, json, statistics, sys
R = sys.argv[1]
import pathlib
ST = str(pathlib.Path(__file__).resolve().parents[2] / '.seocho/benchmarks/agentic-os/strata-v2')
stratum = {json.loads(l)['qid']: json.loads(l)['stratum'] for l in open(f'{ST}/grbench-cases.jsonl') if l.strip()}
family = {str(json.loads(l)['qid']): json.loads(l).get('family') for l in open(f'{ST}/grbench-difficulty.jsonl') if l.strip()}
def p50(v): return round(statistics.median(v), 1) if v else None
rows_out = []; fam_out = collections.defaultdict(lambda: collections.Counter()); hint_kinds = collections.defaultdict(collections.Counter)
for pdir in sorted(glob.glob(f'{R}/run-*')):
    pas = pdir.rsplit('run-', 1)[1]
    for lanedir in sorted(glob.glob(f'{pdir}/e2e/*-tool-*')):
        lane = lanedir.rsplit('/', 1)[1]
        cases = []
        for f in glob.glob(f'{lanedir}/shard-*/attempts/*/run-receipt.json'):
            d = json.load(open(f))
            cases.extend(d.get('cases') or [])
        agg = collections.defaultdict(lambda: collections.defaultdict(list))
        for c in cases:
            st = stratum.get(str(c.get('qid')), 'unknown'); a = agg[st]
            q = [e for e in c.get('tools', []) if e.get('name') == 'execute_cypher']
            ok = [e for e in q if e.get('outcome') == 'ok']
            zero_idx = [i for i, e in enumerate(q) if e.get('outcome') == 'ok' and int(e.get('rows') or 0) == 0]
            recovered = sum(1 for i in zero_idx if any(int(e.get('rows') or 0) > 0 for e in q[i+1:] if e.get('outcome') == 'ok'))
            hints = [e.get('zero_row_diagnostic') for e in q if e.get('zero_row_diagnostic')]
            a['cases'].append(1)
            a['answered'].append(1 if c.get('answer_status') == 'answered' else 0)
            a['exhausted'].append(1 if str(c.get('terminal', '')).endswith('exhausted') else 0)
            a['queries'].append(len(q)); a['ok_queries'].append(len(ok)); a['zero_rows'].append(len(zero_idx))
            a['zero_cases'].append(1 if zero_idx else 0); a['recovered'].append(recovered)
            a['empty_retries'].append(int(c.get('empty_result_retry_count') or 0)); a['repairs'].append(int(c.get('repair_count') or 0))
            a['turns'].append(int(c.get('turn_count') or len(c.get('turns') or [])))
            t = c.get('time') or {}
            wall = t.get('case_execution_wall_ms') or t.get('wall_ms') or sum(float(x.get('latency_ms') or 0) for x in c.get('turns') or [])
            a['wall_s'].append(float(wall) / 1000)
            a['db_ms'].append(sum(float(e.get('wall_ms') or e.get('latency_ms') or 0) for e in q))
            a['hint_events'].append(len(hints)); a['hint_ms'].extend(float(h.get('hint_ms') or 0) for h in hints)
            a['hint_reads'].extend(int(h.get('reads') or 0) for h in hints)
            a['hint_resolved'].append(sum(1 for h in hints if h.get('hint_kind') == 'resolved'))
            for h in hints: hint_kinds[(pas, st)][h.get('hint_kind')] += 1
            fam = family.get(str(c.get('qid')))
            fam_out[(pas, fam)]['cases'] += 1; fam_out[(pas, fam)]['answered'] += 1 if c.get('answer_status') == 'answered' else 0
            fam_out[(pas, fam)]['zero_rows'] += len(zero_idx); fam_out[(pas, fam)]['recovered'] += recovered
        for st in ('easy', 'medium', 'hard', 'unknown'):
            if st not in agg: continue
            a = agg[st]
            rows_out.append({'pass': pas, 'lane': lane, 'stratum': st, 'cases': sum(a['cases']), 'answered': sum(a['answered']), 'exhausted': sum(a['exhausted']),
                             'queries': sum(a['queries']), 'zero_row_events': sum(a['zero_rows']), 'cases_with_zero': sum(a['zero_cases']), 'recovered_after_zero': sum(a['recovered']),
                             'empty_retries': sum(a['empty_retries']), 'repairs': sum(a['repairs']), 'turns_p50': p50(a['turns']), 'wall_s_p50': p50(a['wall_s']),
                             'db_ms_p50': p50(a['db_ms']), 'hint_events': sum(a['hint_events']), 'hint_resolved': sum(a['hint_resolved']), 'hint_ms_p50': p50(a['hint_ms']), 'hint_reads_p50': p50(a['hint_reads'])})
cols = ['pass', 'stratum', 'cases', 'answered', 'exhausted', 'queries', 'zero_row_events', 'cases_with_zero', 'recovered_after_zero', 'empty_retries', 'repairs', 'turns_p50', 'wall_s_p50', 'db_ms_p50', 'hint_events', 'hint_resolved', 'hint_ms_p50', 'hint_reads_p50']
md = '| ' + ' | '.join(cols) + ' |\n|' + '---|' * len(cols) + '\n'
for r in rows_out: md += '| ' + ' | '.join(str(r.get(k)) for k in cols) + ' |\n'
md += '\n**hint kinds (pass, stratum):** ' + '; '.join(f'{k[0]}/{k[1]} ' + ','.join(f'{kk}={vv}' for kk, vv in sorted(v.items())) for k, v in sorted(hint_kinds.items())) + '\n'
md += '\n| pass | family | cases | answered | zero_row_events | recovered_after_zero |\n|---|---|---|---|---|---|\n'
for (pas, fam), v in sorted(fam_out.items(), key=lambda kv: (kv[0][1] or '', kv[0][0])):
    md += f"| {pas} | {fam} | {v['cases']} | {v['answered']} | {v['zero_rows']} | {v['recovered']} |\n"
print(md)
if len(sys.argv) > 2: open(sys.argv[2], 'w').write(md)
