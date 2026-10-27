"""Publication failure must never become a verified archive receipt."""
import importlib.util
import io
import json
import os
import stat
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'docker/agentic-os-appliance/observability/vast/export_private_run.py'
spec = importlib.util.spec_from_file_location('vast_s3_export', SCRIPT)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

class ObjectStore:
    def __init__(self, corrupt=False, deny=False):
        self.objects = {}; self.corrupt, self.deny = corrupt, deny
    def put_object(self, Bucket, Key, Body, **kwargs):
        if self.deny: raise PermissionError('denied')
        if kwargs.get('IfNoneMatch') == '*' and (Bucket, Key) in self.objects: raise FileExistsError('immutable object')
        self.objects[Bucket, Key] = Body.read() if hasattr(Body, 'read') else Body
    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(b'corrupt' if self.corrupt else self.objects[Bucket, Key])}

def test_preflight_rejects_a_corrupt_get():
    with pytest.raises(ValueError, match='SHA mismatch'):
        export.preflight(ObjectStore(corrupt=True), 's3://test-bucket/test-prefix')

@pytest.mark.parametrize('failure', ['deny', 'corrupt'])
def test_failed_archive_keeps_raw_and_staging(tmp_path, failure):
    run = tmp_path / 'run'; run.mkdir(); (run / 'outcome.json').write_text('{"failed":true}')
    archive = tmp_path / 'staging' / 'run.tar.gz'
    result = export.publish(ObjectStore(**{failure: True}), run, archive, 's3://test-bucket/test-prefix')
    assert result['status'] == 'failed' and not result['get_sha_verified']
    assert archive.exists() and (run / 'outcome.json').exists()
    assert json.loads(archive.with_suffix('.gz.receipt.json').read_text())['status'] == 'failed'

def test_verified_archive_and_no_silent_overwrite(tmp_path):
    run = tmp_path / 'run'; run.mkdir(); (run / 'unknown.json').write_text('{"unknown":true}')
    archive = tmp_path / 'stage' / 'run.tar.gz'; store = ObjectStore()
    result = export.publish(store, run, archive, 's3://test-bucket/test-prefix')
    assert result['get_sha_verified'] and result['status'] == 'verified'
    second = export.publish(store, run, tmp_path / 'stage2' / archive.name, 's3://test-bucket/test-prefix')
    assert second['status'] == 'failed' and second['error_class'] == 'FileExistsError'

def test_symlink_is_not_exported(tmp_path):
    run = tmp_path / 'run'; run.mkdir(); (run / 'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='Symlinks'):
        export.publish(ObjectStore(), run, tmp_path / 'run.tar.gz', 's3://test-bucket/test-prefix')

@pytest.mark.parametrize('uri', ['s3://bucket', 'https://bucket/prefix', 's3://bucket/prefix?key=secret'])
def test_destination_is_explicit_and_private(uri):
    with pytest.raises(ValueError): export.destination(uri)

def test_approved_credential_file_is_mapped(monkeypatch, tmp_path):
    path = tmp_path / 'credentials'; path.write_text('[default]\naws_access_key_id=test-only\naws_secret_access_key=test-only\n'); path.chmod(0o600)
    monkeypatch.delenv('AWS_SHARED_CREDENTIALS_FILE', raising=False)
    monkeypatch.setenv('GARE2E_AWS_CREDENTIALS_FILE', str(path))
    assert export.credential_file() == str(path)
    assert os.environ['AWS_SHARED_CREDENTIALS_FILE'] == str(path)

def test_personal_profile_is_not_implicit_fallback(monkeypatch):
    for name in ['AWS_SHARED_CREDENTIALS_FILE', 'GARE2E_AWS_CREDENTIALS_FILE', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_ROLE_ARN', 'AWS_WEB_IDENTITY_TOKEN_FILE']:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match='no personal profile fallback'): export.credential_file()

def test_empty_profile_cannot_fall_through_to_other_provider(monkeypatch, tmp_path):
    path = tmp_path / 'credentials'; path.write_text('[default]\n'); path.chmod(0o600)
    monkeypatch.setenv('AWS_SHARED_CREDENTIALS_FILE', str(path)); monkeypatch.setenv('AWS_PROFILE', 'default')
    with pytest.raises(ValueError, match='provider fallback forbidden'): export.credential_file()

def test_archive_private_during_creation_even_with_public_umask(tmp_path, monkeypatch):
    run = tmp_path / 'run'; run.mkdir(); (run / 'raw').write_text('private')
    output = tmp_path / 'staging' / 'run.tar.gz'
    original = export.tarfile.open
    def inspect_open(*args, **kwargs):
        assert stat.S_IMODE(os.fstat(kwargs['fileobj'].fileno()).st_mode) == 0o600
        return original(*args, **kwargs)
    monkeypatch.setattr(export.tarfile, 'open', inspect_open)
    old = os.umask(0o022)
    try: assert export.publish(ObjectStore(), run, output, 's3://test/prefix')['status'] == 'verified'
    finally: os.umask(old)

def test_run_symlink_is_rejected(tmp_path):
    real = tmp_path / 'real'; real.mkdir(); link = tmp_path / 'link'; link.symlink_to(real)
    with pytest.raises(ValueError): export.publish(ObjectStore(), link, tmp_path / 'out.tar.gz', 's3://test/prefix')
