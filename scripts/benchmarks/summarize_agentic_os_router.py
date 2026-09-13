"""lab 019: router evaluation from a queue-mode run — routed lanes, answered per lane, reasons, anchor resolution, per-family vs S1 comparators (content-free receipts).

usage: python3 summarize_agentic_os_router.py <run-dir>
"""
import glob, json, sys, collections, statistics
R = sys.argv[1]
import pathlib
ST = str(pathlib.Path(__file__).resolve().parents[2] / '.seocho/benchmarks/agentic-os/strata-v2')
fam = {}
for f in (f'{ST}/grbench-difficulty.jsonl',):
    for l in open(f):
        if l.strip(): d = json.loads(l); fam[str(d['qid'])] = d.get('family')
for l in open(f'{ST}/heldout-grbench-cases.jsonl'):
    if l.strip(): d = json.loads(l); fam.setdefault(str(d['qid']), d.get('family'))
def cases(pas):
    out = []
    for f in glob.glob(f'{R}/run-{pas}/e2e/*/shard-*/attempts/*/run-receipt.json'):
        out += json.load(open(f)).get('cases', [])
    return out
def p50(v): return round(statistics.median(v), 1) if v else None
for pas in ('router-v0-k8', 'router-v0a-k8', 'heldout-router-v0-k8'):
    cs = cases(pas)
    lanes = collections.Counter(); ans_by_lane = collections.Counter(); reasons = collections.Counter(); fams = collections.defaultdict(lambda: [0, 0])
    resolved = []; classes = collections.Counter(); ms = []; abst = 0
    for c in cs:
        plan = c.get('router_plan') or {}; ra = c.get('request_analysis') or {}
        key = f"{plan.get('lane')}/{plan.get('arm')}"; lanes[key] += 1
        ok = c.get('answer_status') == 'answered'
        if ok: ans_by_lane[key] += 1
        if plan.get('lane') == 'abstain': abst += 1
        for r in plan.get('reasons') or []: reasons[r] += 1
        resolved.append((ra.get('mentions') or 0, ra.get('anchors_resolved') or 0)); classes[ra.get('degree_class_max')] += 1; ms.append(float(ra.get('analysis_ms') or 0))
        f = fam.get(str(c.get('qid'))); fams[f][0] += 1; fams[f][1] += int(ok)
    print(f"\n## {pas}: cases={len(cs)} answered={sum(ans_by_lane.values())} abstained={abst}")
    print('  lanes:', {k: f"{ans_by_lane[k]}/{v}" for k, v in sorted(lanes.items())})
    print('  reasons:', dict(reasons))
    print('  mentions/resolved: cases with >=1 mention', sum(1 for m, r in resolved if m), ' with >=1 resolved', sum(1 for m, r in resolved if r), ' none resolved', sum(1 for m, r in resolved if m and not r))
    print('  degree_class_max:', dict(classes), ' analysis_ms p50', p50(ms), 'max', round(max(ms), 1) if ms else None)
    print('  by family answered/cases:', {k: f"{v[1]}/{v[0]}" for k, v in sorted(fams.items(), key=lambda kv: str(kv[0]))})
# S1 comparators by family
for pas, lab in (('heldout-s1-k8', R), ('m27b-s1-k8', str(pathlib.Path(__file__).resolve().parents[2] / '.seocho/benchmarks/agentic-os/colocated-appliance/runs/lab-s20-try-103026'))):
    cs = []
    for f in glob.glob(f'{lab}/run-{pas}/e2e/*/shard-*/attempts/*/run-receipt.json'): cs += json.load(open(f)).get('cases', [])
    fams = collections.defaultdict(lambda: [0, 0])
    for c in cs:
        f = fam.get(str(c.get('qid'))); fams[f][0] += 1; fams[f][1] += int(c.get('answer_status') == 'answered')
    print(f"\n## {pas} (S1 comparator): answered={sum(v[1] for v in fams.values())}/{len(cs)}")
    print('  by family:', {k: f"{v[1]}/{v[0]}" for k, v in sorted(fams.items(), key=lambda kv: str(kv[0]))})
