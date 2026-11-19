"""Verify the stored GUG collection without extracting or executing its sources."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
from typing import BinaryIO
import zipfile


def stream_digest(stream: BinaryIO) -> str:
    value = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(chunk)
    return value.hexdigest()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return stream_digest(stream)


def local(root: Path, name: str) -> Path:
    path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("unsafe catalog path")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("catalog path escapes collection")
    return path


def verify(root: Path) -> dict[str, int]:
    capture = json.loads((root / "catalog/capture.json").read_text())
    roots = json.loads((root / "catalog/roots.json").read_text())
    files = {}
    for line in (root / "catalog/files.jsonl").read_text().splitlines():
        item = json.loads(line)
        name = item["path"]
        if name in files:
            raise ValueError("duplicate source path")
        local(root, name)
        files[name] = item
    summaries = {}
    with gzip.open(root / "results/summaries.jsonl.gz", "rt") as stream:
        for line in stream:
            item = json.loads(line)
            name = item["source_path"]
            if name in summaries or item["source_sha256"] != files[name]["sha256"]:
                raise ValueError("invalid summary provenance")
            summaries[name] = item["value"]
    if capture["captured_files"] != len(files) or capture["captured_bytes"] != sum(item["bytes"] for item in files.values()):
        raise ValueError("capture file totals mismatch")
    if capture["summary_records"] != len(summaries):
        raise ValueError("capture summary total mismatch")
    packages = json.loads((root / "catalog/packages.json").read_text())
    root_names = [item["source_root"] for item in roots]
    package_names = [item["source_root"] for item in packages]
    if len(set(root_names)) != len(roots) or len(set(package_names)) != len(packages) or set(root_names) != set(package_names):
        raise ValueError("root/package coverage mismatch")
    if capture["source_roots"] != len(roots) or capture["root_directories"] != sum(item["kind"] == "directory" for item in roots) or capture["standalone_files"] != sum(item["kind"] == "file" for item in roots):
        raise ValueError("capture root totals mismatch")
    for item in roots:
        members = [value for name, value in files.items() if name.split("/")[0] == item["source_root"]]
        if len(members) != item["captured_files"] or sum(value["bytes"] for value in members) != item["captured_bytes"]:
            raise ValueError("root source totals mismatch")
    seen = set()
    checked_summaries = set()
    for package in packages:
        path = local(root, package["path"])
        if path.stat().st_size != package["bytes"] or digest(path) != package["sha256"]:
            raise ValueError("package digest mismatch: " + package["path"])
        names = set()
        if package["kind"] == "original-file":
            name = package["source_root"]
            expected = files[name]
            if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
                raise ValueError("standalone source mismatch")
            names.add(name)
        else:
            with tarfile.open(path, "r|gz") as archive:
                for member in archive:
                    name = member.name
                    local(root, name)
                    if not member.isfile() or name in names or name not in files:
                        raise ValueError("invalid archive member")
                    if name.split("/")[0] != package["source_root"]:
                        raise ValueError("member belongs to another source root")
                    expected = files[name]
                    if member.size != expected["bytes"]:
                        raise ValueError("member size mismatch")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("unreadable member")
                    if name in summaries:
                        data = stream.read()
                        actual = hashlib.sha256(data).hexdigest()
                        if json.dumps(json.loads(data), sort_keys=True) != json.dumps(summaries[name], sort_keys=True):
                            raise ValueError("summary differs from original")
                        checked_summaries.add(name)
                    else:
                        actual = stream_digest(stream)
                    if actual != expected["sha256"]:
                        raise ValueError("member digest mismatch")
                    names.add(name)
        if len(names) != package["source_files"] or seen.intersection(names):
            raise ValueError("package coverage mismatch")
        seen.update(names)
    if seen != files.keys() or checked_summaries != summaries.keys():
        raise ValueError("collection coverage mismatch")
    copies = json.loads((root / "catalog/copies.json").read_text())
    if len(copies) != capture["readable_copies"] or len({item["path"] for item in copies}) != len(copies):
        raise ValueError("readable copy coverage mismatch")
    for item in copies:
        original = files[item["source_path"]]
        if item["sha256"] != original["sha256"] or digest(local(root, item["path"])) != original["sha256"]:
            raise ValueError("readable copy mismatch")
    with (root / "results/summaries.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(summaries) or len({row["source_path"] for row in rows}) != len(rows):
        raise ValueError("CSV coverage mismatch")
    for row in rows:
        name = row["source_path"]
        if row["source_sha256"] != files[name]["sha256"]:
            raise ValueError("CSV provenance mismatch")
        if not isinstance(summaries[name], dict):
            continue
        for key, value in summaries[name].items():
            if value is None or isinstance(value, (str, int, float, bool)):
                if row.get(key) != ("" if value is None else str(value)):
                    raise ValueError("CSV scalar differs from original")
    array_values = {(name, str(index)): value for name, values in summaries.items() if isinstance(values, list) for index, value in enumerate(values)}
    if array_values:
        with (root / "results/array-summaries.csv").open(newline="") as stream:
            array_rows = list(csv.DictReader(stream))
        if len(array_rows) != len(array_values) or {(row["source_path"], row["array_index"]) for row in array_rows} != array_values.keys():
            raise ValueError("array CSV coverage mismatch")
        for row in array_rows:
            name = row["source_path"]
            if row["source_sha256"] != files[name]["sha256"]:
                raise ValueError("array CSV provenance mismatch")
            value = array_values[(name, row["array_index"])]
            if isinstance(value, dict):
                for key, scalar in value.items():
                    if scalar is None or isinstance(scalar, (str, int, float, bool)):
                        if row.get(key) != ("" if scalar is None else str(scalar)):
                            raise ValueError("array CSV scalar differs from original")
    handoff = json.loads((root / "handoff/index.json").read_text())
    zip_path = local(root / "handoff", handoff["archive"]["path"])
    if digest(zip_path) != handoff["archive"]["sha256"]:
        raise ValueError("handoff archive mismatch")
    with zipfile.ZipFile(zip_path) as archive:
        actual_names = archive.namelist()
        indexed_names = [item["archive_member"] for item in handoff["members"]]
        copy_names = [item["path"] for item in handoff["members"]]
        if len(set(actual_names)) != len(actual_names) or len(set(indexed_names)) != len(indexed_names) or set(actual_names) != set(indexed_names) or len(set(copy_names)) != len(copy_names):
            raise ValueError("handoff member coverage mismatch")
        for item in handoff["members"]:
            data = archive.read(item["archive_member"])
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("handoff member mismatch")
            if local(root / "handoff", item["path"]).read_bytes() != data:
                raise ValueError("handoff readable copy mismatch")
    return {"packages": len(packages), "source_files": len(files), "summaries": len(summaries), "array_summary_entries": len(array_values), "readable_copies": len(copies), "handoff_members": len(handoff["members"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Downloaded private collection directory")
    args = parser.parse_args()
    try:
        result = verify(args.root)
    except (ValueError, KeyError, OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Verification failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps({"status": "ARCHIVE_BYTES_VERIFIED_NOT_RUNTIME", **result}))


if __name__ == "__main__":
    main()
