"""Offline SF1 ZIP integrity checks, without extraction or attachment execution.

Static checks do not establish DB/index/model readiness, FP8/lossless behavior,
expected-answer correctness, performance, or authorization to run the baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = "sf1-vllm-v1/"
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_MEMBER = 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_MEMBERS = 128
REQUIRED = {"baseline.json", "config/dataset.json", "config/requests.json",
            "config/schemas.json", "AGENT_PROMPT.md", "HANDOFF.md", "neo4j.conf.historical"}
PAIR_FIELDS = ("tool", "arguments", "expected", "query_sha256", "dataset_id")


class Invalid(ValueError):
    pass


def need(condition: bool, code: str) -> None:
    if not condition:
        raise Invalid(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        need(key not in result, "INVALID_JSON")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise Invalid("INVALID_JSON")


def load_json(data: bytes) -> object:
    try:
        return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Invalid("INVALID_JSON") from None


def read_bundle(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        need(len(members) <= MAX_MEMBERS, "SIZE_LIMIT")
        names, total = set(), 0
        for member in members:
            name = member.orig_filename
            parts = (name[:-1] if member.is_dir() else name).split("/")
            need(name == member.filename and "\\" not in name and ":" not in name
                 and all(p not in {"", ".", ".."} for p in parts)
                 and (name == ROOT or name.startswith(ROOT)), "UNSAFE_PATH")
            need(name not in names, "DUPLICATE_MEMBER")
            names.add(name)
            mode = stat.S_IFMT(member.external_attr >> 16)
            need(mode in {0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG}
                 and not member.flag_bits & 1, "UNSAFE_MEMBER")
            total += member.file_size
            need(member.file_size <= MAX_MEMBER and total <= MAX_TOTAL, "SIZE_LIMIT")
        files = {}
        for member in members:
            if not member.is_dir():
                with archive.open(member) as stream:
                    content = stream.read(MAX_MEMBER + 1)
                need(len(content) == member.file_size and len(content) <= MAX_MEMBER, "SIZE_LIMIT")
                files[member.filename[len(ROOT):]] = content
        return files


def validate(files: dict[str, bytes]) -> dict:
    need(REQUIRED | {"manifest.json"} <= files.keys(), "MISSING_REQUIRED")
    manifest = load_json(files["manifest.json"])
    need(isinstance(manifest, dict) and manifest.get("freeze_id") == "sf1-vllm-v1"
         and isinstance(manifest.get("files"), dict), "INVALID_MANIFEST")
    hashes = manifest["files"]
    need(hashes.keys() == files.keys() - {"manifest.json", "validation.json"}, "MANIFEST_COVERAGE")
    need(all(digest(value) and sha(files[name]) == value for name, value in hashes.items()), "HASH_MISMATCH")
    queries = {name for name in hashes if name.startswith("queries/") and name.endswith(".cypher")}
    need(bool(queries), "MISSING_QUERIES")
    byte_hashes = {hashes[name] for name in queries}
    text_hashes = {sha(io.TextIOWrapper(io.BytesIO(files[name]), encoding="utf-8").read().strip().encode("utf-8"))
                   for name in queries}
    baseline, dataset, requests, schemas = [load_json(files[name]) for name in
        ("baseline.json", "config/dataset.json", "config/requests.json", "config/schemas.json")]
    need(isinstance(baseline, dict) and baseline.get("freeze_id") == "sf1-vllm-v1"
         and isinstance(dataset, dict), "INVALID_DATASET")
    need(all(isinstance(dataset.get(k), str) and dataset[k] for k in ("dataset_id", "workspace"))
         and digest(dataset.get("snapshot_sha256")), "INVALID_DATASET")
    workload = baseline.get("workload")
    need(isinstance(workload, dict) and all(workload.get(k) == dataset[k]
         for k in ("dataset_id", "workspace", "snapshot_sha256")), "DATASET_MISMATCH")
    need(all(type(workload.get(k)) is int and workload[k] == n for k, n in
             (("prompts", 32), ("tools", 8), ("argument_contracts", 16))), "INVALID_COUNTS")
    need(isinstance(schemas, list) and len(schemas) == 8, "INVALID_SCHEMAS")
    tools = set()
    for schema in schemas:
        need(isinstance(schema, dict) and schema.get("type") == "function"
             and isinstance(schema.get("function"), dict), "INVALID_SCHEMAS")
        function = schema["function"]
        name = function.get("name")
        need(isinstance(name, str) and bool(name) and name not in tools
             and isinstance(function.get("parameters"), dict), "INVALID_SCHEMAS")
        tools.add(name)
    need(isinstance(requests, list) and len(requests) == 32, "INVALID_REQUESTS")
    variants, identities, ids, used_tools = defaultdict(list), set(), set(), set()
    byte_matches = text_matches = unmatched = 0
    for request in requests:
        need(isinstance(request, dict), "INVALID_REQUESTS")
        need(all(isinstance(request.get(k), str) and request[k]
                 for k in ("id", "variant_id", "tool", "prompt", "dataset_id"))
             and isinstance(request.get("arguments"), dict)
             and isinstance(request.get("expected"), list)
             and digest(request.get("query_sha256")), "INVALID_REQUESTS")
        identity = (request["id"], request["variant_id"])
        need(request["id"] not in ids and identity not in identities, "DUPLICATE_IDENTITY")
        ids.add(request["id"])
        identities.add(identity)
        need(request["dataset_id"] == dataset["dataset_id"], "DATASET_MISMATCH")
        need(request["tool"] in tools, "UNKNOWN_TOOL")
        used_tools.add(request["tool"])
        variants[request["variant_id"]].append(request)
        value = request["query_sha256"]
        byte_matches += value in byte_hashes
        text_matches += value in text_hashes
        unmatched += value not in byte_hashes | text_hashes
    need(used_tools == tools and len(variants) == 16, "INVALID_COUNTS")
    argument_contracts = set()
    for pair in variants.values():
        need(len(pair) == 2, "INVALID_PAIR")
        a, b = pair
        need(a["prompt"] != b["prompt"] and
             json.dumps([a[k] for k in PAIR_FIELDS], sort_keys=True, allow_nan=False) ==
             json.dumps([b[k] for k in PAIR_FIELDS], sort_keys=True, allow_nan=False), "INVALID_PAIR")
        contract = json.dumps([a["tool"], a["arguments"]], sort_keys=True, allow_nan=False)
        need(contract not in argument_contracts, "DUPLICATE_ARGUMENT_CONTRACT")
        argument_contracts.add(contract)
    model, generation = baseline.get("model"), baseline.get("generation")
    need(isinstance(model, dict) and isinstance(generation, dict), "INVALID_BASELINE")
    gaps = ["RUNTIME_NOT_VALIDATED", "EXPECTED_CORRECTNESS_NOT_VALIDATED",
            "QUERY_SEMANTIC_BINDING_NOT_VALIDATED"]
    if unmatched:
        gaps.append("UNVERIFIED_QUERY_BINDING")
    return {"status": "STATIC_PASS_NOT_RUNTIME", "files": len(files), "hashed_files": len(hashes),
            "requests": 32, "variants": 16, "argument_contracts": len(argument_contracts),
            "tools": 8, "queries": len(queries),
            "query_byte_sha256_matches": byte_matches, "query_text_sha256_matches": text_matches,
            "query_sha256_unmatched": unmatched,
            "query_binding_status": "UNVERIFIED_QUERY_BINDING" if unmatched else "HASH_MATCH_ONLY",
            "historical_declared_dtype_bfloat16": model.get("dtype") == "bfloat16",
            "historical_tp1": type(model.get("tensor_parallel_size")) is int and model["tensor_parallel_size"] == 1,
            "historical_thinking_off": generation.get("enable_thinking") is False,
            "target_precision_status": "NOT_VALIDATED",
            "gaps": gaps}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Invalid("INVALID_ARGUMENTS")


def main(argv: list[str] | None = None) -> int:
    result, exit_code = {"status": "INVALID"}, 1
    try:
        parser = Parser(prog="validate_sf1_handoff.py", description=__doc__)
        parser.add_argument("zip", metavar="ZIP")
        parser.add_argument("--expected-archive-sha256")
        try:
            args = parser.parse_args(argv)
            expected = args.expected_archive_sha256
            need(expected is None or digest(expected), "INVALID_ARGUMENTS")
        except Invalid:
            exit_code = 2
            raise
        with Path(args.zip).open("rb") as stream:
            data = stream.read(MAX_ARCHIVE + 1)
        need(len(data) <= MAX_ARCHIVE, "SIZE_LIMIT")
        result["archive_sha256"] = sha(data)
        need(expected is None or expected == result["archive_sha256"], "ARCHIVE_SHA256_MISMATCH")
        result.update(validate(read_bundle(data)))
        result["external_archive_sha256_verified"] = expected is not None
        exit_code = 0
    except Invalid as error:
        result["error_code"] = str(error)
    except Exception:
        result["error_code"] = "INVALID_INPUT"
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
