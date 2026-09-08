from __future__ import annotations

import pytest

from seocho.eval.agentic_os_artifacts import (
    ArtifactPrivacyError,
    Correlation,
    append_jsonl,
    append_private_jsonl,
    assert_content_free,
    scan_for_content_leak,
    write_manifest,
)


def test_manifest_is_immutable_and_receipts_require_correlation(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    assert write_manifest(manifest, {"arm": "B0", "model": "qwen"})
    assert write_manifest(manifest, {"arm": "B0", "model": "qwen"})
    with pytest.raises(FileExistsError):
        write_manifest(manifest, {"arm": "S1", "model": "qwen"})
    receipt = Correlation("run", "case", "B0", 0, "none", "agent").receipt_fields()
    append_jsonl(tmp_path / "receipts.jsonl", {**receipt, "tool_calls": 1})
    with pytest.raises(ValueError):
        append_jsonl(tmp_path / "receipts.jsonl", {"run_id": "missing-fields"})


def test_private_jsonl_keeps_correlation_without_joining_telemetry(tmp_path) -> None:
    correlation = Correlation("run", "case", "B0", 0, "warm", "agent").receipt_fields()
    target = tmp_path / "predictions.private.jsonl"

    append_private_jsonl(target, {**correlation, "answer": "private answer"})

    assert '"run_id": "run"' in target.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        append_private_jsonl(target, {"answer": "missing join keys"})


def test_privacy_sentinel_detects_leaked_text(tmp_path) -> None:
    target = tmp_path / "receipt.jsonl"
    target.write_text('{"ok":true}\n', encoding="utf-8")
    assert scan_for_content_leak([target], ["PRIVATE_SENTINEL"]) == {}
    target.write_text('PRIVATE_SENTINEL\n', encoding="utf-8")
    assert str(target) in scan_for_content_leak([target], ["PRIVATE_SENTINEL"])
    with pytest.raises(ArtifactPrivacyError, match="content sentinel"):
        assert_content_free([target], ["PRIVATE_SENTINEL"])


def test_privacy_validation_returns_receipt_for_clean_telemetry(tmp_path) -> None:
    target = tmp_path / "receipt.jsonl"
    target.write_text('{"tool_calls": 1}\n', encoding="utf-8")
    report = assert_content_free([target], iter(["PROMPT_CANARY"]))
    assert report["passed"] is True
    assert report["sentinel_count"] == 1
