"""Synthetic frozen tools only. No private fixtures or live services are read."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from pathlib import Path
from threading import Event, Timer
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest
from neo4j import Query, READ_ACCESS, Record

from seocho.integrations.finbench import (
    ArgumentRelation,
    FrozenQueryExecutor,
    FrozenToolRegistry,
    QueryExecutionRecord,
    QueryPair,
    QueryTemplate,
    StageAdmission,
    StageAdmissionCancelled,
    StageAdmissionRecord,
    StageAdmissionTimeout,
    ToolBinding,
    ordered_rows_equal,
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def template(root: Path, name: str, raw: bytes) -> QueryTemplate:
    (root / (name + ".cypher")).write_bytes(raw)
    text = raw.decode().replace("\r\n", "\n").replace("\r", "\n").strip()
    return QueryTemplate(name, name + ".cypher", digest(raw), digest(text.encode()))


def integer(lo: int, hi: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": lo, "maximum": hi}


def schema(*, selector: bool = False) -> dict[str, Any]:
    props = {
        "node": integer(0, 2**63 - 1),
        "amount": {"type": "number", "minimum": 0, "maximum": 1e15},
        "start": integer(0, 2**63 - 1),
        "end": integer(1, 2**63 - 1),
        "lower": integer(0, 2**63 - 1),
        "upper": integer(0, 2**63 - 1),
        "limit": integer(1, 100),
    }
    if selector:
        props["hops"] = integer(1, 3)
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def arguments(**overrides: Any) -> dict[str, Any]:
    return {
        "node": 7,
        "amount": 1.5,
        "start": 0,
        "end": 20,
        "lower": 1,
        "upper": 9,
        "limit": 10,
        **overrides,
    }


def binding(root: Path, *, selector: bool = False) -> ToolBinding:
    original = template(root, "generic", b" \r\nRETURN $node AS value\r\n ")
    optimized = template(root, "alternative", b"RETURN $node + 0 AS value\n")
    variants = {None: QueryPair(original, optimized)}
    if selector:
        variants = {
            index: QueryPair(
                template(root, f"finite-{index}", f"RETURN {index} AS depth".encode())
            )
            for index in (1, 2, 3)
        }
    return ToolBinding(
        "lookup",
        "Synthetic bounded lookup.",
        schema(selector=selector),
        variants,
        selector="hops" if selector else None,
        relations=(
            ArgumentRelation("start", "end"),
            ArgumentRelation("lower", "upper", "le"),
        ),
    )


def registry(root: Path, tool: ToolBinding | None = None) -> FrozenToolRegistry:
    return FrozenToolRegistry(
        query_root=root, tools=[tool or binding(root)], workspace_id="synthetic-host"
    )


def test_private_file_to_bound_call_is_pinned_and_frozen(tmp_path: Path) -> None:
    tool = binding(tmp_path)
    catalog = registry(tmp_path, tool)
    call = arguments()
    bound = catalog.bind("lookup", call)
    assert bound.query == "RETURN $node AS value"
    assert bound.raw_sha256 != bound.query_sha256
    assert bound.raw_sha256 == tool.variants[None].original.raw_sha256
    assert bound.query_sha256 == digest(bound.query.encode())
    assert bound.arm == "original"
    assert bound.parameters == {**call, "workspace": "synthetic-host"}
    call["node"] = 99
    tool.parameters["properties"]["node"]["maximum"] = 0
    tool.variants.clear()
    exported = catalog.schemas()
    exported[0]["function"]["parameters"]["properties"].clear()
    (tmp_path / "generic.cypher").write_text("RETURN 999", encoding="utf-8")
    again = catalog.bind("lookup", arguments())
    assert again.query == bound.query
    assert again.arguments["node"] == 7
    assert catalog.schemas()[0]["function"]["parameters"] == schema()
    with pytest.raises(TypeError):
        bound.parameters["workspace"] = "model-controlled"
    assert "RETURN" not in repr(bound)


def test_optimized_arm_keeps_original_and_requested_hashes(tmp_path: Path) -> None:
    catalog = registry(tmp_path)
    original = catalog.bind("lookup", arguments())
    optimized = catalog.bind(
        "lookup",
        arguments(),
        arm="optimized",
        expected_original_sha256=original.query_sha256,
    )
    assert optimized.query != original.query
    assert optimized.query_sha256 != original.query_sha256
    assert optimized.original_query_sha256 == original.query_sha256
    assert optimized.original_raw_sha256 == original.raw_sha256
    assert optimized.original_template_id == original.template_id
    assert optimized.expected_original_sha256 == original.query_sha256
    with pytest.raises(ValueError, match="original_query_hash_mismatch"):
        catalog.bind(
            "lookup",
            arguments(),
            arm="optimized",
            expected_original_sha256=optimized.query_sha256,
        )
    with pytest.raises(ValueError, match="query_arm"):
        catalog.bind("lookup", arguments(), arm="other")


@pytest.mark.parametrize(
    "field,value",
    [
        ("node", True),
        ("node", 7.0),
        ("node", "7"),
        ("node", -1),
        ("node", 2**63),
        ("node", 10**1000),
        ("amount", True),
        ("amount", float("nan")),
        ("amount", float("inf")),
        ("amount", float("-inf")),
        ("amount", -0.5),
        ("amount", 1e15 + 1),
        ("amount", 10**1000),
        ("start", True),
        ("end", 0),
        ("limit", 0),
        ("limit", 101),
        ("limit", 1.0),
        ("limit", None),
    ],
)
def test_rejects_invalid_actual_values(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    catalog = registry(tmp_path)
    call = arguments()
    call[field] = value
    with pytest.raises(ValueError, match="argument_value:" + field):
        catalog.bind("lookup", call)


@pytest.mark.parametrize(
    "changed",
    [
        {"node": 0, "amount": 0, "limit": 1},
        {"node": 2**63 - 1, "amount": 1e15, "limit": 100},
        {"amount": 3, "start": 2**63 - 2, "end": 2**63 - 1},
        {"lower": 9, "upper": 9},
    ],
)
def test_inclusive_numeric_boundaries(tmp_path: Path, changed: dict) -> None:
    call = arguments()
    call.update(changed)
    assert registry(tmp_path).bind("lookup", call).arguments == call


@pytest.mark.parametrize("call", [None, [], "{}", {"node": 7}])
def test_exact_argument_object(tmp_path: Path, call: Any) -> None:
    with pytest.raises(ValueError, match="argument_keys"):
        registry(tmp_path).bind("lookup", call)


@pytest.mark.parametrize("extra", ["workspace", "workspace_id", "expected_answer"])
def test_rejects_unexpected_keys_and_workspace_override(
    tmp_path: Path, extra: str
) -> None:
    call = arguments()
    call[extra] = "untrusted"
    with pytest.raises(ValueError, match="argument_keys"):
        registry(tmp_path).bind("lookup", call)


@pytest.mark.parametrize(
    "changed",
    [
        {"start": 20, "end": 20},
        {"start": 21, "end": 20},
        {"lower": 10, "upper": 9},
    ],
)
def test_cross_field_relations(tmp_path: Path, changed: dict) -> None:
    call = arguments()
    call.update(changed)
    with pytest.raises(ValueError, match="argument_relation"):
        registry(tmp_path).bind("lookup", call)


def test_finite_hop_selection_without_interpolation(tmp_path: Path) -> None:
    catalog = registry(tmp_path, binding(tmp_path, selector=True))
    for hops in (1, 2, 3):
        bound = catalog.bind("lookup", arguments(hops=hops))
        assert bound.query == f"RETURN {hops} AS depth"
        assert "hops" not in bound.parameters
        assert bound.arguments["hops"] == hops
    for invalid in (True, 0, 4, "1 RETURN 9", 2.0):
        with pytest.raises(ValueError, match="argument_value:hops"):
            catalog.bind("lookup", arguments(hops=invalid))
    with pytest.raises(ValueError, match="query_arm"):
        catalog.bind("lookup", arguments(hops=1), arm="optimized")
    with pytest.raises(ValueError, match="unknown_tool"):
        catalog.bind("unknown", arguments(hops=1))


@pytest.mark.parametrize(
    "raw_hash,text_hash,error",
    [
        ("0" * 64, None, "template_raw_hash_mismatch"),
        (None, "0" * 64, "template_bound_hash_mismatch"),
        ("bad", None, "invalid_sha256"),
    ],
)
def test_independent_hash_pins(
    tmp_path: Path,
    raw_hash: str | None,
    text_hash: str | None,
    error: str,
) -> None:
    tool = binding(tmp_path)
    original = tool.variants[None].original
    broken = replace(
        original,
        raw_sha256=raw_hash or original.raw_sha256,
        bound_sha256=text_hash or original.bound_sha256,
    )
    with pytest.raises(ValueError, match=error):
        registry(tmp_path, replace(tool, variants={None: QueryPair(broken)}))


def test_changed_optimized_artifact_is_not_silently_accepted(tmp_path: Path) -> None:
    tool = binding(tmp_path)
    (tmp_path / "alternative.cypher").write_bytes(b"RETURN 99")
    with pytest.raises(ValueError, match="template_raw_hash_mismatch"):
        registry(tmp_path, tool)


@pytest.mark.parametrize("kind", ["absolute", "parent", "symlink"])
def test_paths_are_bounded_to_explicit_root(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "queries"
    root.mkdir()
    outside = template(tmp_path, "outside", b"RETURN 1")
    tool = binding(root)
    if kind == "absolute":
        path = tmp_path / outside.path
    elif kind == "parent":
        path = "../outside.cypher"
    else:
        (root / "link.cypher").symlink_to(tmp_path / outside.path)
        path = "link.cypher"
    broken = replace(outside, path=path)
    with pytest.raises(ValueError, match="template_path_outside_root"):
        registry(root, replace(tool, variants={None: QueryPair(broken)}))


@pytest.mark.parametrize(
    "mutation",
    [
        "optional",
        "extra",
        "enum",
        "unbounded",
        "boolean_bound",
        "workspace",
        "missing_variant",
        "unknown_relation",
        "bad_relation",
    ],
)
def test_trusted_configuration_fails_closed_for_unsupported_contracts(
    tmp_path: Path,
    mutation: str,
) -> None:
    tool = binding(tmp_path, selector=mutation == "missing_variant")
    if mutation == "optional":
        tool.parameters["required"].pop()
    elif mutation == "extra":
        tool.parameters["additionalProperties"] = True
    elif mutation == "enum":
        tool.parameters["properties"]["node"]["enum"] = [1, 2]
    elif mutation == "unbounded":
        tool.parameters["properties"]["node"].pop("maximum")
    elif mutation == "boolean_bound":
        tool.parameters["properties"]["node"]["minimum"] = False
    elif mutation == "workspace":
        tool.parameters["properties"]["workspace"] = integer(0, 1)
        tool.parameters["required"].append("workspace")
    elif mutation == "missing_variant":
        tool.variants.pop(2)
    elif mutation == "unknown_relation":
        tool = replace(tool, relations=(ArgumentRelation("missing", "end"),))
    else:
        tool = replace(tool, relations=(ArgumentRelation("start", "end", "eq"),))
    with pytest.raises(ValueError):
        registry(tmp_path, tool)


def test_custom_workspace_parameter_and_duplicate_names(tmp_path: Path) -> None:
    tool = binding(tmp_path)
    catalog = FrozenToolRegistry(
        query_root=tmp_path,
        tools=[tool],
        workspace_id="trusted",
        workspace_parameter="tenant",
        registry_version="synthetic-v2",
    )
    bound = catalog.bind("lookup", arguments())
    assert bound.parameters["tenant"] == "trusted"
    assert "workspace" not in bound.parameters
    assert bound.registry_version == "synthetic-v2"
    with pytest.raises(ValueError, match="duplicate_or_empty_tool"):
        FrozenToolRegistry(query_root=tmp_path, tools=[tool, tool], workspace_id="x")


def test_immutable_binding_mappings_are_supported(tmp_path: Path) -> None:
    tool = binding(tmp_path)
    tool = replace(
        tool,
        parameters=MappingProxyType(tool.parameters),
        variants=MappingProxyType(tool.variants),
    )
    assert registry(tmp_path, tool).bind("lookup", arguments()).arguments == arguments()


class FakeResult:
    def __init__(self, failure: str | None) -> None:
        self.failure = failure
        self.consumed = False

    def __iter__(self) -> Iterator[Record]:
        if self.failure == "empty":
            return
        yield Record([("value", 8)])
        if self.failure == "iteration":
            raise RuntimeError("synthetic stream failure")
        yield Record([("value", 3)])

    def consume(self) -> SimpleNamespace:
        self.consumed = True
        if self.failure == "consume":
            raise RuntimeError("synthetic consume failure")
        return SimpleNamespace(result_available_after=4, result_consumed_after=2)


class FakeDriver:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.sessions: list[FakeSession] = []
        self.closed = False

    def session(self, **kwargs: Any) -> FakeSession:
        if self.failure == "session":
            raise RuntimeError("synthetic session failure")
        session = FakeSession(self.failure, kwargs)
        self.sessions.append(session)
        return session

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, failure: str | None, kwargs: dict) -> None:
        self.failure = failure
        self.kwargs = kwargs
        self.closed = False
        self.result = FakeResult(failure)
        self.query = None
        self.parameters = None

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.closed = True
        if self.failure == "close":
            raise RuntimeError("synthetic close failure")

    def run(self, query: Query, parameters: dict[str, Any]) -> FakeResult:
        self.query, self.parameters = query, parameters
        if self.failure == "run":
            raise RuntimeError("synthetic run failure")
        return self.result


def test_executor_uses_real_neo4j_query_record_contract_and_shared_driver(
    tmp_path: Path,
) -> None:
    bound = registry(tmp_path).bind("lookup", arguments(), arm="optimized")
    driver = FakeDriver()
    receipts = []
    executor = FrozenQueryExecutor(
        driver,
        database="synthetic",
        transaction_timeout_s=2.5,
        on_record=receipts.append,
    )
    result = executor.execute(bound)
    second = executor.execute(bound)
    assert ordered_rows_equal(result.rows, [{"value": 8}, {"value": 3}])
    assert result.rows == second.rows
    assert len(driver.sessions) == 2
    assert not driver.closed
    assert len(receipts) == 2
    session = driver.sessions[0]
    assert session.closed and session.result.consumed
    assert session.kwargs == {
        "database": "synthetic",
        "default_access_mode": READ_ACCESS,
    }
    assert isinstance(session.query, Query)
    assert session.query.text == bound.query
    assert session.query.timeout == 2.5
    assert session.parameters == dict(bound.parameters)
    receipt = result.record
    assert receipts[0] is receipt
    assert receipt.row_count == 2 and receipt.success and receipt.error_type is None
    assert receipt.query_sha256 == bound.query_sha256
    assert receipt.original_query_sha256 == bound.original_query_sha256
    assert receipt.raw_sha256 == bound.raw_sha256
    assert receipt.original_raw_sha256 == bound.original_raw_sha256
    assert receipt.workspace_id == "synthetic-host"
    assert receipt.started_monotonic_s <= receipt.run_returned_monotonic_s
    assert receipt.run_returned_monotonic_s <= receipt.consumed_monotonic_s
    assert receipt.consumed_monotonic_s <= receipt.ended_monotonic_s
    assert receipt.wall_ms >= 0
    assert receipt.started_unix_s <= receipt.ended_unix_s
    assert (receipt.server_available_ms, receipt.server_consumed_ms) == (4, 2)
    assert not {"query", "arguments", "parameters", "rows"} & asdict(receipt).keys()


def test_executor_accepts_empty_results_and_consumes_summary(tmp_path: Path) -> None:
    driver = FakeDriver("empty")
    execution = FrozenQueryExecutor(driver).execute(
        registry(tmp_path).bind("lookup", arguments())
    )
    assert execution.rows == []
    assert execution.record.success and execution.record.row_count == 0
    assert driver.sessions[0].result.consumed and driver.sessions[0].closed


@pytest.mark.parametrize(
    "failure,rows",
    [
        ("session", 0),
        ("run", 0),
        ("iteration", 1),
        ("consume", 2),
        ("close", 2),
    ],
)
def test_executor_failure_receipt_and_cleanup(
    tmp_path: Path, failure: str, rows: int
) -> None:
    driver = FakeDriver(failure)
    receipts = []
    executor = FrozenQueryExecutor(driver, on_record=receipts.append)
    with pytest.raises(RuntimeError, match="synthetic"):
        executor.execute(registry(tmp_path).bind("lookup", arguments()))
    assert all(session.closed for session in driver.sessions)
    assert not driver.closed
    assert len(receipts) == 1
    record = receipts[0]
    assert not record.success and record.error_type == "RuntimeError"
    assert record.row_count == rows
    assert record.ended_monotonic_s >= record.started_monotonic_s
    if failure in ("session", "run"):
        assert record.run_returned_monotonic_s is None
    if failure != "close":
        assert record.consumed_monotonic_s is None


@pytest.mark.parametrize("failure", [None, "iteration"])
def test_observer_failure_cannot_retry_or_mask_dispatch(
    tmp_path: Path, failure: str | None
) -> None:
    def broken_observer(record: QueryExecutionRecord) -> None:
        raise ValueError("synthetic observer failure")

    driver = FakeDriver(failure)
    executor = FrozenQueryExecutor(driver, on_record=broken_observer)
    bound = registry(tmp_path).bind("lookup", arguments())
    if failure:
        with pytest.raises(RuntimeError, match="stream"):
            executor.execute(bound)
    else:
        assert executor.execute(bound).record.success
    assert len(driver.sessions) == 1 and driver.sessions[0].closed


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), 10**1000])
def test_transaction_timeout_must_be_positive_finite(value: Any) -> None:
    with pytest.raises(ValueError, match="invalid_timeout"):
        FrozenQueryExecutor(FakeDriver(), transaction_timeout_s=value)


@pytest.mark.parametrize(
    "actual,expected",
    [
        ([{"v": True}], [{"v": 1}]),
        ([{"v": 1}], [{"v": 1.0}]),
        ([{"v": 1}, {"v": 2}], [{"v": 2}, {"v": 1}]),
        ([{"v": 1}, {"v": 1}], [{"v": 1}]),
        ([{"v": [1, 2]}], [{"v": [2, 1]}]),
        ([{"v": [1]}], [{"v": (1,)}]),
        ([{"a": 1, "b": 2}], [{"b": 2, "a": 1}]),
        ([{"v": {1: "x"}}], [{"v": {True: "x"}}]),
    ],
)
def test_oracle_is_strict_in_types_order_and_multiplicity(
    actual: Any, expected: Any
) -> None:
    assert not ordered_rows_equal(actual, expected)


def test_oracle_accepts_equal_nested_rows_and_empty_results() -> None:
    rows = [{"a": [1, 2.0, None, True], "b": {"c": "synthetic"}}]
    assert ordered_rows_equal(rows, copy.deepcopy(rows))
    assert ordered_rows_equal([], [])


def test_admission_disabled_by_default_and_separate_stage_permits() -> None:
    disabled = StageAdmission()
    with disabled.enter("tool") as record:
        with disabled.enter("tool") as nested:
            assert not record.enabled and not nested.enabled
    gate = StageAdmission(tool_slots=1, final_slots=1, timeout_s=0)
    with gate.enter("tool") as tool:
        with gate.enter("final") as final:
            assert tool.enabled and final.enabled
            with pytest.raises(StageAdmissionTimeout) as caught:
                with gate.enter("tool"):
                    pytest.fail("saturated gate admitted")
            assert caught.value.record.admitted_monotonic_s is None
            assert caught.value.record.wait_s >= 0
    with gate.enter("tool"):
        pass


def test_admission_releases_on_base_exception() -> None:
    gate = StageAdmission(tool_slots=1, timeout_s=0)
    with pytest.raises(KeyboardInterrupt):
        with gate.enter("tool"):
            raise KeyboardInterrupt()
    with gate.enter("tool") as record:
        assert record.admitted_monotonic_s is not None


def test_admission_records_queue_wait_with_real_worker() -> None:
    gate = StageAdmission(tool_slots=1, timeout_s=2)
    ready = Event()
    entered = Event()

    def worker() -> StageAdmissionRecord:
        ready.set()
        with gate.enter("tool") as record:
            entered.set()
            return record

    with ThreadPoolExecutor(max_workers=1) as pool:
        with gate.enter("tool"):
            future = pool.submit(worker)
            assert ready.wait(1)
            assert not entered.wait(0.01)
        record = future.result(timeout=3)
    assert record.enabled and record.wait_s > 0
    assert record.admitted_monotonic_s >= record.queued_monotonic_s


@pytest.mark.parametrize(
    "options",
    [
        {"tool_slots": 0},
        {"final_slots": True},
        {"tool_slots": 1.5},
        {"timeout_s": -1},
        {"timeout_s": float("inf")},
        {"timeout_s": True},
    ],
)
def test_admission_invalid_configuration(options: dict) -> None:
    with pytest.raises(ValueError):
        StageAdmission(**options)


def test_unknown_stage_fails_before_admission() -> None:
    with pytest.raises(ValueError, match="unknown_stage"):
        with StageAdmission().enter("other"):
            pytest.fail("unknown stage admitted")


def test_admission_records_failures_and_releases_after_callback_abort() -> None:
    receipts = []
    gate = StageAdmission(tool_slots=1, timeout_s=0, on_record=receipts.append)
    with pytest.raises(RuntimeError):
        with gate.enter("tool"):
            raise RuntimeError("execution failed")
    assert len(receipts) == 1 and receipts[0].admitted_monotonic_s is not None
    with gate.enter("tool"):
        with pytest.raises(StageAdmissionTimeout):
            with gate.enter("tool"):
                pytest.fail("saturated gate admitted")
    assert len(receipts) == 3 and receipts[-1].admitted_monotonic_s is None

    def abort(record):
        raise KeyboardInterrupt()

    gate = StageAdmission(tool_slots=1, timeout_s=0, on_record=abort)
    with pytest.raises(KeyboardInterrupt):
        with gate.enter("tool"):
            pytest.fail("callback abort ignored")
    gate._on_record = None
    with gate.enter("tool"):
        pass


def test_pre_cancelled_admission_never_dispatches() -> None:
    cancel = Event()
    cancel.set()
    receipts = []
    gate = StageAdmission(tool_slots=1, on_record=receipts.append)
    with pytest.raises(StageAdmissionCancelled) as caught:
        with gate.enter("tool", cancel_event=cancel):
            pytest.fail("cancelled call dispatched")
    assert caught.value.record is receipts[0]
    assert receipts[0].admitted_monotonic_s is None
    with gate.enter("tool"):
        pass


def test_async_cancelled_queue_never_dispatches(monkeypatch) -> None:
    receipts = []
    queued = Event()
    dispatched = Event()
    gate = StageAdmission(tool_slots=1, timeout_s=2, on_record=receipts.append)
    original = gate.enter

    @contextmanager
    def observed_enter(stage, **kwargs):
        queued.set()
        with original(stage, **kwargs) as record:
            yield record

    async def scenario():
        with original("tool"):
            monkeypatch.setattr(gate, "enter", observed_enter)
            task = asyncio.create_task(gate.run("tool", dispatched.set))
            assert await asyncio.to_thread(queued.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert not dispatched.is_set()
        assert not dispatched.is_set()
        with original("tool"):
            pass

    asyncio.run(scenario())
    assert len(receipts) == 3 and receipts[1].admitted_monotonic_s is None


def test_async_cancel_running_stage_drains_before_releasing_permit() -> None:
    started, release = Event(), Event()
    gate = StageAdmission(tool_slots=1, timeout_s=0)

    def execute():
        started.set()
        assert release.wait(2)
        return "finished"

    async def scenario():
        task = asyncio.create_task(gate.run("tool", execute))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            with pytest.raises(StageAdmissionTimeout):
                with gate.enter("tool"):
                    pytest.fail("running worker lost its permit")
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        with gate.enter("tool"):
            pass

    asyncio.run(scenario())


def test_async_stage_preserves_success_and_failure_receipts() -> None:
    receipts = []
    gate = StageAdmission(tool_slots=1, timeout_s=0, on_record=receipts.append)

    def fail():
        raise ValueError("synthetic execution failure")

    async def scenario():
        assert await gate.run("tool", lambda: 42) == 42
        with pytest.raises(ValueError, match="synthetic"):
            await gate.run("tool", fail)
        with gate.enter("tool"):
            pass

    asyncio.run(scenario())
    assert len(receipts) == 3
    assert all(record.admitted_monotonic_s is not None for record in receipts)


def test_async_shutdown_waits_for_worker_before_cancellation_receipt() -> None:
    started, release, finished = Event(), Event(), Event()
    gate = StageAdmission(tool_slots=1, timeout_s=0)
    cancellation_receipts = []

    def execute():
        started.set()
        assert release.wait(2)
        finished.set()

    async def caller():
        try:
            await gate.run("tool", execute)
        except asyncio.CancelledError:
            cancellation_receipts.append(finished.is_set())
            raise

    async def scenario():
        asyncio.create_task(caller())
        assert await asyncio.to_thread(started.wait, 1)
        # Returning from main cancels every asyncio Task, not only the caller.
        timer.start()

    timer = Timer(0.1, release.set)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        timer.join()
    assert cancellation_receipts == [True]
    with gate.enter("tool"):
        pass


def test_async_stage_preserves_caller_trace_context() -> None:
    trace = ContextVar("synthetic_trace", default="unset")

    async def scenario():
        trace.set("caller-trace")
        assert await StageAdmission().run("tool", trace.get) == "caller-trace"

    asyncio.run(scenario())
