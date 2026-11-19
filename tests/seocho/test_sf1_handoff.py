"""Exercise the offline handoff CLI using synthetic, nonprivate ZIPs."""

import hashlib
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmarks/validate_sf1_handoff.py"
PRIVATE = "SYNTHETIC_PRIVATE_SENTINEL"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundle() -> dict:
    dataset = {"dataset_id": "synthetic", "workspace": "test", "snapshot_sha256": "a" * 64}
    query = b"RETURN 1\r\n"
    return {
        "baseline.json": {"freeze_id": "sf1-vllm-v1", "workload": {
            **dataset, "prompts": 32, "tools": 8, "argument_contracts": 16},
            "model": {"dtype": "bfloat16", "tensor_parallel_size": 1, "source": PRIVATE},
            "generation": {"enable_thinking": False}},
        "config/dataset.json": dataset.copy(),
        "config/schemas.json": [{"type": "function", "function": {"name": f"t{i}",
            "parameters": {"type": "object"}, "description": PRIVATE}} for i in range(8)],
        "config/requests.json": [{"id": f"r{i}", "variant_id": f"v{i // 2}", "tool": f"t{i // 2 % 8}",
            "arguments": {"value": i // 2}, "prompt": f"{PRIVATE}{i}", "expected": [],
            "dataset_id": "synthetic", "query_sha256": sha(query)} for i in range(32)],
        "queries/synthetic.cypher": query, "AGENT_PROMPT.md": PRIVATE.encode(),
        "HANDOFF.md": b"synthetic", "neo4j.conf.historical": b"synthetic",
        "validation.json": b"untrusted auxiliary evidence",
    }


def write_zip(path: Path, files: dict, mutation: str = "") -> None:
    encoded = {k: v if isinstance(v, bytes) else json.dumps(v).encode() for k, v in files.items()}
    hashes = {k: sha(v) for k, v in encoded.items() if k != "validation.json"}
    if mutation == "hash":
        hashes["baseline.json"] = "0" * 64
    if mutation == "coverage":
        del hashes["AGENT_PROMPT.md"]
    encoded["manifest.json"] = json.dumps({"freeze_id": "sf1-vllm-v1", "files": hashes}).encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in encoded.items():
            archive.writestr("sf1-vllm-v1/" + name, value)
        if mutation == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr("sf1-vllm-v1/HANDOFF.md", b"duplicate")
        if mutation == "symlink":
            info = zipfile.ZipInfo("sf1-vllm-v1/link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, PRIVATE)


def run_cli(path: Path, *args: str) -> tuple[int, dict]:
    process = subprocess.run([sys.executable, str(SCRIPT), str(path), *args],
                             capture_output=True, text=True, timeout=10)
    assert process.stderr == ""
    assert PRIVATE not in process.stdout
    return process.returncode, json.loads(process.stdout)


def test_accept_and_external_digest(tmp_path: Path) -> None:
    path = tmp_path / (PRIVATE + ".zip")
    write_zip(path, bundle())
    code, result = run_cli(path, "--expected-archive-sha256", sha(path.read_bytes()))
    assert code == 0 and result["status"] == "STATIC_PASS_NOT_RUNTIME"
    assert (result["requests"], result["variants"], result["tools"]) == (32, 16, 8)
    assert result["query_byte_sha256_matches"] == 32
    assert result["query_text_sha256_matches"] == 0
    assert result["query_binding_status"] == "HASH_MATCH_ONLY"
    assert result["query_sha256_unmatched"] == 0
    assert "QUERY_SEMANTIC_BINDING_NOT_VALIDATED" in result["gaps"]
    assert result["historical_declared_dtype_bfloat16"] is True
    assert result["target_precision_status"] == "NOT_VALIDATED"
    assert result["external_archive_sha256_verified"] is True
    assert run_cli(path, "--expected-archive-sha256", "0" * 64)[0] == 1
    assert run_cli(path, "--expected-archive-sha256", PRIVATE)[0] == 2
    assert run_cli(path, "--" + PRIVATE)[0] == 2


def test_query_provenance_is_a_gap_not_corruption(tmp_path: Path) -> None:
    files = bundle()
    for i, request in enumerate(files["config/requests.json"]):
        request["query_sha256"] = sha(b"RETURN 1") if i < 20 else sha(files["AGENT_PROMPT.md"])
    path = tmp_path / "input.zip"
    write_zip(path, files)
    code, result = run_cli(path)
    assert code == 0 and result["status"] == "STATIC_PASS_NOT_RUNTIME"
    assert result["query_byte_sha256_matches"] == 0
    assert result["query_text_sha256_matches"] == 20
    assert result["query_sha256_unmatched"] == 12
    assert result["query_binding_status"] == "UNVERIFIED_QUERY_BINDING"
    assert "UNVERIFIED_QUERY_BINDING" in result["gaps"]


@pytest.mark.parametrize("case,error", [
    ("hash", "HASH_MISMATCH"), ("coverage", "MANIFEST_COVERAGE"),
    ("duplicate", "DUPLICATE_MEMBER"), ("symlink", "UNSAFE_MEMBER"),
    ("id", "DUPLICATE_IDENTITY"), ("dataset", "DATASET_MISMATCH"),
    ("query", "INVALID_REQUESTS"), ("pair", "INVALID_PAIR"),
    ("schema", "INVALID_SCHEMAS"), ("tool", "UNKNOWN_TOOL"),
    ("json", "INVALID_JSON"), ("path", "UNSAFE_PATH"),
    ("oversize", "SIZE_LIMIT"), ("missing", "MISSING_REQUIRED"),
    ("pair_types", "INVALID_PAIR"), ("json_keys", "INVALID_JSON"),
    ("dir_path", "UNSAFE_PATH"), ("total_size", "SIZE_LIMIT"),
    ("member_count", "SIZE_LIMIT"), ("request_dataset", "DATASET_MISMATCH"),
])
def test_reject_without_sensitive_output(tmp_path: Path, case: str, error: str) -> None:
    files = bundle()
    requests = files["config/requests.json"]
    if case == "id":
        requests[2]["id"] = requests[0]["id"]
    elif case == "dataset":
        files["baseline.json"]["workload"]["workspace"] = PRIVATE
    elif case == "query":
        requests[0]["query_sha256"] = PRIVATE
    elif case == "pair":
        requests[1]["expected"] = [PRIVATE]
    elif case == "schema":
        files["config/schemas.json"][1] = files["config/schemas.json"][0]
    elif case == "tool":
        requests[0]["tool"] = PRIVATE
    elif case == "json":
        files["config/requests.json"] = ("{" + PRIVATE).encode()
    elif case == "path":
        files["../" + PRIVATE] = b"private"
    elif case == "oversize":
        files[PRIVATE] = b"x" * (1024 * 1024 + 1)
    elif case == "missing":
        del files["baseline.json"]
    elif case == "pair_types":
        requests[0]["expected"], requests[1]["expected"] = [1], [True]
    elif case == "json_keys":
        files["baseline.json"] = ('{"private":"' + PRIVATE + '","private":null}').encode()
    elif case == "dir_path":
        files["private//"] = b""
    elif case == "total_size":
        files.update({f"padding{i}": b"x" * 1024 * 1024 for i in range(8)})
    elif case == "member_count":
        files.update({f"padding{i}": b"" for i in range(128)})
    elif case == "request_dataset":
        requests[0]["dataset_id"] = PRIVATE
    path = tmp_path / (PRIVATE + ".zip")
    write_zip(path, files, case)
    code, result = run_cli(path)
    assert code == 1 and result["status"] == "INVALID"
    assert result["error_code"] == error


def test_io_and_zip_errors_are_sanitized(tmp_path: Path) -> None:
    path = tmp_path / PRIVATE
    assert run_cli(path) == (1, {"status": "INVALID", "error_code": "INVALID_INPUT"})
    path.write_bytes(PRIVATE.encode())
    code, result = run_cli(path)
    assert code == 1 and result["error_code"] == "INVALID_INPUT"
    path.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    assert run_cli(path)[1]["error_code"] == "SIZE_LIMIT"


def test_attachments_are_never_extracted_or_executed(tmp_path: Path) -> None:
    files = bundle()
    files[PRIVATE + ".py"] = b"raise RuntimeError('attachment executed')"
    path = tmp_path / "input.zip"
    write_zip(path, files)
    assert run_cli(path)[0] == 0
    assert list(tmp_path.iterdir()) == [path]


def test_reject_duplicate_argument_contracts_between_variants(tmp_path: Path) -> None:
    files = bundle()
    requests = files['config/requests.json']
    requests[16]['arguments'] = requests[0]['arguments'].copy()
    requests[17]['arguments'] = requests[0]['arguments'].copy()
    path = tmp_path / 'duplicate-contract.zip'
    write_zip(path, files)
    code, result = run_cli(path)
    assert code == 1
    assert result['error_code'] == 'DUPLICATE_ARGUMENT_CONTRACT'
