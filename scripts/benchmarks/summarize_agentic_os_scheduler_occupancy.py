"""Scheduler occupancy per pass (lab 020 / 022): how full was the decode batch, and was there headroom?

Reads run-*/workload/vllm-metrics.jsonl (1 s vLLM /metrics samples taken by the
appliance workload sampler) and run-*/workload/gpu.csv.  Per pass: mean and
p90 of running requests, share of samples with running < K (idle decode
slots: agents are between turns, in a tool call or in DozerDB), waiting
requests (queueing = no headroom), KV-cache usage mean / max, GPU utilisation
mean, generation tokens per second, and — when the /metrics scrape with
speculative counters exists (e2e/spec-decode-metrics.json) — accepted tokens
per draft.  The headroom question: KV max and waiting say whether a larger K
fits; running < K says how much of the batch the agent loop itself leaves empty.

usage: python3 summarize_agentic_os_scheduler_occupancy.py <run-dir> [K] [markdown-out]
"""
import csv, glob, json, statistics, sys
R = sys.argv[1]; K = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8
def p(v, q): 
    if not v: return None
    v = sorted(v); return round(v[min(len(v) - 1, int(q * len(v)))], 2)
def mean(v): return round(statistics.mean(v), 2) if v else None
rows = []
for pdir in sorted(glob.glob(f'{R}/run-*')):
    pas = pdir.rsplit('run-', 1)[1]
    try:
        samples = [json.loads(l) for l in open(f'{pdir}/workload/vllm-metrics.jsonl') if l.strip()]
    except FileNotFoundError:
        continue
    def series(name):
        out = []
        for s in samples:
            m = s.get('metrics') or {}
            v = m.get(name)
            if isinstance(v, dict): v = v.get('value', v.get('sum'))
            if isinstance(v, (int, float)): out.append((float(s['timestamp_unix_s']), float(v)))
        return out
    running = [v for _, v in series('vllm:num_requests_running')]
    waiting = [v for _, v in series('vllm:num_requests_waiting')]
    kv = [v for _, v in series('vllm:kv_cache_usage_perc')]
    gen = series('vllm:generation_tokens')
    active = [v for v in running if v > 0]
    tok_s = round((gen[-1][1] - gen[0][1]) / max(1e-9, gen[-1][0] - gen[0][0]), 1) if len(gen) >= 2 else None
    try:
        g = list(csv.DictReader(open(f'{pdir}/workload/gpu.csv')))
        util = [float(r['gpu_utilization_pct']) for r in g if r.get('gpu_utilization_pct') not in (None, '')]
        mem = [int(r['memory_used_bytes']) / 2**30 for r in g if r.get('memory_used_bytes')]
    except FileNotFoundError:
        util, mem = [], []
    rows.append({'pass': pas, 'samples': len(samples), 'running_mean': mean(running), 'running_p90': p(running, 0.9), 'running_max': max(running) if running else None,
                 'share_running_lt_K': round(sum(1 for v in active if v < K) / max(1, len(active)), 2), 'share_idle': round(sum(1 for v in running if v == 0) / max(1, len(running)), 2),
                 'waiting_mean': mean(waiting), 'waiting_max': max(waiting) if waiting else None, 'kv_mean_pct': mean([v * (100 if max(kv or [0]) <= 1 else 1) for v in kv]), 'kv_max_pct': round(max(kv) * (100 if max(kv or [0]) <= 1 else 1), 2) if kv else None,
                 'gpu_util_mean': mean(util), 'gpu_mem_gib_max': round(max(mem), 1) if mem else None, 'gen_tok_s': tok_s})
cols = ['pass', 'samples', 'running_mean', 'running_p90', 'running_max', 'share_running_lt_K', 'share_idle', 'waiting_mean', 'waiting_max', 'kv_mean_pct', 'kv_max_pct', 'gpu_util_mean', 'gpu_mem_gib_max', 'gen_tok_s']
md = f'K = {K}\n\n| ' + ' | '.join(cols) + ' |\n|' + '---|' * len(cols) + '\n' + ''.join('| ' + ' | '.join(str(r.get(k)) for k in cols) + ' |\n' for r in rows)
print(md)
if len(sys.argv) > 3: open(sys.argv[3], 'w').write(md)
