"""Build the inline fragment from reviewed illustrative graph data (standard library)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('refusing overwrite')
    data=json.loads((ROOT/'subgraph-data.json').read_text())
    measured=json.loads((ROOT/'measured-profile-summary.json').read_text())
    for case in data['cases']:
        assert case['hits']==[next(r['db_hits'] for r in measured['records'] if r['case']==case['id'] and r['version']==version) for version in ['reference','optimized']]
    for c in data['cases']:
        nodes={n['id'] for n in c['nodes']};edges={e['id'] for e in c['edges']}
        assert len(nodes)==len(c['nodes']) and len(edges)==len(c['edges'])
        assert all(e['a'] in nodes and e['b'] in nodes for e in c['edges'])
        for side in ['before','after']:
            assert len(c[side])==data['steps_per_case']
            assert all(set(p['edges'])<=edges and set(p['selected']+p['rejected'])<=nodes for p in c[side])
    source=(ROOT/'subgraph-template.html').read_text()
    assert source.count('__DATA__')==1
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(source.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c')))
    print('Built fragment; graph identifiers and seven phases per lane verified.')


if __name__=='__main__':
    main()
