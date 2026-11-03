"""Render reviewed historical numbers; this does not run a DB or model."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    OUT = args.output
    OUT.mkdir(parents=True, exist_ok=False)
    data = json.loads((ROOT / 'evidence/overview.json').read_text())
    plt.rcParams.update({'font.size': 11, 'axes.titlesize': 13, 'axes.titleweight': 'bold',
                         'font.family': 'DejaVu Sans', 'pdf.fonttype': 42})
    fig, axes = plt.subplots(2, 2, figsize=(15, 10.5))
    fig.subplots_adjust(top=.88, bottom=.19, left=.13, right=.97, hspace=.68, wspace=.42)
    gray, teal, red = '#66788c', '#16857e', '#c55b45'
    x = np.arange(3)
    ax = axes[0, 0]
    for key, off, color in [('reference', -.18, gray), ('optimized', .18, teal)]:
        bars = ax.bar(x + off, data['query_seconds'][key], width=.34, color=color, label=key.title())
        ax.bar_label(bars, fmt='%.3f', fontsize=10, padding=3)
    ax.set(xticks=x, xticklabels=['L4: fan-in', 'L5: linked pairs', 'L6: flow ranking'],
           ylabel='DB-only seconds', ylim=(0, 9), title='A  Cypher reduces traversal work')
    ax.legend(frameon=False, fontsize=10)
    ax.text(0, -.25, 'Historical Python study; 4 samples per tool/version.\nSame query semantics; 256MiB page cache.', transform=ax.transAxes, fontsize=10)

    ax = axes[0, 1]
    for key, off, color in [('mixed', -.18, gray), ('fanin', .18, teal)]:
        for i, val in enumerate(data['memory_seconds'][key]):
            if val is None:
                ax.text(i + off, .25, '42/96\nfailed arm', ha='center', va='bottom', color=red, fontsize=9)
                continue
            ax.bar(i + off, val, width=.34, color=color,
                   label=('Mixed' if key == 'mixed' else 'L4-heavy') if i == 1 else None)
            ax.text(i + off, val + .2, f'{val:.2f}', ha='center', fontsize=10)
    ax.set(xticks=x, xticklabels=['H512 / P256', 'H1024 / P256', 'H1024 / P1024'],
           ylabel='DB-only batch seconds', ylim=(0, 12), title='B  Heap + cache: reliability and speed')
    ax.legend(frameon=False, fontsize=10)
    ax.text(0, -.25, 'MiB; 3-block medians. Mixed: 240/240 per arm.\nL4-heavy: failed baseline time omitted; others 96/96.', transform=ax.transAxes, fontsize=10)

    ax = axes[1, 0]
    ys = [r['candidate_batch_change_percent'] for r in data['e2e']]
    bars = ax.barh(range(4), ys, color=[red if v > 0 else teal for v in ys], height=.6)
    for bar, v in zip(bars, ys):
        ax.text(v + (.10 if v >= 0 else -.10), bar.get_y() + .3, f'{v:+.2f}%',
                ha='left' if v >= 0 else 'right', va='center', fontsize=11)
    ax.set(yticks=range(4), yticklabels=['Frozen32 / C8', 'Frozen32 / C32', 'Hard40 / C8', 'Hard40 / C32'],
           xlim=(-2.1, 4.6), xlabel='Batch time change (%)   |   negative = faster',
           title='C  DB gains do not guarantee E2E gains')
    ax.invert_yaxis()
    ax.axvline(0, color='#333333', linewidth=.8)
    ax.text(0, -.28, 'BF16 + neo4rs; H512/P256 vs H1024/P1024.\n864/864 correct, 0 failed/dropped; 3-block medians.', transform=ax.transAxes, fontsize=10)

    ax = axes[1, 1]
    rows = sorted(data['tail'], key=lambda r: (r['block'], r['arm'] != 'baseline'))
    for i, r in enumerate(rows):
        ax.barh(i, r['first_schedule_s'] - r['enqueue_s'], left=r['enqueue_s'], color='#dda15e', height=.62,
                label='Engine queue' if i == 0 else None)
        ax.barh(i, r['end_s'] - r['first_schedule_s'], left=r['first_schedule_s'], color=teal, height=.62,
                label='After first schedule' if i == 0 else None)
    ax.set(yticks=range(6), yticklabels=[f"B{r['block']+1} {'base' if r['arm']=='baseline' else 'candidate'}" for r in rows],
           xlim=(0, 145), xlabel='Seconds from cohort start', title='D  Same tail request, later model admission')
    ax.invert_yaxis()
    ax.set_ylim(5.7, -1.25)
    ax.legend(frameon=False, fontsize=9, loc='upper left', bbox_to_anchor=(.01, 1.01))
    ax.text(0, -.28, 'Hard40/C32: same request/evidence, 1,728 final tokens.\nBlank prefix is earlier episode work, not idle time.', transform=ax.transAxes, fontsize=10)
    for ax in axes.flat:
        ax.spines[['top', 'right']].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis='x' if ax in axes[1] else 'y', alpha=.15)
    fig.suptitle('Graph-CoT: from Neo4j optimization to vLLM re-entry', fontsize=20, fontweight='bold', y=.97)
    fig.text(.5, .925, 'Reduce DB work; then measure when tool results re-enter the model queue.', ha='center', fontsize=13)
    fig.text(.5, .025, 'Separate studies, workloads and budgets; do not multiply gains. Finite cohorts on a shared host, not sustained capacity.\nSource values and provenance: evidence/overview.json. Full interpretation: RESULTS.md', ha='center', fontsize=10)
    for ext in ['png', 'pdf']:
        fig.savefig(OUT / f'neo4j-to-vllm-overview.{ext}', dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    main()
