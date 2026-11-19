"""Explicit, frozen query tools for harnesses using trusted private definitions.

No tool catalog, query, dataset schema, or expected answer ships in this module.
Operators supply numeric argument schemas and pinned templates to
``FrozenToolRegistry``. Hashes establish artifact identity, never semantic
equivalence. READ routing is not a static safety proof or an authorization
boundary. Review templates, enforce database privileges and tenant predicates,
and compare ordered results independently before enabling an optimized arm.

Only this adapter's callers opt in. General SEOCHO agents are unaffected.
"""

from __future__ import annotations

import copy
import asyncio
import hashlib
import logging
import math
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass, field
from pathlib import Path
from threading import BoundedSemaphore, Event
from types import MappingProxyType
from typing import Any, Literal

QueryArm = Literal["original", "optimized"]
Stage = Literal["tool", "final"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryTemplate:
    """Trusted relative path and independently pinned byte/text SHA-256 values.

    Bound text matches ``Path.read_text(encoding='utf-8').strip()`` including
    universal newline conversion. The raw hash covers the exact file bytes.
    Pins must come from trusted review evidence, not be recomputed to make an
    unexpected artifact pass. Templates are loaded once at registry creation.
    """

    template_id: str
    path: str | Path
    raw_sha256: str
    bound_sha256: str


@dataclass(frozen=True)
class QueryPair:
    """An original template and an explicitly selected optional replacement."""

    original: QueryTemplate
    optimized: QueryTemplate | None = None


@dataclass(frozen=True)
class ArgumentRelation:
    """A required numeric relation, such as start < end or lower <= upper."""

    left: str
    right: str
    operator: Literal["lt", "le"] = "lt"


@dataclass(frozen=True)
class ToolBinding:
    """Trusted tool schema and finite template table, never model supplied.

    ``parameters`` supports an exact-key object of bounded integer/number
    properties. Unsupported schema constraints fail at construction instead of
    being silently ignored. Cross-field semantics belong in ``relations``.
    With no selector, ``variants`` has one key, ``None``. Otherwise the integer
    selector's complete bounded domain maps to prewritten query pairs, and the
    selector is omitted from database parameters. No query interpolation occurs.
    """

    name: str
    description: str
    parameters: Mapping[str, Any] = field(repr=False)
    variants: Mapping[int | None, QueryPair] = field(repr=False)
    selector: str | None = None
    relations: tuple[ArgumentRelation, ...] = ()


@dataclass(frozen=True)
class BoundQuery:
    """Private dispatch data plus selected and original artifact provenance.

    Arguments, parameters and text may contain private data. Do not persist
    this object to public logs. ``QueryExecutionRecord`` contains no raw values.
    ``expected_original_sha256`` is the caller's unchanged provenance assertion,
    not a replacement for the selected arm's independently validated pins.
    """

    tool: str
    arm: QueryArm
    template_id: str
    original_template_id: str
    query_sha256: str
    raw_sha256: str
    original_query_sha256: str
    original_raw_sha256: str
    expected_original_sha256: str | None
    workspace_id: str
    registry_version: str
    query: str = field(repr=False)
    arguments: Mapping[str, int | float] = field(repr=False)
    parameters: Mapping[str, int | float | str] = field(repr=False)


@dataclass(frozen=True)
class _LoadedTemplate:
    template: QueryTemplate
    text: str = field(repr=False)


@dataclass(frozen=True)
class _LoadedTool:
    binding: ToolBinding
    variants: Mapping[int | None, Mapping[QueryArm, _LoadedTemplate]]


def _number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _check_hash(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("invalid_sha256")


def _load_template(root: Path, template: QueryTemplate) -> _LoadedTemplate:
    _check_hash(template.raw_sha256)
    _check_hash(template.bound_sha256)
    path = Path(template.path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("template_path_outside_root")
    resolved = (root / path).resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("template_path_outside_root")
    raw = resolved.read_bytes()
    if _sha256(raw) != template.raw_sha256:
        raise ValueError("template_raw_hash_mismatch")
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or _sha256(text.encode("utf-8")) != template.bound_sha256:
        raise ValueError("template_bound_hash_mismatch")
    return _LoadedTemplate(template, text)


def _validate_binding(binding: ToolBinding, workspace_parameter: str) -> None:
    schema = binding.parameters
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema) - {"type", "properties", "required", "additionalProperties"}
    ):
        raise ValueError("unsupported_argument_schema")
    props = schema.get("properties")
    required = schema.get("required")
    if (
        not isinstance(props, dict)
        or not all(isinstance(key, str) and key for key in props)
        or not isinstance(required, list)
        or not all(isinstance(key, str) for key in required)
        or len(required) != len(props)
        or set(required) != set(props)
        or workspace_parameter in props
    ):
        raise ValueError("argument_schema_keys")
    for spec in props.values():
        if (
            not isinstance(spec, dict)
            or spec.get("type") not in ("integer", "number")
            or set(spec) - {"type", "minimum", "maximum", "description"}
            or not _number(spec.get("minimum"))
            or not _number(spec.get("maximum"))
            or spec["minimum"] > spec["maximum"]
            or (
                spec["type"] == "integer"
                and any(type(spec[key]) is not int for key in ("minimum", "maximum"))
            )
        ):
            raise ValueError("unsupported_numeric_schema")
    for relation in binding.relations:
        if (
            relation.left not in props
            or relation.right not in props
            or relation.operator not in ("lt", "le")
        ):
            raise ValueError("invalid_argument_relation")
    if binding.selector is None:
        if set(binding.variants) != {None}:
            raise ValueError("template_variants")
    else:
        spec = props.get(binding.selector, {})
        if spec.get("type") != "integer":
            raise ValueError("template_selector")
        lo, hi = spec["minimum"], spec["maximum"]
        if len(binding.variants) != hi - lo + 1 or any(
            type(key) is not int or not lo <= key <= hi for key in binding.variants
        ):
            raise ValueError("template_variants")


class FrozenToolRegistry:
    """Load only explicit trusted bindings and templates beneath ``query_root``.

    There is no filesystem discovery, Python-module execution, global catalog,
    question matching, or expected-answer dispatch. Caller mutations and later
    file edits do not change the loaded registry. The workspace is injected by
    the host; templates must actually enforce its scope in the database.
    """

    def __init__(
        self,
        *,
        query_root: str | Path,
        tools: Sequence[ToolBinding],
        workspace_id: str,
        workspace_parameter: str = "workspace",
        registry_version: str = "frozen-tools-v1",
    ) -> None:
        if not workspace_id or not workspace_parameter or not registry_version:
            raise ValueError("trusted_context_required")
        root = Path(query_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("query_root_not_directory")
        self._workspace_id = workspace_id
        self._workspace_parameter = workspace_parameter
        self._registry_version = registry_version
        self._tools: dict[str, _LoadedTool] = {}
        for supplied in tools:
            binding = ToolBinding(
                supplied.name,
                supplied.description,
                copy.deepcopy(dict(supplied.parameters)),
                dict(supplied.variants),
                supplied.selector,
                tuple(supplied.relations),
            )
            if not binding.name or binding.name in self._tools:
                raise ValueError("duplicate_or_empty_tool")
            _validate_binding(binding, workspace_parameter)
            variants: dict[int | None, dict[QueryArm, _LoadedTemplate]] = {}
            for key, pair in binding.variants.items():
                arms: dict[QueryArm, _LoadedTemplate] = {
                    "original": _load_template(root, pair.original)
                }
                if pair.optimized is not None:
                    arms["optimized"] = _load_template(root, pair.optimized)
                variants[key] = arms
            self._tools[binding.name] = _LoadedTool(binding, variants)

    def schemas(self) -> list[dict[str, Any]]:
        """Return independent OpenAI-style function schemas from private input."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.binding.name,
                    "description": tool.binding.description,
                    "parameters": copy.deepcopy(tool.binding.parameters),
                },
            }
            for tool in self._tools.values()
        ]

    def bind(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        arm: QueryArm = "original",
        expected_original_sha256: str | None = None,
    ) -> BoundQuery:
        """Validate the actual call and select one finite preloaded template.

        ``expected_original_sha256`` checks the original bound-text pin even
        in the optimized arm. It is never rewritten to the optimized hash.
        """
        if name not in self._tools:
            raise ValueError("unknown_tool")
        tool = self._tools[name]
        binding = tool.binding
        props = binding.parameters["properties"]
        if not isinstance(arguments, dict) or set(arguments) != set(props):
            raise ValueError("argument_keys")
        for key, spec in props.items():
            value = arguments[key]
            valid = type(value) is int if spec["type"] == "integer" else _number(value)
            if not valid or not spec["minimum"] <= value <= spec["maximum"]:
                raise ValueError("argument_value:" + key)
        for relation in binding.relations:
            left, right = arguments[relation.left], arguments[relation.right]
            if not (left < right if relation.operator == "lt" else left <= right):
                raise ValueError("argument_relation:" + relation.left)
        key = arguments[binding.selector] if binding.selector is not None else None
        arms = tool.variants[key]
        if arm not in ("original", "optimized") or arm not in arms:
            raise ValueError("query_arm")
        selected = arms[arm]
        original = arms["original"].template
        if (
            expected_original_sha256 is not None
            and expected_original_sha256 != original.bound_sha256
        ):
            raise ValueError("original_query_hash_mismatch")
        params = dict(arguments)
        if binding.selector is not None:
            params.pop(binding.selector)
        params[self._workspace_parameter] = self._workspace_id
        return BoundQuery(
            tool=name,
            arm=arm,
            template_id=selected.template.template_id,
            original_template_id=original.template_id,
            query_sha256=selected.template.bound_sha256,
            raw_sha256=selected.template.raw_sha256,
            original_query_sha256=original.bound_sha256,
            original_raw_sha256=original.raw_sha256,
            expected_original_sha256=expected_original_sha256,
            workspace_id=self._workspace_id,
            registry_version=self._registry_version,
            query=selected.text,
            arguments=MappingProxyType(dict(arguments)),
            parameters=MappingProxyType(params),
        )


@dataclass(frozen=True)
class QueryExecutionRecord:
    """Content-free dispatch receipt, emitted on success and failure.

    Monotonic boundaries measure client wall time including session cleanup.
    Server timings are driver-reported milliseconds, not GPU stage timings.
    ``row_count`` is the number materialized, which can be partial on failure.
    """

    tool: str
    arm: QueryArm
    template_id: str
    original_template_id: str
    query_sha256: str
    raw_sha256: str
    original_query_sha256: str
    original_raw_sha256: str
    expected_original_sha256: str | None
    workspace_id: str
    registry_version: str
    started_unix_s: float
    ended_unix_s: float
    started_monotonic_s: float
    run_returned_monotonic_s: float | None
    consumed_monotonic_s: float | None
    ended_monotonic_s: float
    row_count: int
    success: bool
    error_type: str | None
    server_available_ms: int | None
    server_consumed_ms: int | None

    @property
    def wall_ms(self) -> float:
        return 1000 * (self.ended_monotonic_s - self.started_monotonic_s)


@dataclass(frozen=True)
class QueryExecution:
    rows: list[dict[str, Any]] = field(repr=False)
    record: QueryExecutionRecord


def _timeout(value: float, *, allow_zero: bool = False) -> None:
    if not _number(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError("invalid_timeout")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("invalid_timeout")


class FrozenQueryExecutor:
    """Execute bound calls using a caller-owned shared synchronous Neo4j driver.

    Each call owns a READ session and a timed auto-commit transaction. The
    executor never closes the shared driver or retries a query. No expected
    answer enters dispatch. ``on_record`` receives failure receipts too; its
    exceptions are logged without payload and cannot mask a database failure
    or turn a completed query into a retry. Neo4j is imported only on execution.
    """

    def __init__(
        self,
        driver: Any,
        *,
        database: str = "neo4j",
        transaction_timeout_s: float = 15.0,
        on_record: Callable[[QueryExecutionRecord], None] | None = None,
    ) -> None:
        _timeout(transaction_timeout_s)
        self._driver = driver
        self._database = database
        self._timeout_s = transaction_timeout_s
        self._on_record = on_record

    def execute(self, bound: BoundQuery) -> QueryExecution:
        """Materialize ordered rows and record end boundaries after session close."""
        from neo4j import READ_ACCESS, Query

        start_unix, start = time.time(), time.monotonic()
        run_returned = consumed = None
        rows: list[dict[str, Any]] = []
        summary = None
        error_type = None
        try:
            with self._driver.session(
                database=self._database, default_access_mode=READ_ACCESS
            ) as session:
                result = session.run(
                    Query(bound.query, timeout=self._timeout_s), dict(bound.parameters)
                )
                run_returned = time.monotonic()
                for record in result:
                    rows.append(record.data())
                summary = result.consume()
                consumed = time.monotonic()
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            end, end_unix = time.monotonic(), time.time()
            receipt = QueryExecutionRecord(
                tool=bound.tool,
                arm=bound.arm,
                template_id=bound.template_id,
                original_template_id=bound.original_template_id,
                query_sha256=bound.query_sha256,
                raw_sha256=bound.raw_sha256,
                original_query_sha256=bound.original_query_sha256,
                original_raw_sha256=bound.original_raw_sha256,
                expected_original_sha256=bound.expected_original_sha256,
                workspace_id=bound.workspace_id,
                registry_version=bound.registry_version,
                started_unix_s=start_unix,
                ended_unix_s=end_unix,
                started_monotonic_s=start,
                run_returned_monotonic_s=run_returned,
                consumed_monotonic_s=consumed,
                ended_monotonic_s=end,
                row_count=len(rows),
                success=error_type is None,
                error_type=error_type,
                server_available_ms=getattr(summary, "result_available_after", None),
                server_consumed_ms=getattr(summary, "result_consumed_after", None),
            )
            if self._on_record is not None:
                try:
                    self._on_record(receipt)
                except Exception:
                    logger.warning("query_record_callback_failed")
        return QueryExecution(rows, receipt)


def ordered_rows_equal(actual: Any, expected: Any) -> bool:
    """Strict recursive oracle comparison, separate from dispatch.

    Types, row order, column order, nested sequence order and duplicate counts
    must match. In particular bool/int and int/float are distinct. This checks
    only the supplied results, not general query equivalence.
    """
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, Mapping):
        return len(actual) == len(expected) and all(
            ordered_rows_equal(ak, ek) and ordered_rows_equal(av, ev)
            for (ak, av), (ek, ev) in zip(actual.items(), expected.items())
        )
    if isinstance(actual, (list, tuple)):
        return len(actual) == len(expected) and all(
            ordered_rows_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


@dataclass(frozen=True)
class StageAdmissionRecord:
    stage: Stage
    enabled: bool
    queued_monotonic_s: float
    admitted_monotonic_s: float | None
    wait_s: float


class StageAdmissionTimeout(TimeoutError):
    def __init__(self, record: StageAdmissionRecord) -> None:
        super().__init__("stage_admission_timeout:" + record.stage)
        self.record = record


class StageAdmissionCancelled(RuntimeError):
    def __init__(self, record: StageAdmissionRecord) -> None:
        super().__init__("stage_admission_cancelled")
        self.record = record


class StageAdmission:
    """Optional synchronous harness gates; both stages are disabled by default.

    Share one instance across harness workers. Queue timeout bounds admission
    wait only, never execution time. Hold a lease only around its stage, not
    around an entire agent loop. No general agent uses these gates implicitly.
    """

    def __init__(
        self,
        *,
        tool_slots: int | None = None,
        final_slots: int | None = None,
        timeout_s: float = 30.0,
        on_record: Callable[[StageAdmissionRecord], None] | None = None,
    ) -> None:
        _timeout(timeout_s, allow_zero=True)
        self._timeout_s = timeout_s
        self._on_record = on_record
        self._gates: dict[Stage, BoundedSemaphore | None] = {}
        for stage, slots in (("tool", tool_slots), ("final", final_slots)):
            if slots is not None and (type(slots) is not int or slots < 1):
                raise ValueError("invalid_stage_slots")
            self._gates[stage] = BoundedSemaphore(slots) if slots is not None else None

    @contextmanager
    def enter(
        self, stage: Stage, *, cancel_event: Event | None = None
    ) -> Iterator[StageAdmissionRecord]:
        """Yield queue timing and release an admitted permit on every exit."""
        if stage not in self._gates:
            raise ValueError("unknown_stage")
        gate = self._gates[stage]
        queued = time.monotonic()
        if cancel_event is None:
            admitted = gate is None or gate.acquire(timeout=self._timeout_s)
        else:
            admitted = False
            attempted = False
            while not cancel_event.is_set():
                remaining = max(0.0, queued + self._timeout_s - time.monotonic())
                if attempted and remaining == 0:
                    break
                admitted = gate is None or gate.acquire(timeout=min(0.05, remaining))
                attempted = True
                if admitted or remaining == 0:
                    break
            if admitted and cancel_event.is_set():
                if gate is not None:
                    gate.release()
                admitted = False
        now = time.monotonic()
        record = StageAdmissionRecord(
            stage, gate is not None, queued, now if admitted else None, now - queued
        )
        try:
            if self._on_record is not None:
                try:
                    self._on_record(record)
                except Exception:
                    logger.warning("stage_record_callback_failed")
            if cancel_event is not None and cancel_event.is_set() and not admitted:
                raise StageAdmissionCancelled(record)
            if not admitted:
                raise StageAdmissionTimeout(record)
            yield record
        finally:
            if admitted and gate is not None:
                gate.release()

    async def run(self, stage: Stage, call: Callable[[], Any]) -> Any:
        """Run a synchronous stage in a worker and drain it before cancellation.

        Cancellation before admission prevents dispatch. Already executing work
        finishes under the caller's DB/HTTP timeout; this method does not claim
        to interrupt a synchronous driver or release its permit prematurely.
        """
        cancel_event = Event()

        def worker() -> Any:
            with self.enter(stage, cancel_event=cancel_event):
                return call()

        # A separate Task would be cancelled alongside the caller at loop
        # shutdown, even though its synchronous thread had not finished.
        context = copy_context()
        task = asyncio.get_running_loop().run_in_executor(None, context.run, worker)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel_event.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise
