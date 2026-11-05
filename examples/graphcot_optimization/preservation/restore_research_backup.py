"""Create, verify and restore content-addressed age-encrypted research volumes."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

BLOCK = 4 * 1024 * 1024


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(BLOCK), b''):
            h.update(b)
    return h.hexdigest()


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def safe_relative(value: str) -> Path:
    p = Path(value)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('unsafe relative path')
    return p


def create(selection: Path, output: Path, recipient: str, limit: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    source = read_json(selection)
    files = []
    unique = {}
    for record in source['files']:
        path = Path(record['physical_path'])
        safe_relative(record['logical_path'])
        if path.is_symlink() or not path.is_file():
            raise ValueError('unsupported source')
        if path.stat().st_size != record['bytes']:
            raise ValueError('source size changed: '+record['logical_path'])
        files.append({**{k: record[k] for k in ['logical_path', 'bytes', 'sha256']},
                      'mode': path.stat().st_mode & 0o777, 'mtime_ns': path.stat().st_mtime_ns})
        unique.setdefault(record['sha256'], record)
    groups, current, size = [], [], 0
    for sha, record in sorted(unique.items()):
        if current and size + record['bytes'] > limit:
            groups.append(current)
            current, size = [], 0
        current.append((sha, record))
        size += record['bytes']
    if current:
        groups.append(current)
    index = dict(format='gug-content-backup-v1', files=files,
                 exclusions=source.get('excluded', []),
                 note='Original paths map to SHA256 objects; duplicate content is stored once. No credentials/key included.')
    volumes = []
    for n, group in enumerate(groups, 1):
        name = f'gug-research-20261104-volume-{n:02d}.tar.zst.age'
        target = output / name
        with tempfile.TemporaryFile() as err:
            encrypted = subprocess.Popen(['age', '-r', recipient, '-o', str(target)], stdin=subprocess.PIPE, stderr=err)
            compressed = subprocess.Popen(['zstd', '-q', '-T2', '-3', '-c'], stdin=subprocess.PIPE,
                                          stdout=encrypted.stdin, stderr=err)
            encrypted.stdin.close()
            try:
                with tarfile.open(fileobj=compressed.stdin, mode='w|') as tar:
                    if n == 1:
                        data = json.dumps(index, indent=2).encode()
                        info = tarfile.TarInfo('index.json')
                        info.size, info.mode = len(data), 0o600
                        tar.addfile(info, io.BytesIO(data))
                    for sha, record in group:
                        path = Path(record['physical_path'])
                        # Validate exact source immediately before publication.
                        if digest(path) != sha:
                            raise ValueError('source hash changed: '+record['logical_path'])
                        info = tarfile.TarInfo('objects/'+sha)
                        info.size, info.mode = record['bytes'], 0o600
                        with path.open('rb') as stream:
                            tar.addfile(info, stream)
                compressed.stdin.close()
                if compressed.wait() or encrypted.wait():
                    raise RuntimeError('compression/encryption failed')
            except BaseException:
                for process in [compressed, encrypted]:
                    if process.poll() is None:
                        process.terminate()
                    process.wait()
                raise
        volumes.append(dict(name=name, bytes=target.stat().st_size, sha256=digest(target), objects=len(group)))
        print(json.dumps({'created': name, 'bytes': target.stat().st_size, 'objects': len(group)}), flush=True)
    manifest = dict(format='gug-content-backup-v1', logical_files=len(files), unique_objects=len(unique),
                    logical_bytes=sum(f['bytes'] for f in files), unique_bytes=sum(f['bytes'] for f in unique.values()),
                    recipient=recipient, volumes=volumes,
                    recovery='The private age identity is deliberately outside this release. Index is encrypted in volume01.')
    (output / 'BACKUP-MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (output / 'SHA256SUMS').write_text(''.join(f"{v['sha256']}  {v['name']}\n" for v in volumes))
    return manifest


def verify_or_restore(manifest_path: Path, identity: Path, output: Path | None) -> dict:
    manifest = read_json(manifest_path)
    root = manifest_path.parent
    if output:
        output.mkdir(parents=True, exist_ok=False)
        (output / '.objects').mkdir()
    objects, index = {}, None
    for volume in manifest['volumes']:
        path = root / safe_relative(volume['name'])
        if path.stat().st_size != volume['bytes'] or digest(path) != volume['sha256']:
            raise ValueError('ciphertext verification failed: '+volume['name'])
        with tempfile.TemporaryFile() as err:
            age = subprocess.Popen(['age', '-d', '-i', str(identity), str(path)], stdout=subprocess.PIPE, stderr=err)
            zstd = subprocess.Popen(['zstd', '-q', '-d', '-c'], stdin=age.stdout, stdout=subprocess.PIPE, stderr=err)
            age.stdout.close()
            try:
                with tarfile.open(fileobj=zstd.stdout, mode='r|') as tar:
                    for member in tar:
                        name = safe_relative(member.name)
                        if not member.isfile():
                            raise ValueError('unsupported archive member')
                        stream = tar.extractfile(member)
                        if str(name) == 'index.json':
                            if index is not None or member.size > 128 * 1024 * 1024:
                                raise ValueError('invalid index')
                            index = json.load(stream)
                            continue
                        if len(name.parts) != 2 or name.parts[0] != 'objects':
                            raise ValueError('unexpected object path')
                        sha = name.parts[1]
                        if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha) or sha in objects:
                            raise ValueError('invalid or duplicate object')
                        h, size = hashlib.sha256(), 0
                        target = (output / '.objects' / sha).open('xb') if output else None
                        try:
                            for block in iter(lambda: stream.read(BLOCK), b''):
                                h.update(block)
                                size += len(block)
                                if target:
                                    target.write(block)
                        finally:
                            if target:
                                target.close()
                        if h.hexdigest() != sha:
                            raise ValueError('object hash mismatch')
                        objects[sha] = size
                # Drain the complete authenticated age stream, including its final tag.
                while zstd.stdout.read(BLOCK):
                    pass
                zstd.stdout.close()
                if zstd.wait() or age.wait():
                    raise ValueError('decryption/decompression authentication failed')
            except BaseException:
                for process in [zstd, age]:
                    if process.poll() is None:
                        process.terminate()
                    process.wait()
                raise
        print(json.dumps({'verified': volume['name']}), flush=True)
    if index is None or len(objects) != manifest['unique_objects']:
        raise ValueError('missing index or objects')
    for record in index['files']:
        path = safe_relative(record['logical_path'])
        if objects.get(record['sha256']) != record['bytes']:
            raise ValueError('file mapping mismatch')
        if output:
            target = output / path
            target.parent.mkdir(parents=True, exist_ok=True)
            # Each logical path is independent; never expose secret material through a symlink.
            os.link(output / '.objects' / record['sha256'], target)
    if len(index['files']) != manifest['logical_files']:
        raise ValueError('file count mismatch')
    if output:
        (output / 'restoration-index.json').write_text(json.dumps(index, indent=2)+'\n')
    return dict(verified=True, logical_files=len(index['files']), unique_objects=len(objects),
                object_bytes=sum(objects.values()), materialized=bool(output))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    create_parser = sub.add_parser('create')
    create_parser.add_argument('--selection', required=True, type=Path)
    create_parser.add_argument('--output', required=True, type=Path)
    create_parser.add_argument('--recipient', required=True)
    create_parser.add_argument('--volume-bytes', type=int, default=768*1024*1024)
    for mode in ['verify', 'restore']:
        p = sub.add_parser(mode)
        p.add_argument('--manifest', required=True, type=Path)
        p.add_argument('--identity', required=True, type=Path)
        if mode == 'restore':
            p.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.mode == 'create':
        result = create(args.selection, args.output, args.recipient, args.volume_bytes)
    else:
        result = verify_or_restore(args.manifest, args.identity, getattr(args, 'output', None))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
