"""Export reviewed historical prose; original bytes remain in encrypted evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re


def sanitize(text: str) -> str:
    text = re.sub(r'/(?:Users|workspace|opt|tmp|root)/[^\s`<>"\)\]]+', '<instance-path>', text)
    text = re.sub(r'(?:root@)?ssh\d+\.vast\.ai(?::\d+)?', '<instance-host>', text)
    text = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?', '<endpoint>', text)
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<email>', text)

    def link(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        if url.startswith(('https://neo4j.com/', 'https://docs.vllm.ai/')):
            return match.group(0)
        return label + ' [see original archived attachment]'

    text = re.sub(r'!?\[([^\]]*)\]\(([^)]+)\)', link, text)
    return text


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    selected = list((a.source_root/'results').glob('*/FINDINGS.md'))
    selected += list((a.source_root/'results').glob('*/final-evidence/FINDINGS.md'))
    names = ['QUANTIZATION_ABLATION', 'BF16_REPEATABILITY', 'NEO4J_RUNTIME_FINDINGS',
             'RUNTIME_MEASUREMENTS', 'STUDY_RESULTS', 'NEO4RS_INTERVENTION_RESULTS',
             'SINGLE_TOOL_AXES_RESULTS', 'NEO4RS_TUNING_RESULTS', 'VLLM_RESEARCH_HANDOFF_SUMMARY']
    selected += [a.source_root/'docs'/f'{n}.md' for n in names]
    records = []
    for source in sorted(selected):
        if not source.is_file() or source.is_symlink():
            continue
        rel = str(source.relative_to(a.source_root))
        name = rel.replace('/', '__')
        original = source.read_bytes()
        body = sanitize(original.decode())
        header = ('> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. '
                  'This is not a new measurement. Original: `'+rel+'`; SHA256 `'+hashlib.sha256(original).hexdigest()+'`.\n\n')
        target = a.output/name
        target.write_text(header+body)
        records.append(dict(source_path=rel, source_sha256=hashlib.sha256(original).hexdigest(),
                            exported_file=name, export_sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    (a.output/'REPORT-MANIFEST.json').write_text(json.dumps({'reports': records,
        'changes': 'Mask private absolute paths, endpoint/email/host strings; replace unavailable attachment links; preserve numerical prose.'}, indent=2)+'\n')
    print(json.dumps({'reports_exported':len(records)}))


if __name__ == '__main__':
    main()
