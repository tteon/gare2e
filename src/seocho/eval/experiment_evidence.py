"""Reviewed query/evaluation evidence for the existing immutable experiment viewer."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

QUERY_SCHEMA = "gare2e.query_attempt.v1"
QUALITY_FIELDS = (
    "answer_correctness",
    "evidence_recall",
    "evidence_precision",
    "faithfulness",
    "required_entity_coverage",
    "required_relationship_coverage",
)


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        val = float(value)
        return val if math.isfinite(val) and val >= 0 else None
    except (ValueError, TypeError):
        return None


def label(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_@.:/() +\[\],=-]{1,160}", value):
        raise ValueError(
            "Evidence labels must be bounded identifiers, not raw payloads"
        )
    return value


def hex_id(value: Any, length: int) -> str | None:
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(rf"[0-9a-f]{{{length}}}", value)
        and int(value, 16)
        else None
    )


def plan_evidence(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    operators = []
    for op in plan.get("operators", []):
        item = {
            k: number(op.get(k))
            for k in (
                "estimated_rows",
                "actual_rows",
                "db_hits",
                "page_cache_hits",
                "page_cache_misses",
                "cardinality_error",
                "memory_bytes",
                "time_ns",
            )
        }
        if item["actual_rows"] is not None and item["estimated_rows"] is not None:
            item["cardinality_error"] = abs(
                math.log((item["actual_rows"] + 1) / (item["estimated_rows"] + 1))
            )
        item.update(
            {k: label(op.get(k)) for k in ("operator", "operator_id", "parent_id")}
        )
        operators.append(item)
    # Recompute totals; legacy aggregate zero is not evidence of collected counters.
    totals = {}
    coverage = {}
    for key in ("db_hits", "page_cache_hits", "page_cache_misses"):
        vals = [op[key] for op in operators]
        coverage[key] = {
            "known": sum(v is not None for v in vals),
            "operators": len(vals),
        }
        totals["total_" + key] = (
            sum(vals) if vals and all(v is not None for v in vals) else None
        )
    return {
        "fingerprint": label(plan.get("fingerprint")),
        "operators": operators,
        "findings": [label(f) for f in plan.get("findings", [])],
        "counter_coverage": coverage,
        **totals,
    }


def query_record(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version") != QUERY_SCHEMA:
        raise ValueError("Unknown query evidence version")
    if raw.get("scope") not in {
        "timed_execution",
        "diagnostic_replay",
        "auxiliary_query",
    }:
        raise ValueError("Query scope is required")
    if raw.get("outcome") not in {"ok", "error", "reused"}:
        raise ValueError("Unknown query outcome")
    if not raw.get("query_id"):
        raise ValueError("Query identity is required")
    result = {
        k: label(raw.get(k))
        for k in (
            "query_id",
            "workspace_id",
            "run_id",
            "parent_query_id",
            "session_id",
            "tool_call_id",
            "turn_id",
            "snapshot_id",
            "mode",
            "scope",
            "outcome",
            "error_class",
            "reused_from",
            "clock_domain",
        )
    }
    result["schema_version"] = QUERY_SCHEMA
    result.update(
        {
            k: hex_id(raw.get(k), n)
            for k, n in (
                ("trace_id", 32),
                ("span_id", 16),
                ("query_digest", 64),
                ("parameter_digest", 64),
            )
        }
    )
    for key in ("started_unix_ns", "started_monotonic_ns", "ended_monotonic_ns"):
        val = raw.get(key)
        if val is not None and (
            isinstance(val, bool) or not isinstance(val, int) or val < 0
        ):
            raise ValueError("Clock timestamps must be non-negative integers")
        result[key] = val
    result.update(
        {k: number(raw.get(k)) for k in ("latency_ms", "db_queries_executed")}
    )
    receipt = raw.get("receipt") or {}
    result["receipt"] = {
        k: number(receipt.get(k))
        for k in (
            "rows",
            "result_bytes",
            "result_tokens",
            "retained_tokens",
            "graph_properties",
            "validator_ms",
            "server_available_ms",
            "server_consumed_ms",
            "serialize_ms",
        )
    }
    result["receipt"]["truncated"] = (
        receipt.get("truncated") if isinstance(receipt.get("truncated"), bool) else None
    )
    result["plan"] = plan_evidence(raw.get("plan"))
    return result


def attach_evidence(
    source: Path, artifacts: dict[str, dict[str, Any]], conditions: list[dict[str, Any]]
) -> dict[str, str]:
    """Join by exact trace ID or explicit pass/platform task ID; reject ambiguous evidence."""
    import hashlib

    hashes = {}
    lookup = {c["label"]: c["id"] for c in conditions}
    seen: set[str] = set()
    for filename in ("queries.jsonl", "quality.jsonl"):
        path = source / filename
        if not path.exists():
            continue
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            candidates = []
            for cid, artifact in artifacts.items():
                if raw.get("pass") and lookup.get(raw["pass"]) != cid:
                    continue
                for task in artifact["tasks"]:
                    by_trace = (
                        hex_id(raw.get("trace_id"), 32) is not None
                        and raw["trace_id"] == task["trace_id"]
                    )
                    by_id = raw.get("pass") and raw.get("task_id") == task["id"]
                    if by_trace or by_id:
                        candidates.append(task)
            if len(candidates) != 1:
                raise ValueError(
                    "Evidence must join exactly one task; no heuristic case/time joins"
                )
            task = candidates[0]
            if filename == "queries.jsonl":
                record = query_record(raw)
                if record["query_id"] in seen:
                    raise ValueError("Duplicate query ID")
                seen.add(record["query_id"])
                task.setdefault("queries", []).append(record)
            else:
                if "quality_evaluation" in task or not raw.get("evaluator_revision"):
                    raise ValueError("Quality needs one versioned evaluation per task")
                scores = {k: number(raw.get(k)) for k in QUALITY_FIELDS}
                if any(
                    raw.get(k) is not None and (v is None or v > 1)
                    for k, v in scores.items()
                ):
                    raise ValueError("Quality scores must be in [0,1]")
                task["quality_evaluation"] = {
                    "evaluator_revision": label(raw["evaluator_revision"]),
                    "evidence_digest": hex_id(raw.get("evidence_digest"), 64),
                    "scores": scores,
                }
    for artifact in artifacts.values():
        for task in artifact["tasks"]:
            queries = task.setdefault("queries", [])
            by_id = {q["query_id"]: q for q in queries}
            for q in queries:
                if q["parent_query_id"] and q["parent_query_id"] not in by_id:
                    raise ValueError("Missing parent query evidence")
                if q["reused_from"] and q["reused_from"] not in by_id:
                    raise ValueError("Missing reuse source evidence")
                if q["outcome"] == "reused":
                    source_query = by_id.get(q["reused_from"])
                    if (
                        not source_query
                        or source_query["outcome"] != "ok"
                        or q["db_queries_executed"] != 0
                        or q["mode"] != "exact_result_reuse"
                        or any(
                            not q[k] or q[k] != source_query[k]
                            for k in (
                                "session_id",
                                "snapshot_id",
                                "query_digest",
                                "parameter_digest",
                            )
                        )
                    ):
                        raise ValueError(
                            "Reuse needs a successful matching source and zero submissions"
                        )
            queries.sort(key=lambda q: q["started_monotonic_ns"] or 0)
            anchor = task.get("started_unix_s")
            if anchor and task.get("clock_source") != "unknown_relative_only":
                for q in queries:
                    if q["started_unix_ns"] is None or q["latency_ms"] is None:
                        continue
                    lane = (
                        "PROFILE replay"
                        if q["scope"] == "diagnostic_replay"
                        else "Result reuse"
                        if q["outcome"] == "reused"
                        else "Query attempt"
                    )
                    task["events"].append(
                        {
                            "lane": lane,
                            "name": q["query_id"],
                            "start_us": (q["started_unix_ns"] / 1e9 - anchor) * 1e6,
                            "duration_us": q["latency_ms"] * 1000,
                            "evidence": {
                                k: q[k]
                                for k in (
                                    "query_id",
                                    "scope",
                                    "outcome",
                                    "db_queries_executed",
                                    "parent_query_id",
                                )
                            },
                        }
                    )
                task["events"].sort(key=lambda event: event["start_us"])
            task["evidence_insights"] = task_insights(task)
    return hashes


def task_insights(task: dict[str, Any]) -> list[dict[str, Any]]:
    queries = task.get("queries", [])
    items = []

    def add(kind: str, title: str, detail: str, ids: list[str]) -> None:
        items.append(
            {"status": kind, "title": title, "detail": detail, "query_ids": ids}
        )

    if not queries:
        add(
            "missing",
            "Query 계측 미수집",
            "기존 tool 구간은 볼 수 있지만 DB invocation·plan·reuse를 추정하지 않습니다.",
            [],
        )
    repeats: dict[tuple[str, str], list[str]] = {}
    for q in queries:
        if (
            q["scope"] == "timed_execution"
            and q["outcome"] == "ok"
            and q["query_digest"]
            and q["parameter_digest"]
        ):
            repeats.setdefault((q["query_digest"], q["parameter_digest"]), []).append(
                q["query_id"]
            )
    repeated_ids = [qid for ids in repeats.values() if len(ids) > 1 for qid in ids]
    if repeated_ids:
        add(
            "observed",
            "동일 query·parameter 재호출",
            "동일 session/snapshot인지 확인한 뒤 reuse 후보로 검토하세요. 반복이 항상 불필요한 것은 아닙니다.",
            repeated_ids,
        )
    failed = [q["query_id"] for q in queries if q["outcome"] == "error"]
    if failed:
        add(
            "observed",
            "실패한 query attempt",
            "오류 종류와 DB 제출 여부를 확인하세요. 실제 DB 실패와 제출 전 거절은 다릅니다.",
            failed,
        )
    reused = [q["query_id"] for q in queries if q["outcome"] == "reused"]
    if reused:
        add(
            "observed",
            "기존 결과 재사용",
            "DB 실행 생략입니다. Agent tool 호출 또는 LLM turn 감소를 뜻하지 않습니다.",
            reused,
        )
    missing = [
        q["query_id"]
        for q in queries
        if q["plan"]
        and any(
            q["plan"][k] is None
            for k in (
                "total_db_hits",
                "total_page_cache_hits",
                "total_page_cache_misses",
            )
        )
    ]
    if missing:
        add(
            "missing",
            "Plan 카운터 일부 미수집",
            "빈 카운터는 0이 아닙니다. 과거 집계 대신 operator 원본 범위를 확인하세요.",
            missing,
        )
    diagnostics = [q["query_id"] for q in queries if q["scope"] == "diagnostic_replay"]
    if diagnostics:
        add(
            "limitation",
            "별도 PROFILE 실행",
            "이 plan의 cache·시간은 replay 관측입니다. 원래 timed query의 값으로 사용하지 않습니다.",
            diagnostics,
        )
    if not task.get("quality_evaluation"):
        add(
            "missing",
            "답변 품질 미평가",
            "실행 완료는 정답 판정이 아닙니다. evaluator revision과 evidence score가 필요합니다.",
            [],
        )
    add(
        "hypothesis",
        "DB → GPU 인과 연결은 미검증",
        "DB Hits, page miss, storage I/O, GPU idle은 각각의 관측과 통제 실험이 필요합니다.",
        [],
    )
    return items


def structured_logs(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp_unix_ns": q["started_unix_ns"],
            "trace_id": q["trace_id"],
            "span_id": q["span_id"],
            "severity": "ERROR" if q["outcome"] == "error" else "INFO",
            "event": "graph.query." + q["outcome"],
            "query_id": q["query_id"],
            "graph_workspace_id": q.get("workspace_id"),
            "run_id": q.get("run_id"),
            "tool_call_id": q["tool_call_id"],
            "scope": q["scope"],
            "db_queries_executed": q["db_queries_executed"],
            "duration_ms": q["latency_ms"],
            "error_class": q["error_class"],
        }
        for q in task.get("queries", [])
    ]


def otlp_logs(task: dict[str, Any]) -> dict[str, Any]:
    records = []
    for log in structured_logs(task):
        if log["timestamp_unix_ns"] is None:
            continue
        row: dict[str, Any] = {
            "timeUnixNano": str(log["timestamp_unix_ns"]),
            "severityText": log["severity"],
            "severityNumber": 17 if log["severity"] == "ERROR" else 9,
            "body": {"stringValue": log["event"]},
            "attributes": [
                {"key": "gare2e." + k, "value": {"stringValue": str(v)}}
                for k, v in log.items()
                if v is not None
                and k
                not in {"trace_id", "span_id", "timestamp_unix_ns", "event", "severity"}
            ],
        }
        if log["trace_id"]:
            row["traceId"] = log["trace_id"]
            if log["span_id"]:
                row["spanId"] = log["span_id"]
        records.append(row)
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "gare2e-graph"},
                        }
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "gare2e.query_evidence", "version": "1"},
                        "logRecords": records,
                    }
                ],
            }
        ]
    }
