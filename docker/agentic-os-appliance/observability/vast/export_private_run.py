"""Server-side S3 publication; no browser/Mac agent or presigned URL service required."""
from __future__ import annotations
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import tarfile
from urllib.parse import urlparse
import uuid


def credential_file() -> str | None:
    """Use only an explicitly approved complete file or environment key pair."""
    name = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or os.environ.get("GARE2E_AWS_CREDENTIALS_FILE")
    if name:
        path = Path(name)
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise ValueError("Approved credential file must be a private regular file")
        profile = os.environ.get("AWS_PROFILE", "default")
        config = configparser.RawConfigParser(); config.read(path)
        if not all(config.get(profile, k, fallback="").strip() for k in ("aws_access_key_id", "aws_secret_access_key")):
            raise ValueError("Approved profile must contain a complete key pair; provider fallback forbidden")
        os.environ["AWS_SHARED_CREDENTIALS_FILE"] = str(path)
        return str(path)
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return None
    raise ValueError("Explicit server credential file or key pair required; no personal profile fallback")


def destination(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/") or parsed.query or parsed.fragment:
        raise ValueError("An approved private s3://bucket/prefix is required")
    return parsed.netloc, parsed.path.strip("/")


def verify_get(client, bucket: str, key: str, digest: str, size: int) -> None:
    result = client.get_object(Bucket=bucket, Key=key)
    h = hashlib.sha256(); count = 0
    with result["Body"] as body:
        for block in iter(lambda: body.read(1024 * 1024), b""):
            h.update(block); count += len(block)
    if h.hexdigest() != digest or count != size:
        raise ValueError("S3 GET size/SHA mismatch; local staging retained")


def preflight(client, uri: str) -> dict:
    bucket, prefix = destination(uri)
    key = prefix + "/preflight/" + uuid.uuid4().hex + ".json"
    body = json.dumps({"scope": "storage gate, not benchmark evidence", "nonce": uuid.uuid4().hex}).encode()
    digest = hashlib.sha256(body).hexdigest()
    client.put_object(Bucket=bucket, Key=key, Body=body, Metadata={"sha256": digest})
    verify_get(client, bucket, key, digest, len(body))
    return {"status": "verified", "s3_uri": f"s3://{bucket}/{key}", "sha256": digest, "bytes": len(body)}


def publish(client, run: Path, output: Path, uri: str) -> dict:
    bucket, prefix = destination(uri)
    if run.is_symlink() or not run.is_dir() or output.exists() or output.resolve().is_relative_to(run.resolve()):
        raise ValueError("Existing run and fresh staging archive outside that run required")
    files = sorted(run.rglob("*"))
    if any(p.is_symlink() for p in files):
        raise ValueError("Symlinks are not allowed")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for p in files:
                if p.is_file(): archive.add(p, arcname=str(p.relative_to(run)), recursive=False)
    h = hashlib.sha256()
    with output.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    digest = h.hexdigest(); size = output.stat().st_size
    key = prefix + "/runs/" + run.name + "/" + output.name
    receipt = {"run_id": run.name, "s3_uri": f"s3://{bucket}/{key}", "sha256": digest, "bytes": size,
               "status": "pending", "get_sha_verified": False, "local_staging_retained": True}
    try:
        # One immutable key per run; no silent overwrite of an earlier trial.
        with output.open("rb") as f:
            client.put_object(Bucket=bucket, Key=key, Body=f, Metadata={"sha256": digest}, IfNoneMatch="*")
        verify_get(client, bucket, key, digest, size)
        receipt.update(status="verified", get_sha_verified=True)
    except Exception as error:
        receipt.update(status="failed", error_class=type(error).__name__)
    output.with_suffix(output.suffix + ".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--s3-uri", required=True)
    p.add_argument("--preflight", action="store_true")
    p.add_argument("--run", type=Path); p.add_argument("--output", type=Path)
    a = p.parse_args()
    destination(a.s3_uri)
    if not a.preflight and (not a.run or not a.output): p.error("--run and --output are required")
    import boto3
    from botocore.config import Config
    # Operator-managed server role/credential chain only. No personal fallback.
    approved_file = credential_file()
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        session = boto3.Session(aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                                aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                                aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
                                region_name=os.environ.get("AWS_REGION"))
    else:
        config = configparser.RawConfigParser(); config.read(approved_file)
        profile = os.environ.get("AWS_PROFILE", "default")
        session = boto3.Session(aws_access_key_id=config.get(profile, "aws_access_key_id"),
                                aws_secret_access_key=config.get(profile, "aws_secret_access_key"),
                                aws_session_token=config.get(profile, "aws_session_token", fallback=None),
                                region_name=os.environ.get("AWS_REGION"))
    client = session.client("s3", config=Config(connect_timeout=10, read_timeout=120, retries={"max_attempts": 2}))
    result = preflight(client, a.s3_uri) if a.preflight else publish(client, a.run, a.output, a.s3_uri)
    print(json.dumps(result))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
