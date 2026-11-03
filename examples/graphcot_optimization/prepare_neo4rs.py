"""Download the pinned upstream crate, verify it and apply the recorded Txn patch."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent / 'neo4rs_adapter'


def main() -> None:
    spec = json.loads((ROOT / 'vendor/upstream-crate.json').read_text())
    target = ROOT / 'vendor/neo4rs-0.8.0'
    if target.exists():
        raise SystemExit('Vendor directory already exists; refusing to overwrite it.')
    data = urllib.request.urlopen(spec['url'], timeout=60).read()
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise SystemExit('Upstream archive SHA256 mismatch')
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            members = archive.getmembers()
            for m in members:
                if not (base / m.name).resolve().is_relative_to(base.resolve()) or not (m.isfile() or m.isdir()):
                    raise SystemExit('Unsafe archive member')
            archive.extractall(base, members=members)
        tree = base / 'neo4rs-0.8.0'
        provenance = json.loads((ROOT / 'vendor/PATCH_PROVENANCE.json').read_text())
        txn = tree / 'src/txn.rs'
        assert hashlib.sha256(txn.read_bytes()).hexdigest() == provenance['upstream_sha256']
        subprocess.run(['patch', '-p1', '-i', str(ROOT / 'vendor/transaction-lease.patch')], cwd=tree, check=True)
        assert hashlib.sha256(txn.read_bytes()).hexdigest() == provenance['patched_sha256']
        shutil.copytree(tree, target)
    print('Verified neo4rs0.8.0 + recorded Txn patch. Next: cargo test --locked --lib')


if __name__ == '__main__':
    main()
