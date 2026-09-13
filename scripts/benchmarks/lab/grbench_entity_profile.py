"""Entity profile of the GRBench MAPLE/Physics questions (data-scientist view, offline, no LLM).

For each question: the anchor entities it names (quoted paper titles, author
names after 'by'/'of'), whether they resolve to graph nodes, and the degree of
those nodes per relation. Then: duplication across questions, hub share,
per-stratum anchor degree, and how many distinct nodes the 90-case cohort
touches — the inputs a router / cache policy would need.
"""
import csv, gzip, json, re, sys, collections, time
ROOT = '/home/hadry/lab/seocho-room/seocho'
Q = f'{ROOT}/.seocho/datasets/graph-cot/data/processed_data/maple/Physics/data.json'
IMP = f'{ROOT}/.seocho/benchmarks/agentic-os/colocated-appliance/bootstrap/grbench-import-v1'
ST = f'{ROOT}/.seocho/benchmarks/agentic-os/strata-v2'
OUT = sys.argv[1]
t0 = time.time()
questions = [json.loads(l) for l in open(Q) if l.strip()]
strat = {json.loads(l)['qid']: json.loads(l)['stratum'] for l in open(f'{ST}/grbench-cases.jsonl')}
labels = {str(json.loads(l)['qid']): json.loads(l) for l in open(f'{ST}/grbench-difficulty.jsonl')}
quoted = re.compile(r'"([^"]+?)\??"')
mentions = {}  # qid -> list of (kind, text)
for q in questions:
    text = q['question']
    ents = [('title', m.strip().casefold()) for m in quoted.findall(text)]
    # author names: capitalised token runs not inside quotes, 2-4 tokens
    outside = quoted.sub(' ', text)
    for m in re.finditer(r"\b([A-Z][\w'.-]+(?:\s+[A-Z][\w'.-]+){1,3})\b", outside):
        cand = m.group(1)
        if cand.split()[0] in {'Can', 'What', 'Which', 'Who', 'How', 'Please', 'Is', 'Are', 'Did', 'Does', 'List', 'Name', 'Find', 'Identify', 'In', 'The', 'Among', 'Based'}:
            continue
        ents.append(('name', cand.casefold()))
    mentions[q['qid']] = ents
all_ent = collections.Counter(e for ents in mentions.values() for e in ents)
titles = {e[1] for e in all_ent if e[0] == 'title'}; names = {e[1] for e in all_ent if e[0] == 'name'}
# resolve to node ids by scanning the node CSV once
id_of = {}
with gzip.open(f'{IMP}/grbench-nodes.csv.gz', 'rt', encoding='utf-8', newline='') as fh:
    rd = csv.reader(fh); header = next(rd); col = {h: i for i, h in enumerate(header)}
    ci, cty, cname, ctitle = col['id:ID(GRBenchNode)'], col['node_type'], col['feature_name'], col['feature_title']
    for row in rd:
        nm = row[cname].casefold(); tt = row[ctitle].casefold()
        if row[cty] == 'paper' and tt in titles: id_of[('title', tt)] = (row[ci], 'paper')
        elif row[cty] in ('author', 'venue') and nm in names: id_of[('name', nm)] = (row[ci], row[cty])
t1 = time.time()
anchor_ids = {v[0] for v in id_of.values()}
deg = {i: collections.Counter() for i in anchor_ids}
with gzip.open(f'{IMP}/grbench-rels.csv.gz', 'rt', encoding='utf-8', newline='') as fh:
    rd = csv.reader(fh); header = next(rd); col = {h: i for i, h in enumerate(header)}
    cs, crel = col[':START_ID(GRBenchNode)'], col['relation_name']
    for row in rd:
        s = row[cs]
        if s in deg: deg[s][row[crel]] += 1
t2 = time.time()
per_q = []
for q in questions:
    qid = q['qid']; ents = mentions[qid]
    resolved = [(k, t, *id_of[(k, t)]) for k, t in ents if (k, t) in id_of]
    degs = [sum(deg[r[2]].values()) for r in resolved]
    per_q.append({'qid': qid, 'stratum': strat.get(qid), 'family': labels.get(qid, {}).get('family'), 'mentions': len(ents), 'resolved': len(resolved),
                  'anchor_types': sorted({r[3] for r in resolved}), 'anchor_degree_max': max(degs) if degs else None, 'anchor_degree_sum': sum(degs) if degs else None,
                  'anchor_ids': [r[2] for r in resolved]})
cohort = [p for p in per_q if p['stratum']]
ent_counts = collections.Counter(e for ents in mentions.values() for e in ents)
node_counts = collections.Counter(i for p in per_q for i in p['anchor_ids'])
node_counts_c = collections.Counter(i for p in cohort for i in p['anchor_ids'])
def q50(v): v = sorted(v); return v[len(v)//2] if v else None
summary = {
  'questions': len(questions), 'cohort': len(cohort),
  'mentions_total': sum(ent_counts.values()), 'distinct_entities': len(ent_counts), 'entity_dup_rate': round(1 - len(ent_counts)/max(1, sum(ent_counts.values())), 3),
  'resolved_share': round(sum(p['resolved'] for p in per_q)/max(1, sum(p['mentions'] for p in per_q)), 3),
  'distinct_anchor_nodes_all': len(node_counts), 'nodes_hit_by_2plus_questions_all': sum(1 for v in node_counts.values() if v >= 2),
  'distinct_anchor_nodes_cohort': len(node_counts_c), 'nodes_hit_by_2plus_questions_cohort': sum(1 for v in node_counts_c.values() if v >= 2),
  'anchor_type_mix': collections.Counter(t for p in per_q for t in p['anchor_types']),
  'anchor_degree_p50_by_stratum': {s: q50([p['anchor_degree_max'] for p in cohort if p['stratum']==s and p['anchor_degree_max'] is not None]) for s in ('easy','medium','hard')},
  'anchor_degree_max_by_stratum': {s: max([p['anchor_degree_max'] for p in cohort if p['stratum']==s and p['anchor_degree_max'] is not None] or [None]) for s in ('easy','medium','hard')},
  'unresolved_questions_cohort': [p['qid'] for p in cohort if p['resolved']==0],
  'top_shared_nodes_all': [(k, v, sum(deg[k].values())) for k, v in node_counts.most_common(8)],
  'family_anchor_degree_p50': {f: q50([p['anchor_degree_max'] for p in per_q if p['family']==f and p['anchor_degree_max'] is not None]) for f in sorted({p['family'] for p in per_q if p['family']})},
  'timing_s': {'nodes_scan': round(t1-t0,1), 'rels_scan': round(t2-t1,1)},
}
json.dump({'summary': summary, 'per_question': per_q}, open(OUT, 'w'), indent=1, default=str)
print(json.dumps(summary, default=str)[:1500])
