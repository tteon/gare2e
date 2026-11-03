"""Exercise real local age/zstd round trips, never external research services."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('gug_preservation', ROOT /
    'examples/graphcot_optimization/preservation/restore_research_backup.py')
BACKUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKUP)


@unittest.skipUnless(shutil.which('age') and shutil.which('zstd'), 'Optional local age/zstd tools required')
class PreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / 'test.agekey'
        subprocess.run(['age-keygen', '-o', str(self.key)], check=True, capture_output=True)
        self.recipient = subprocess.check_output(['age-keygen', '-y', str(self.key)], text=True).strip()

    def selection(self, logical: str = 'local/result.json') -> Path:
        source = self.root / 'source.json'
        source.write_text('{"correct":1}\n')
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        record = dict(logical_path=logical, physical_path=str(source), bytes=source.stat().st_size, sha256=sha)
        path = self.root / 'selection.json'
        path.write_text(json.dumps({'files': [record, {**record, 'logical_path': 'remote/result.json'}]}))
        return path

    def test_authenticated_roundtrip_dedup_and_corruption(self) -> None:
        summary = BACKUP.create(self.selection(), self.root/'encrypted', self.recipient, 1024)
        manifest = self.root/'encrypted/BACKUP-MANIFEST.json'
        proof = BACKUP.verify_or_restore(manifest, self.key, self.root/'restored')
        self.assertEqual(proof['logical_files'], 2)
        self.assertEqual(proof['unique_objects'], 1)
        self.assertEqual((self.root/'restored/local/result.json').read_bytes(),
                         (self.root/'source.json').read_bytes())
        asset = self.root/'encrypted'/summary['volumes'][0]['name']
        content = asset.read_bytes()
        asset.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        with self.assertRaises(ValueError):
            BACKUP.verify_or_restore(manifest, self.key, None)

    def test_source_path_mapping_cannot_escape_restore_root(self) -> None:
        with self.assertRaises(ValueError):
            BACKUP.create(self.selection('../outside'), self.root/'encrypted', self.recipient, 1024)
        self.assertFalse((self.root/'outside').exists())


if __name__ == '__main__':
    unittest.main()
