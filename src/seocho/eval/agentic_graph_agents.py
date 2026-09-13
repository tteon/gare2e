"""Agent runners for graph-query benchmark lanes.

The runners deliberately keep model/provider integration outside SEOCHO's
request-time services.  They are experiment adapters: the baseline uses an
OpenAI-compatible endpoint directly, while the multi-agent lane uses the
OpenAI Agents SDK only for explicit orchestrator-to-specialist handoff.

Receipts contain counts, timings, IDs, and digests only.  Prompts, generated
answers, SPARQL, and graph rows are private evaluation artifacts and are never
placed in the returned receipt.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from .agent_execution import AgentExecutionRecorder, build_retrieval_funnel, observe_tool
from .gold_graph import GoldGraphScore
from .gtsqa import GTSQACase, score_gtsqa_retrieval
from .oxigraph_runtime import GTSQAOxigraphExecutor, OxigraphQueryResult


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_ABSTENTION_MARKERS = (
    "abstain",
    "cannot answer",
    "can't answer",
    "cannot provide",
    "unable to answer",
    "unable to retrieve",
    "insufficient evidence",
    "insufficient information",
    "not enough evidence",
)


def classify_answer_status(answer: str, *, evidence_status: str) -> str:
    """Classify a private answer without copying its text into telemetry."""
    normalized = " ".join(answer.casefold().split())
    if not normalized:
        return "empty"
    if any(marker in normalized for marker in _ABSTENTION_MARKERS):
        return "abstained"
    if evidence_status == "not_called":
        return "direct_answer"
    if evidence_status == "ok":
        return "answered"
    return "unsupported_answer"


def redacted_episode_error(error: Exception, *, stage: str) -> dict[str, Any]:
    """Return a content-free error classification suitable for public receipts."""
    error_class = type(error).__name__
    if error_class == "MaxTurnsExceeded":
        code = "turn_budget_exhausted"
    elif error_class == "ModelBehaviorError":
        code = "model_tool_protocol_error"
    elif isinstance(error, ValueError):
        code = "agent_output_or_usage_validation_error"
    elif isinstance(error, TypeError):
        code = "agent_output_type_error"
    else:
        code = "agent_episode_runtime_error"
    return {
        "error_class": error_class,
        "error_code": code,
        "error_stage": stage,
        "error_fingerprint": _digest(f"{error_class}:{error}"),
    }


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


_PRIVATE_MODEL_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "seocho_private_model_context", default=None
)


_PRIVATE_TOOL_CONTEXT: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "seocho_private_tool_context", default=None
)

_PRIVATE_BRANCH_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "seocho_private_branch_context", default=None
)


def _private_jsonable(value: Any) -> Any:
    """Convert SDK/provider objects to JSON without retaining live agent objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _private_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_private_jsonable(item) for item in value]
    if hasattr(value, "output") and hasattr(value, "usage"):
        return {
            "output": [_private_jsonable(item) for item in getattr(value, "output", ())],
            "usage": _private_jsonable(getattr(value, "usage", None)),
            "response_id": getattr(value, "response_id", None),
            "request_id": getattr(value, "request_id", None),
        }
    raw_item = getattr(value, "raw_item", None)
    if raw_item is not None:
        result = {"type": str(getattr(value, "type", type(value).__name__))}
        result["raw_item"] = _private_jsonable(raw_item)
        for name in ("tool_name", "call_id"):
            item = getattr(value, name, None)
            if item is not None:
                result[name] = str(item)
        return result
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _private_jsonable(model_dump(mode="json"))
        except TypeError:
            return _private_jsonable(model_dump())
    return str(value)


def _private_wire_fields(side: str, value: bytes | str) -> dict[str, str]:
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    fields = {f"wire_{side}_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        fields[f"wire_{side}_json"] = raw.decode("utf-8")
    except UnicodeDecodeError:
        fields[f"wire_{side}_base64"] = base64.b64encode(raw).decode("ascii")
    return fields


@dataclass
class PrivateTrace:
    """Per-case private event log; public receipts remain content-free."""

    correlation: Mapping[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: Callable[[Mapping[str, Any]], None] | None = None
    _branch_last: dict[str, str] = field(default_factory=dict)
    _started: float = field(default_factory=time.perf_counter)
    _ordinal: int = 0
    _model_ordinal: int = 0
    _tool_attempts: dict[str, int] = field(default_factory=dict)
    _last_tool_event: dict[str, str] = field(default_factory=dict)
    last_model_event_id: str | None = None
    last_model_turn_id: str | None = None

    @property
    def root_event_id(self) -> str:
        return f"episode:{self.correlation['run_id']}:{self.correlation['case_id']}:{self.correlation.get('capture_id', 'local')}"

    def _new_event(self, event: str, *, role: str, turn_id: str, parent_event_id: str | None, **fields: Any) -> dict[str, Any]:
        self._ordinal += 1
        record = {
            **dict(self.correlation),
            "schema_version": "agentic_os.private_event.v1",
            "event_id": f"{self.root_event_id}:event:{self._ordinal:06d}",
            "event": event,
            "actor_role": role,
            "turn_id": turn_id,
            "parent_event_id": parent_event_id or self.root_event_id,
            "event_ordinal": self._ordinal,
            "timestamp_unix_s": round(time.time(), 6),
            "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3),
            **fields,
        }
        self.events.append(record)
        self._checkpoint(record)
        return record

    def _checkpoint(self, event: Mapping[str, Any]) -> None:
        if self.checkpoint is not None:
            self.checkpoint(event)

    def begin_model_call(
        self,
        *,
        role: str,
        system_instructions: str | None,
        input: Any,
        tools: Any,
        model_settings: Any,
        parent_event_id: str | None = None,
    ) -> str:
        branch = _PRIVATE_BRANCH_CONTEXT.get() or {}
        role = branch.get("role", role)
        branch_id = branch.get("branch_id", self.root_event_id)
        previous = self._branch_last.get(branch_id)
        parent_event_id = parent_event_id or previous or branch.get("parent_event_id")
        self._model_ordinal += 1
        turn_id = f"{role}:turn:{self._model_ordinal}"
        event = self._new_event(
            "model_call",
            role=role,
            turn_id=turn_id,
            parent_event_id=parent_event_id,
            status="started",
            branch_id=branch_id,
            parent_call_id=previous or branch.get("parent_call_id"),
            sdk_request={
                "system_instructions": system_instructions,
                "input": _private_jsonable(input),
                "tools": _private_jsonable(tools),
                "model_settings": _private_jsonable(model_settings),
            },
        )
        self._branch_last[branch_id] = event["event_id"]
        self.last_model_event_id = event["event_id"]
        self.last_model_turn_id = turn_id
        return event["event_id"]

    def finish_model_call(
        self,
        event_id: str,
        *,
        response: Any = None,
        error: Exception | None = None,
        status_code: int | None = None,
        raw_request: Any = None,
        raw_response: Any = None,
        wire_request: bytes | str | None = None,
        wire_response: bytes | str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        event = next(item for item in self.events if item["event_id"] == event_id)
        event.update(
            {
                "status": "error" if error is not None else "ok",
                "finished_timestamp_unix_s": round(time.time(), 6),
                "finished_elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3),
                "sdk_response": _private_jsonable(response) if response is not None else None,
                "usage": _private_jsonable(response.get("usage") if isinstance(response, Mapping) else getattr(response, "usage", None)),
                "error": {
                    "class": type(error).__name__,
                    "message": None,
                }
                if error is not None
                else None,
            }
        )
        if status_code is not None:
            event["status_code"] = status_code
        if raw_request is not None:
            event["http_request"] = _private_jsonable(raw_request)
        if raw_response is not None:
            event["http_response"] = _private_jsonable(raw_response)
        for name, value in (("request", wire_request), ("response", wire_response)):
            if value is None:
                continue
            event.update(_private_wire_fields(name, value))
        if finish_reason is not None:
            event["finish_reason"] = finish_reason
        self._checkpoint(event)

    def record_http(
        self,
        event_id: str,
        *,
        request_body: Any,
        response_body: Any,
        status_code: int,
        latency_ms: float,
        finish_reason: str | None,
        wire_request: bytes | None = None,
        wire_response: bytes | None = None,
        attempt_index: int | None = None,
    ) -> None:
        event = next(item for item in self.events if item["event_id"] == event_id)
        attempt = {
            "request": _private_jsonable(request_body),
            "response": _private_jsonable(response_body),
            "status_code": status_code,
            "latency_ms": round(latency_ms, 3),
        }
        if finish_reason is not None:
            attempt["finish_reason"] = finish_reason
        for side, raw in (("request", wire_request), ("response", wire_response)):
            if raw is not None:
                attempt.update(_private_wire_fields(side, raw))
        attempts = event.setdefault("http_attempts", [])
        if attempt_index is None:
            attempts.append(attempt)
        else:
            attempts[attempt_index] = attempt
        event.update(
            {
                "status_code": status_code,
                "http_request": attempt["request"],
                "http_response": attempt["response"],
                "http_latency_ms": round(latency_ms, 3),
            }
        )
        if finish_reason is not None:
            event["finish_reason"] = finish_reason
        self._checkpoint(event)

    def begin_tool_call(
        self,
        *,
        role: str,
        name: str,
        arguments: Any,
        parent_event_id: str | None = None,
        turn_id: str | None = None,
        native_call_id: str | None = None,
    ) -> str:
        branch = _PRIVATE_BRANCH_CONTEXT.get() or {}
        parent_event_id = self._branch_last.get(branch.get("branch_id", self.root_event_id)) or parent_event_id
        attempt = self._tool_attempts.get(name, 0)
        self._tool_attempts[name] = attempt + 1
        event = self._new_event(
            "tool_call",
            role=role,
            turn_id=turn_id or self.last_model_turn_id or f"{role}:tool:{attempt + 1}",
            parent_event_id=parent_event_id,
            name=name,
            arguments=_private_jsonable(arguments),
            attempt_index=attempt,
            native_call_id=native_call_id or getattr(_PRIVATE_TOOL_CONTEXT.get(), "tool_call_id", None),
            previous_same_tool_event_id=self._last_tool_event.get(name),
            status="started",
        )
        self._last_tool_event[name] = event["event_id"]
        return event["event_id"]

    def finish_tool_call(self, event_id: str, *, outcome: str, result: Any = None, error: Exception | None = None) -> None:
        event = next(item for item in self.events if item["event_id"] == event_id)
        event.update(
            {
                "status": outcome,
                "finished_timestamp_unix_s": round(time.time(), 6),
                "finished_elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3),
                "result": _private_jsonable(result) if result is not None else None,
                "error": {"class": type(error).__name__, "message": str(error)} if error else None,
            }
        )

        self._checkpoint(event)

    def record_final_answer(self, answer: str) -> None:
        self._new_event(
            "final_answer",
            role="episode",
            turn_id="episode:final",
            parent_event_id=self.last_model_event_id,
            answer=answer,
        )

    def records(self) -> list[dict[str, Any]]:
        return list(self.events)


@dataclass(frozen=True)
class NativeToolCall:
    """Provider-neutral native-function call decoded by the endpoint adapter."""

    name: str
    arguments: Mapping[str, Any]
    call_id: str = ""


@dataclass(frozen=True)
class NativeModelReply:
    """Minimal completion result required by the deterministic ReAct loop."""

    text: str
    tool_calls: tuple[NativeToolCall, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0
    protocol_error: str | None = None
    finish_reason: str | None = None


_TEXTUAL_TOOL_MARKUP = re.compile(r"<tool_call>|<function\s*=", re.I)


def contains_unparsed_tool_markup(text: str) -> bool:
    """Detect model-emitted tool syntax that the endpoint did not parse natively."""
    return bool(_TEXTUAL_TOOL_MARKUP.search(text))


class NativeToolModel(Protocol):
    """Small boundary for a custom OpenAI-compatible chat adapter."""

    def complete(self, *, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]) -> NativeModelReply:
        """Return one model response; optional private tracing is caller-controlled."""


@dataclass(frozen=True)
class OpenAICompatibleNativeToolModel:
    """Synchronous adapter for a vLLM chat-completions endpoint."""

    base_url: str
    model: str
    enable_thinking: bool = True
    max_tokens: int = 512
    timeout_s: float = 180.0
    private_trace: PrivateTrace | None = None
    private_role: str = "single_agent"
    capture_token_ids: bool = False
    capture_wire_bytes: bool = False

    def complete(self, *, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]) -> NativeModelReply:
        return self._complete(messages=messages, tools=tools, tool_choice="auto")

    def complete_with_tool_choice(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        tool_name: str,
    ) -> NativeModelReply:
        """Force one native function for an explicitly non-comparative plumbing smoke."""
        return self._complete(
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": tool_name}},
        )

    def _complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        tool_choice: str | Mapping[str, Any],
    ) -> NativeModelReply:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }
        if self.capture_token_ids:
            payload["return_token_ids"] = True
        if tools:
            payload.update({"tools": list(tools), "tool_choice": tool_choice})
        elif tool_choice != "auto":
            raise ValueError("a forced tool choice requires a non-empty tool surface")
        request_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}/v1/chat/completions",
            data=request_body,
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        trace_event_id = None
        if self.private_trace is not None:
            trace_event_id = self.private_trace.begin_model_call(
                role=self.private_role,
                system_instructions=None,
                input=messages,
                tools=tools,
                model_settings={
                    "model": self.model,
                    "temperature": 0,
                    "max_tokens": self.max_tokens,
                    "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
                    "tool_choice": tool_choice,
                    "return_token_ids": self.capture_token_ids,
                },
            )
        if trace_event_id is not None:
            event = next(e for e in self.private_trace.events if e["event_id"] == trace_event_id)
            event["http_request"] = payload
            if self.capture_wire_bytes:
                event["wire_request_json"] = request_body.decode()
                event["wire_request_sha256"] = hashlib.sha256(request_body).hexdigest()
            self.private_trace._checkpoint(event)
        response_body = None
        try:
            with urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310 - explicit experiment endpoint
                response_body = response.read()
                body = json.loads(response_body.decode("utf-8"))
        except HTTPError as error:
            # Retain only the status class; provider bodies can contain prompt fragments.
            if trace_event_id is not None:
                error_bytes = error.read()
                raw_body = error_bytes.decode("utf-8", errors="replace")
                try:
                    raw_body = json.loads(raw_body)
                except json.JSONDecodeError:
                    pass
                self.private_trace.finish_model_call(
                    trace_event_id,
                    error=error,
                    status_code=error.code,
                    raw_request=payload,
                    raw_response=raw_body,
                    wire_request=request_body if self.capture_wire_bytes else None,
                    wire_response=error_bytes if self.capture_wire_bytes else None,
                )
            raise RuntimeError(f"model endpoint returned HTTP {error.code}") from error
        except Exception as error:
            if trace_event_id is not None:
                self.private_trace.finish_model_call(
                    trace_event_id,
                    error=error,
                    raw_request=payload,
                    raw_response=response_body.decode("utf-8", errors="replace") if response_body is not None else None,
                    wire_request=request_body if self.capture_wire_bytes else None,
                    wire_response=response_body if self.capture_wire_bytes else None,
                )
            raise
        if trace_event_id is not None:
            event["http_response"] = _private_jsonable(body)
            event["status_code"] = 200
            if self.capture_wire_bytes:
                event.update(_private_wire_fields("response", response_body))
            self.private_trace._checkpoint(event)
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[NativeToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                arguments = {}
            calls.append(
                NativeToolCall(
                    name=str(function.get("name", "")),
                    arguments=arguments,
                    call_id=str(raw_call.get("id", "")),
                )
            )
        text = str(message.get("content") or "")
        protocol_error = (
            "textual_tool_call_without_native_tool_calls"
            if not calls and contains_unparsed_tool_markup(text)
            else None
        )
        finish_reason = choice.get("finish_reason")
        if trace_event_id is not None:
            self.private_trace.finish_model_call(
                trace_event_id,
                response=body,
                status_code=200,
                raw_request=payload,
                raw_response=body,
                wire_request=request_body if self.capture_wire_bytes else None,
                wire_response=response_body if self.capture_wire_bytes else None,
                finish_reason=str(finish_reason) if finish_reason is not None else None,
            )
        return NativeModelReply(
            text=text,
            tool_calls=tuple(calls),
            usage=body.get("usage") or {},
            latency_s=time.perf_counter() - started,
            protocol_error=protocol_error,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )


@dataclass(frozen=True)
class GTSQAReActResult:
    """Private answer plus retrieval score and content-free execution receipt."""

    answer: str
    retrieved_edges: tuple[dict[str, str], ...]
    retrieval: GoldGraphScore
    receipt: dict[str, Any]


def run_gtsqa_closed_book(
    case: GTSQACase,
    *,
    model: NativeToolModel,
    evidence_class: str = "behavioral_baseline",
) -> GTSQAReActResult:
    """Run one question-only completion with no graph tool surface."""
    recorder = AgentExecutionRecorder()
    reply = model.complete(
        messages=(
            {"role": "system", "content": "Answer the question concisely. Do not expose chain-of-thought."},
            {"role": "user", "content": case.question},
        ),
        tools=(),
    )
    protocol_error = reply.protocol_error or ("unexpected_tool_call" if reply.tool_calls else None)
    recorder.record_llm(
        usage=reply.usage,
        latency_s=reply.latency_s,
        outcome=protocol_error or "ok",
        output_kind="protocol_error" if protocol_error else "message",
    )
    answer = "" if protocol_error else reply.text
    terminal_status = protocol_error or "final"
    answer_status = classify_answer_status(answer, evidence_status="not_called")
    if terminal_status == "final":
        terminal_status = {
            "abstained": "abstained",
            "empty": "empty_answer",
            "unsupported_answer": "unsupported_answer",
        }.get(answer_status, terminal_status)
    completed = terminal_status == "final" and answer_status in {"answered", "direct_answer"}
    retrieval = score_gtsqa_retrieval((), case)
    receipt = recorder.receipt(success=completed)
    receipt["retrieval_funnel"] = build_retrieval_funnel(
        turns=recorder.turns,
        tools=[],
        success=completed,
        evidence_class=evidence_class,
    )
    receipt.update(
        {
            "schema_version": "agentic_os.gtsqa_closed_book_receipt.v1",
            "benchmark": "GTSQA",
            "case_id": case.case_id,
            "workspace_id": case.workspace_id,
            "condition": "closed_book",
            "terminal_status": terminal_status,
            "answer_status": answer_status,
            "evidence_class": evidence_class,
            "retrieved_triple_count": 0,
            "retrieval": retrieval.to_dict(),
            "answer_digest": _digest(answer) if answer else None,
            "answer_bytes": len(answer.encode("utf-8")),
        }
    )
    return GTSQAReActResult(answer=answer, retrieved_edges=(), retrieval=retrieval, receipt=receipt)


def _term_identifier(value: str) -> str:
    """Extract the stable identifier from the case-scoped RDF IRI."""
    token = str(value).strip().strip("<>")
    for marker in ("entity:", "predicate:"):
        if marker in token:
            return token.rsplit(marker, 1)[1]
    return token


def _row_value(row: Mapping[str, str], name: str) -> str | None:
    return row.get(name) or row.get(f"?{name}")


def _edges_from_result(result: OxigraphQueryResult) -> tuple[dict[str, str], ...]:
    edges: list[dict[str, str]] = []
    for row in result.rows:
        source, relation, target = (_row_value(row, name) for name in ("s", "p", "o"))
        if source is None or relation is None or target is None:
            continue
        edges.append(
            {
                "source": _term_identifier(source),
                "relation": _term_identifier(relation),
                "target": _term_identifier(target),
            }
        )
    return tuple(edges)


def _rdf_schema_slice(case: GTSQACase, *, relation_cap: int = 48) -> dict[str, Any]:
    """Return a bounded discovery observation, never an initial prompt hint."""
    by_relation: dict[str, str] = {}
    for edge in case.graph_edges:
        by_relation.setdefault(edge.relation_id, edge.relation_label)
    relation_ids = sorted(by_relation)[:relation_cap]
    return {
        "graph_iri": f"urn:seocho:gtsqa:case:{case.case_id}",
        "triple_variables_required": ["s", "p", "o"],
        "relation_count": len(by_relation),
        "relations": [{"id": relation_id, "label": by_relation[relation_id]} for relation_id in relation_ids],
        "truncated": len(by_relation) > len(relation_ids),
    }


def gtsqa_react_tools() -> tuple[dict[str, Any], ...]:
    """Return the exact native tool schema presented to the GTSQA model."""
    return (
        {
            "type": "function",
            "function": {
                "name": "inspect_graph_schema",
                "description": (
                    "Call exactly once before SPARQL construction when a factual question depends on "
                    "entities, attributes, relations, paths, counts, or provenance in the active RDF "
                    "benchmark graph. Do not call for greetings, formatting, or facts already supplied "
                    "in the conversation. Returns the authoritative bounded relation vocabulary and "
                    "graph IRI, never the answer."
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_sparql",
                "description": (
                    "Call only after inspect_graph_schema. Execute bounded read-only SELECT SPARQL using "
                    "only discovered graph and predicate identifiers and project ?s ?p ?o so evidence "
                    "triples can be scored. After rejection, change the query using the repair code; "
                    "never repeat an identical failed query. Empty results mean insufficient evidence, "
                    "not permission to answer from memory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "A read-only SELECT query derived from the discovered relation vocabulary; project ?s ?p ?o."}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
    )


def grbench_react_tools() -> tuple[dict[str, Any], ...]:
    """Return the exact native tool schema presented to the GRBench model."""
    return (
        {
            "type": "function",
            "function": {
                "name": "inspect_graph_schema",
                "description": (
                    "Call exactly once before Cypher construction when a factual question depends on named "
                    "entities, attributes, relations, paths, counts, citations, authorship, or provenance in "
                    "the active benchmark graph. Do not call for greetings, formatting, or facts already "
                    "supplied in the conversation. Returns the authoritative labels, relationship types, "
                    "properties, and workspace scope contract, never the answer."
                ),
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_cypher",
                "description": (
                    "Call only after inspect_graph_schema with bounded read-only Cypher derived only from the "
                    "returned schema. Every GRBenchNode pattern must carry {_workspace_id: $workspace_id}; "
                    "include RETURN and LIMIT. After rejection, change the query using its repair code and "
                    "never repeat an identical failed query. Empty results mean insufficient evidence, not "
                    "permission to answer from memory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "A bounded, read-only, workspace-scoped Cypher statement using only discovered "
                                "identifiers and properties."
                            ),
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        },
    )


def run_gtsqa_text2sparql_react(
    case: GTSQACase,
    *,
    model: NativeToolModel,
    max_turns: int = 4,
    executor_factory: Callable[[GTSQACase], GTSQAOxigraphExecutor] = GTSQAOxigraphExecutor,
    schema_discovery: Callable[[GTSQACase], Mapping[str, Any]] | None = None,
    before_tool: Callable[[str, GTSQACase], None] | None = None,
    require_retrieval: bool = False,
    evidence_class: str = "behavioral_baseline",
    system_addendum: str = "",
    private_trace: PrivateTrace | None = None,
) -> GTSQAReActResult:
    """Run a question-only, bounded native-tool ReAct Text2SPARQL episode.

    The graph schema is available *only* through ``inspect_graph_schema``.  The
    executor accepts SELECT queries only, and evidence scoring is restricted to
    projected ``?s ?p ?o`` triples.  A final message without a tool call ends
    the episode; a missing final message is reported as budget exhaustion.
    """
    if max_turns < 1:
        raise ValueError("max_turns must be positive")
    executor = executor_factory(case)
    discover = schema_discovery or _rdf_schema_slice
    recorder = AgentExecutionRecorder()
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Classify the request before answering. For factual questions about named entities, "
                "attributes, relations, paths, counts, or provenance in the active RDF benchmark graph, "
                "do not answer from memory. Start with no schema: call inspect_graph_schema exactly once, "
                "then call execute_sparql with bounded read-only SELECT using only returned identifiers "
                "and projecting ?s ?p ?o. On rejection, change the query using its error code; never "
                "repeat identical SPARQL. Answer only from non-empty evidence or explicitly abstain. "
                "Do not expose chain-of-thought."
                + (
                    " This is a plumbing validation run: call execute_sparql at least once before the final answer."
                    if require_retrieval
                    else ""
                )
                + system_addendum
            ),
        },
        {"role": "user", "content": case.question},
    ]
    def append_message(message: dict[str, Any]) -> None:
        messages.append(message)
        if private_trace is not None and message.get("role") == "tool":
            for event in reversed(private_trace.events):
                if event["event"] == "tool_call" and event.get("native_call_id") == message.get("tool_call_id"):
                    event["raw_tool_result"] = message.get("content")
                    private_trace._checkpoint(event)
                    break

    retrieved: list[dict[str, str]] = []
    failures = 0
    answer = ""
    terminal_status = "turn_budget_exhausted"
    executed_query = False
    inspected_schema = False
    for _turn in range(max_turns):
        if require_retrieval and not executed_query and hasattr(model, "complete_with_tool_choice"):
            forced_tool = "execute_sparql" if inspected_schema else "inspect_graph_schema"
            reply = model.complete_with_tool_choice(  # type: ignore[attr-defined]
                messages=messages,
                tools=gtsqa_react_tools(),
                tool_name=forced_tool,
            )
        else:
            reply = model.complete(messages=messages, tools=gtsqa_react_tools())
        protocol_outcome = (
            "tool_parse_error"
            if reply.protocol_error == "textual_tool_call_without_native_tool_calls"
            else reply.protocol_error
        )
        recorder.record_llm(
            usage=reply.usage,
            latency_s=reply.latency_s,
            outcome=protocol_outcome or "ok",
            output_kind="tool_call" if reply.tool_calls else "message",
        )
        if reply.protocol_error:
            failures += 1
            terminal_status = protocol_outcome
            break
        if not reply.tool_calls:
            if require_retrieval and not executed_query:
                append_message({"role": "assistant", "content": reply.text})
                append_message(
                    {
                        "role": "user",
                        "content": "Plumbing validation requires one execute_sparql call before a final answer.",
                    }
                )
                continue
            answer = reply.text
            terminal_status = "final"
            break
        append_message(
            {
                "role": "assistant",
                "content": reply.text or None,
                "tool_calls": [
                    {
                        "id": call.call_id or f"call_{_digest(call.name)[:12]}",
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(dict(call.arguments), sort_keys=True),
                        },
                    }
                    for call in reply.tool_calls
                ],
            }
        )
        for call in reply.tool_calls:
            call_id = call.call_id or f"call_{_digest(call.name)[:12]}"
            private_tool_event_id = (
                private_trace.begin_tool_call(
                    role="single_agent",
                    name=call.name,
                    arguments=dict(call.arguments),
                    native_call_id=call.call_id or None,
                    parent_event_id=private_trace.last_model_event_id,
                )
                if private_trace is not None
                else None
            )
            if call.name == "inspect_graph_schema":
                inspected_schema = True
                try:
                    if before_tool is not None:
                        observe_tool(recorder, "runtime_policy", before_tool, call.name, case)
                    observation = observe_tool(
                        recorder,
                        call.name,
                        discover,
                        case,
                        receipt=lambda value: {
                            "response_digest": _digest(json.dumps(value, sort_keys=True)),
                            "result_bytes": _json_bytes(value),
                        },
                    )
                    if private_tool_event_id is not None:
                        private_trace.finish_tool_call(private_tool_event_id, outcome="ok", result=observation)
                except Exception as error:
                    if private_tool_event_id is not None:
                        private_trace.finish_tool_call(private_tool_event_id, outcome="error", error=error)
                    raise
                append_message({"role": "tool", "tool_call_id": call_id, "content": json.dumps(observation, sort_keys=True)})
                continue
            if call.name == "execute_sparql":
                query = call.arguments.get("query")
                if not isinstance(query, str):
                    failures += 1
                    if private_tool_event_id is not None:
                        private_trace.finish_tool_call(
                            private_tool_event_id,
                            outcome="error",
                            result={"error_code": "query_must_be_a_string"},
                        )
                    append_message({"role": "tool", "tool_call_id": call_id, "content": '{"error":"query must be a string"}'})
                    continue
                try:
                    if before_tool is not None:
                        observe_tool(recorder, "runtime_policy", before_tool, call.name, case)
                    result = observe_tool(
                        recorder,
                        call.name,
                        executor.execute_select,
                        query,
                        receipt=lambda value: value.receipt,
                        error_receipt=lambda error: {
                            "error_code": (
                                "query_policy_rejected"
                                if isinstance(error, ValueError)
                                else "query_syntax_or_execution_error"
                            )
                        },
                    )
                    executed_query = True
                except (ValueError, RuntimeError) as error:
                    failures += 1
                    error_code = (
                        "query_policy_rejected"
                        if isinstance(error, ValueError)
                        else "query_syntax_or_execution_error"
                    )
                    if private_tool_event_id is not None:
                        private_trace.finish_tool_call(
                            private_tool_event_id,
                            outcome="error",
                            error=error,
                            result={"error_code": error_code},
                        )
                    append_message({"role": "tool", "tool_call_id": call_id, "content": json.dumps({"error_code": error_code})})
                    continue
                edges = _edges_from_result(result)
                retrieved.extend(edges)
                tool_observation = {"rows": list(result.rows), "truncated": result.receipt["truncated"]}
                if private_tool_event_id is not None:
                    private_trace.finish_tool_call(private_tool_event_id, outcome="ok", result=tool_observation)
                # The model sees bounded evidence but execution receipt stays content-free.
                append_message(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(tool_observation, sort_keys=True),
                    }
                )
                continue
            failures += 1
            recorder.record_tool(name=call.name, latency_s=0.0, outcome="unsupported")
            if private_tool_event_id is not None:
                private_trace.finish_tool_call(
                    private_tool_event_id,
                    outcome="error",
                    result={"error_code": "unsupported_tool"},
                )
            append_message({"role": "tool", "tool_call_id": call_id, "content": '{"error":"unsupported tool"}'})

    unique = tuple({(edge["source"], edge["relation"], edge["target"]): edge for edge in retrieved}.values())
    retrieval = score_gtsqa_retrieval(unique, case)
    evidence_status = "ok" if unique else ("empty" if executed_query else "not_called")
    answer_status = classify_answer_status(answer, evidence_status=evidence_status)
    if terminal_status == "final":
        terminal_status = {
            "abstained": "abstained",
            "empty": "empty_answer",
            "unsupported_answer": "unsupported_answer",
        }.get(answer_status, terminal_status)
    completed = terminal_status == "final" and answer_status in {"answered", "direct_answer"}
    receipt = recorder.receipt(success=completed)
    receipt["retrieval_funnel"] = build_retrieval_funnel(
        turns=recorder.turns,
        tools=recorder.tools,
        success=completed,
        evidence_class=evidence_class,
        forced_tool=require_retrieval,
        query_validated=any(
            event.get("name") == "execute_sparql" and event.get("outcome") == "ok"
            for event in recorder.tools
        ),
        result_rows=len(unique),
        gold_evidence_retrieved=retrieval.recall > 0,
    )
    query_events = [
        event for event in recorder.tools if event.get("name") == "execute_sparql"
    ]
    failed_query_indices = [
        index for index, event in enumerate(query_events) if event.get("outcome") != "ok"
    ]
    empty_query_indices = [
        index
        for index, event in enumerate(query_events)
        if event.get("outcome") == "ok"
        and int((event.get("details") or {}).get("rows") or 0) == 0
    ]
    repair_attempts = sum(
        1
        for index in range(1, len(query_events))
        if any(failed < index for failed in failed_query_indices)
    )
    repair_successes = sum(
        1
        for index, event in enumerate(query_events)
        if event.get("outcome") == "ok"
        and any(failed < index for failed in failed_query_indices)
    )
    empty_result_retries = sum(
        1
        for index in range(1, len(query_events))
        if any(empty < index for empty in empty_query_indices)
    )
    receipt.update(
        {
            "schema_version": "agentic_os.gtsqa_text2sparql_react_receipt.v1",
            "benchmark": "GTSQA",
            "case_id": case.case_id,
            "workspace_id": case.workspace_id,
            "max_turns": max_turns,
            "terminal_status": terminal_status,
            "answer_status": answer_status,
            "repair_failures": failures,
            "repair_count": repair_attempts,
            "query_retry_count": max(0, len(query_events) - 1),
            "empty_result_retry_count": empty_result_retries,
            "role_characteristics": {
                "single_agent": {
                    "schema_calls": sum(
                        event.get("name") == "inspect_graph_schema"
                        for event in recorder.tools
                    ),
                    "query_calls": len(query_events),
                    "query_failures": len(failed_query_indices),
                    "repair_attempts": repair_attempts,
                    "repair_successes": repair_successes,
                    "query_retries": max(0, len(query_events) - 1),
                    "empty_result_retries": empty_result_retries,
                }
            },
            "retrieval_required": require_retrieval,
            "evidence_class": evidence_class,
            "retrieved_triple_count": len(unique),
            "retrieval": retrieval.to_dict(),
            "answer_digest": _digest(answer) if answer else None,
            "answer_bytes": len(answer.encode("utf-8")),
        }
    )
    return GTSQAReActResult(answer=answer, retrieved_edges=unique, retrieval=retrieval, receipt=receipt)


class EvidenceTriple(BaseModel):
    """Strict-schema evidence row accepted by the Agents SDK."""

    s: str
    p: str
    o: str


class EvidenceHandoff(BaseModel):
    """Typed, bounded payload returned by the graph-query specialist."""

    evidence: list[EvidenceTriple] = Field(default_factory=list, max_length=100)
    execution_status: str = Field(pattern="^(ok|empty|error|not_called)$")
    query_digest: str = Field(min_length=64, max_length=64)
    evidence_digest: str = Field(min_length=64, max_length=64)
    error_code: str | None = Field(
        default=None,
        pattern="^(query_policy_rejected|query_syntax_or_execution_error|query_runtime_error|agent_tool_error)$",
    )

    @classmethod
    def from_rows(
        cls,
        *,
        rows: Sequence[Mapping[str, str]],
        query: str,
        status: str = "ok",
        error_code: str | None = None,
    ) -> "EvidenceHandoff":
        evidence = [
            EvidenceTriple(s=str(row["s"]), p=str(row["p"]), o=str(row["o"]))
            for row in rows[:100]
            if all(name in row for name in ("s", "p", "o"))
        ]
        serialized = [item.model_dump() for item in evidence]
        return cls(
            evidence=evidence,
            execution_status="empty" if status == "ok" and not evidence else status,
            query_digest=_digest(query),
            evidence_digest=_digest(json.dumps(serialized, sort_keys=True)),
            error_code=error_code,
        )

    @classmethod
    def not_called(cls) -> "EvidenceHandoff":
        """Represent an endogenous direct-answer path without inventing a query."""
        return cls.from_rows(rows=(), query="", status="not_called")

    def redacted_receipt(self) -> dict[str, Any]:
        return {
            "execution_status": self.execution_status,
            "evidence_count": len(self.evidence),
            "evidence_bytes": _json_bytes([item.model_dump() for item in self.evidence]),
            "query_digest": self.query_digest,
            "evidence_digest": self.evidence_digest,
            "error_code": self.error_code,
        }


class QuerySpecialistHandoff(BaseModel):
    """Minimal typed request passed from the orchestrator to the specialist."""

    task: str = Field(min_length=1, max_length=4_000)


@dataclass
class HandoffReceiptRecorder:
    """SDK hook-friendly content-free lifecycle recorder."""

    events: list[dict[str, Any]] = field(default_factory=list)
    _started: float = field(default_factory=time.perf_counter)
    _event_ordinal: int = 0

    def record(self, event: str, **fields: Any) -> None:
        self._event_ordinal += 1
        self.events.append(
            {
                "event_ordinal": self._event_ordinal,
                "event": event,
                "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 3),
                **fields,
            }
        )

    def receipt(self) -> dict[str, Any]:
        handoffs = [event for event in self.events if event["event"] == "handoff"]
        agent_tools = [event for event in self.events if event["event"] == "agent_tool"]
        agent_tool_returns = [
            event for event in self.events if event["event"] == "agent_tool_return"
        ]
        specialist_queries = [
            event
            for event in self.events
            if event.get("role") == "query_specialist"
            and event.get("event") == "tool"
            and event.get("name") == "execute_graph_query"
        ]
        failed_query_ordinals = [
            index
            for index, event in enumerate(specialist_queries)
            if event.get("outcome") == "error"
        ]
        empty_query_ordinals = [
            index
            for index, event in enumerate(specialist_queries)
            if event.get("outcome") == "empty"
            or (
                event.get("outcome") == "ok"
                and int(event.get("evidence_count") or 0) == 0
            )
        ]
        repair_attempts = sum(
            1
            for index in range(1, len(specialist_queries))
            if any(failed < index for failed in failed_query_ordinals)
        )
        repair_successes = sum(
            1
            for index, event in enumerate(specialist_queries)
            if event.get("outcome") in {"ok", "empty"}
            and any(failed < index for failed in failed_query_ordinals)
        )
        empty_result_retries = sum(
            1
            for index in range(1, len(specialist_queries))
            if any(empty < index for empty in empty_query_ordinals)
        )
        productive_retries = sum(
            1
            for index, event in enumerate(specialist_queries)
            if index > 0
            and event.get("outcome") == "ok"
            and int(event.get("evidence_count") or 0) > 0
            and any(
                prior < index
                for prior in (*failed_query_ordinals, *empty_query_ordinals)
            )
        )
        return {
            "schema_version": "agentic_os.agents_sdk_topology_receipt.v1",
            "handoff_count": len(handoffs),
            "handoff_failures": sum(event.get("outcome") != "ok" for event in handoffs),
            "agent_tool_count": len(agent_tools),
            "agent_tool_failures": sum(
                event.get("outcome") != "ok" for event in agent_tool_returns
            ),
            "repair_count": repair_attempts,
            "query_retry_count": max(0, len(specialist_queries) - 1),
            "empty_result_retry_count": empty_result_retries,
            "productive_retry_count": productive_retries,
            "role_characteristics": {
                "manager": {
                    "agent_tool_calls": len(agent_tools),
                    "agent_tool_failures": sum(
                        event.get("outcome") != "ok" for event in agent_tool_returns
                    ),
                    "repair_attempts": 0,
                    "repair_successes": 0,
                },
                "query_specialist": {
                    "schema_calls": sum(
                        event.get("role") == "query_specialist"
                        and event.get("event") == "tool"
                        and event.get("name") == "inspect_graph_schema"
                        for event in self.events
                    ),
                    "query_calls": len(specialist_queries),
                    "query_failures": len(failed_query_ordinals),
                    "repair_attempts": repair_attempts,
                    "repair_successes": repair_successes,
                    "query_retries": max(0, len(specialist_queries) - 1),
                    "empty_result_retries": empty_result_retries,
                    "productive_retries": productive_retries,
                },
            },
            "events": self.events,
            "wall_ms": round((time.perf_counter() - self._started) * 1000, 3),
        }


@dataclass(frozen=True)
class AgentsHandoffResult:
    """Private final answer/evidence plus a content-free two-stage receipt."""

    answer: str
    evidence: EvidenceHandoff
    receipt: dict[str, Any]


@dataclass
class AgentToolInvocationState:
    """Private state shared only inside one manager-to-specialist episode."""

    evidence: EvidenceHandoff | None = None
    specialist_usage: dict[str, int] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "requests": 0,
        }
    )
    invocation_count: int = 0
    evidence_encode_ms: float | None = None
    evidence_bytes: int | None = None


def _sdk_usage(result: Any) -> dict[str, int]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "requests": 0,
    }
    for response in getattr(result, "raw_responses", ()):
        usage = getattr(response, "usage", None)
        if usage is None:
            continue
        totals["requests"] += 1
        totals["input_tokens"] += _usage_int(getattr(usage, "input_tokens", 0))
        totals["output_tokens"] += _usage_int(getattr(usage, "output_tokens", 0))
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        totals["cached_tokens"] += _usage_int(
            getattr(input_details, "cached_tokens", 0)
        )
        totals["reasoning_tokens"] += _usage_int(
            getattr(output_details, "reasoning_tokens", 0)
        )
    return totals


def _usage_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _context_usage(value: Any) -> dict[str, int]:
    usage = getattr(value, "usage", None)
    if usage is None:
        context_wrapper = getattr(value, "context_wrapper", None)
        usage = getattr(context_wrapper, "usage", None)
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": _usage_int(getattr(usage, "input_tokens", 0)),
        "output_tokens": _usage_int(getattr(usage, "output_tokens", 0)),
        "cached_tokens": _usage_int(getattr(input_details, "cached_tokens", 0)),
        "reasoning_tokens": _usage_int(getattr(output_details, "reasoning_tokens", 0)),
        "requests": _usage_int(getattr(usage, "requests", 0)),
    }


def _usage_delta(after: Mapping[str, int], before: Mapping[str, int]) -> dict[str, int]:
    return {
        field: max(0, int(after.get(field, 0)) - int(before.get(field, 0)))
        for field in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "requests")
    }


async def run_agents_sdk_handoff_episode(
    *,
    question: str,
    model: Any,
    execute_query: Callable[[str], EvidenceHandoff],
    schema_discovery: Callable[[], Mapping[str, Any]],
    max_turns: int = 6,
    enable_thinking: bool = True,
    max_output_tokens: int = 512,
    evidence_class: str = "behavioral_baseline",
    query_language: str = "SPARQL",
    private_trace: PrivateTrace | None = None,
) -> AgentsHandoffResult:
    """Run orchestrator→specialist→orchestrator-final with a typed evidence boundary."""
    from agents import Agent, Runner

    audit = HandoffReceiptRecorder()
    orchestrator, _specialist, config = build_agents_sdk_handoff_workflow(
        model=model,
        execute_query=execute_query,
        schema_discovery=schema_discovery,
        recorder=audit,
        enable_thinking=enable_thinking,
        max_output_tokens=max_output_tokens,
        query_language=query_language,
        private_trace=private_trace,
    )
    delegated = await Runner.run(orchestrator, question, max_turns=max_turns, run_config=config)
    evidence = delegated.final_output_as(EvidenceHandoff)
    handoff_start = next(
        (event["elapsed_ms"] for event in reversed(audit.events) if event["event"] == "handoff"),
        None,
    )
    handoff_latency_ms = (
        round((time.perf_counter() - audit._started) * 1000 - float(handoff_start), 3)
        if handoff_start is not None
        else None
    )
    audit.record(
        "handoff_return",
        source="GraphQuerySpecialist",
        target="Orchestrator",
        outcome="ok",
        handoff_latency_ms=handoff_latency_ms,
        **evidence.redacted_receipt(),
    )
    finalizer = Agent(
        name="OrchestratorFinal",
        instructions="Answer the user using only the typed graph evidence. Be concise. Do not expose hidden reasoning.",
        model=(_private_role_model(model, role="manager_finalizer", trace=private_trace) if private_trace else model),
        model_settings=_agent_model_settings(enable_thinking, max_output_tokens),
    )
    private_input = json.dumps({"question": question, "evidence": evidence.model_dump()}, ensure_ascii=False)
    answered = await Runner.run(finalizer, private_input, max_turns=2, run_config=config)
    answer = str(answered.final_output or "")
    answer_status = classify_answer_status(answer, evidence_status=evidence.execution_status)
    receipt = audit.receipt()
    delegation_usage = _sdk_usage(delegated)
    finalization_usage = _sdk_usage(answered)
    tool_events = [
        {
            "name": event.get("name"),
            "outcome": event.get("outcome"),
            "event_ordinal": event.get("event_ordinal"),
            "started_elapsed_ms": event.get("started_elapsed_ms", event.get("elapsed_ms")),
            "latency_ms": event.get("latency_ms"),
        }
        for event in audit.events
        if event.get("event") == "tool"
    ]
    receipt["retrieval_funnel"] = build_retrieval_funnel(
        turns=[],
        tools=tool_events,
        success=bool(answer),
        evidence_class=evidence_class,
        query_validated=any(
            event.get("name") == "execute_graph_query" and event.get("outcome") in {"ok", "empty"}
            for event in tool_events
        ),
        query_executed=any(event.get("name") == "execute_graph_query" for event in tool_events),
        result_rows=len(evidence.evidence),
    )
    receipt.update(
        {
            "schema_version": "agentic_os.agents_sdk_handoff_episode.v1",
            "delegation_usage": delegation_usage,
            "finalization_usage": finalization_usage,
            "turn_count": delegation_usage["requests"] + finalization_usage["requests"],
            "evidence_class": evidence_class,
            "answer_digest": _digest(answer) if answer else None,
            "answer_bytes": len(answer.encode("utf-8")),
            "answer_status": answer_status,
            "success": answer_status in {"answered", "direct_answer"},
        }
    )
    return AgentsHandoffResult(answer=answer, evidence=evidence, receipt=receipt)


async def run_agents_sdk_agent_tool_episode(
    *,
    question: str,
    model: Any,
    execute_query: Callable[[str], EvidenceHandoff],
    schema_discovery: Callable[[], Mapping[str, Any]],
    max_turns: int = 6,
    specialist_max_turns: int = 5,
    enable_thinking: bool = True,
    max_output_tokens: int = 512,
    evidence_class: str = "behavioral_baseline",
    query_language: str = "SPARQL",
    specialist_instructions_addendum: str = "",
    shared_prefix: str = "",
    private_trace: PrivateTrace | None = None,
) -> AgentsHandoffResult:
    """Run manager→specialist-as-tool→manager with endogenous retrieval choice."""
    from agents import Runner

    audit = HandoffReceiptRecorder()
    manager, _specialist, config, state = build_agents_sdk_agent_tool_workflow(
        model=model,
        execute_query=execute_query,
        schema_discovery=schema_discovery,
        recorder=audit,
        enable_thinking=enable_thinking,
        max_output_tokens=max_output_tokens,
        query_language=query_language,
        specialist_max_turns=specialist_max_turns,
        specialist_instructions_addendum=specialist_instructions_addendum,
        shared_prefix=shared_prefix,
        private_trace=private_trace,
    )
    answered = await Runner.run(manager, question, max_turns=max_turns, run_config=config)
    answer = str(answered.final_output or "")
    evidence = state.evidence or EvidenceHandoff.not_called()
    answer_status = classify_answer_status(answer, evidence_status=evidence.execution_status)
    receipt = audit.receipt()
    manager_usage = _sdk_usage(answered)
    receipt["role_characteristics"]["manager"]["usage"] = manager_usage
    receipt["role_characteristics"]["query_specialist"]["usage"] = state.specialist_usage
    tool_events = [
        {
            "name": event.get("name"),
            "outcome": event.get("outcome"),
            "event_ordinal": event.get("event_ordinal"),
            "started_elapsed_ms": event.get("started_elapsed_ms", event.get("elapsed_ms")),
            "latency_ms": event.get("latency_ms"),
        }
        for event in audit.events
        if event.get("event") in {"agent_tool", "tool"}
    ]
    receipt["retrieval_funnel"] = build_retrieval_funnel(
        turns=[],
        tools=tool_events,
        success=bool(answer),
        evidence_class=evidence_class,
        query_validated=any(
            event.get("name") == "execute_graph_query"
            and event.get("outcome") in {"ok", "empty"}
            for event in tool_events
        ),
        query_executed=any(
            event.get("name") == "execute_graph_query" for event in tool_events
        ),
        result_rows=len(evidence.evidence) if state.invocation_count else None,
    )
    receipt.update(
        {
            "schema_version": "agentic_os.agents_sdk_agent_tool_episode.v1",
            "topology": "manager_specialist_as_tool",
            "manager_usage": manager_usage,
            "specialist_usage": state.specialist_usage,
            "turn_count": manager_usage["requests"] + state.specialist_usage["requests"],
            "evidence_class": evidence_class,
            "answer_digest": _digest(answer) if answer else None,
            "answer_bytes": len(answer.encode("utf-8")),
            "answer_status": answer_status,
            "success": answer_status in {"answered", "direct_answer"},
        }
    )
    return AgentsHandoffResult(answer=answer, evidence=evidence, receipt=receipt)


class LLMRequestTimer:
    """Client-observed timing of every chat-completion HTTP request.

    The Agents SDK issues manager and specialist model calls through one
    ``AsyncOpenAI`` client, so the per-call latencies the single-agent ReAct
    runner records around ``chat.completions.create`` were never available to
    the multi-agent runners.  They fell back to a vLLM ``/metrics`` window,
    which is absent in appliance mode (the enclosing workload owns the
    sampler), and reported ``llm_ms = 0`` with the whole wall in the
    middleware residual (V17 attempt-window analysis).

    This timer hooks the underlying httpx client.  Latency is measured from
    request send to response headers; for non-streaming completions vLLM only
    sends headers after generation completes, so this equals the request wall
    as the client sees it.  When a :class:`PrivateTrace` context is active, the
    same hook also retains the request/response body in the caller's private
    artifact; otherwise it remains timing-only.
    """

    def __init__(self, *, capture_token_ids: bool = False) -> None:
        self.capture_token_ids = capture_token_ids
        self.requests: list[dict[str, Any]] = []
        self._epoch = time.perf_counter()

    def reset(self) -> None:
        self.requests = []
        self._epoch = time.perf_counter()

    async def on_request(self, request: Any) -> None:
        request.extensions["seocho_started_perf_s"] = time.perf_counter()
        context = _PRIVATE_MODEL_CONTEXT.get()
        if context is not None:
            request.extensions["seocho_private_model_context"] = context
            try:
                request_body = json.loads(request.content.decode("utf-8"))
            except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
                request_body = request.content.decode("utf-8", errors="replace")
            if self.capture_token_ids and isinstance(request_body, dict):
                request_body["return_token_ids"] = True
                request._content = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode()
                import httpx
                request.stream = httpx.ByteStream(request.content)
                request.headers["Content-Length"] = str(len(request.content))
            request.extensions["seocho_private_request_body"] = _private_jsonable(request_body)
            trace = context["trace"]
            trace.record_http(str(context["event_id"]), request_body=request_body,
                response_body=None, status_code=0, latency_ms=0, finish_reason=None,
                wire_request=request.content)
            event = next(e for e in trace.events if e["event_id"] == context["event_id"])
            request.extensions["seocho_private_attempt_index"] = len(event["http_attempts"]) - 1

    async def on_response(self, response: Any) -> None:
        started = response.request.extensions.get("seocho_started_perf_s")
        if started is None:
            return
        finished = time.perf_counter()
        self.requests.append(
            {
                "path": str(response.request.url.path),
                "status_code": int(response.status_code),
                "started_elapsed_ms": round((started - self._epoch) * 1000, 3),
                "latency_ms": round((finished - started) * 1000, 3),
            }
        )
        context = response.request.extensions.get("seocho_private_model_context")
        if context is not None:
            raw_body = None
            try:
                raw_body = await response.aread()
                response_body = json.loads(raw_body.decode("utf-8"))
            except Exception as error:
                response_body = {"private_capture_error": type(error).__name__}
            choices = response_body.get("choices") if isinstance(response_body, Mapping) else None
            choice = choices[0] if isinstance(choices, list) and choices else {}
            finish_reason = choice.get("finish_reason") if isinstance(choice, Mapping) else None
            trace = context.get("trace")
            if isinstance(trace, PrivateTrace):
                attempt_index = response.request.extensions["seocho_private_attempt_index"]
                trace.record_http(
                    str(context["event_id"]),
                    request_body=response.request.extensions.get("seocho_private_request_body"),
                    response_body=response_body,
                    status_code=int(response.status_code),
                    latency_ms=(finished - started) * 1000,
                    finish_reason=str(finish_reason) if finish_reason is not None else None,
                    wire_request=response.request.content, wire_response=raw_body,
                    attempt_index=attempt_index,
                )

    @property
    def completion_requests(self) -> list[dict[str, Any]]:
        return [item for item in self.requests if item["path"].endswith("/chat/completions")]

    def total_ms(self) -> float:
        return round(sum(item["latency_ms"] for item in self.completion_requests), 3)

    def receipt(self) -> dict[str, Any]:
        items = self.completion_requests
        return {
            "source": "client_http_request_timer",
            "boundary": "request_sent_to_response_headers",
            "request_count": len(items),
            "llm_ms": self.total_ms(),
            "requests": items,
        }

    def http_client(self) -> Any:
        import httpx

        return httpx.AsyncClient(event_hooks={"request": [self.on_request], "response": [self.on_response]})


def _private_tool_output(tool: Any, trace: PrivateTrace) -> Any:
    invoke = tool.on_invoke_tool

    async def capture(context: Any, input_json: str) -> Any:
        token = _PRIVATE_TOOL_CONTEXT.set(context)
        start = len(trace.events)
        try:
            output = await invoke(context, input_json)
            for event in trace.events[start:]:
                if event["event"] == "tool_call" and event.get("native_call_id") == getattr(context, "tool_call_id", None):
                    event["raw_tool_result"] = _private_jsonable(output)
                    trace._checkpoint(event)
            return output
        finally:
            _PRIVATE_TOOL_CONTEXT.reset(token)

    tool.on_invoke_tool = capture
    return tool


def _private_role_model(model: Any, *, role: str, trace: PrivateTrace) -> Any:
    """Tag SDK calls with their agent role while delegating provider behavior."""

    base_model_type = type(model)

    class _RoleModel(base_model_type):
        def __init__(self, delegate: Any) -> None:
            self.delegate = delegate

        def __getattr__(self, name: str) -> Any:
            return getattr(self.delegate, name)

        async def get_response(self, system_instructions: Any, input: Any, model_settings: Any, tools: Any, output_schema: Any, handoffs: Any, tracing: Any, *, previous_response_id: Any = None, conversation_id: Any = None, prompt: Any = None) -> Any:
            event_id = trace.begin_model_call(
                role=role,
                system_instructions=system_instructions,
                input=input,
                tools=[
                    {
                        "name": getattr(tool, "name", None),
                        "description": getattr(tool, "description", None),
                        "parameters": _private_jsonable(getattr(tool, "params_json_schema", None)),
                    }
                    for tool in (tools or [])
                ],
                model_settings=model_settings,
            )
            token = _PRIVATE_MODEL_CONTEXT.set(
                {"trace": trace, "event_id": event_id, "role": role, "turn_id": trace.last_model_turn_id}
            )
            try:
                response = await self.delegate.get_response(
                    system_instructions,
                    input,
                    model_settings,
                    tools,
                    output_schema,
                    handoffs,
                    tracing,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    prompt=prompt,
                )
            except Exception as error:
                trace.finish_model_call(event_id, error=error)
                raise
            else:
                trace.finish_model_call(event_id, response=response)
                return response
            finally:
                _PRIVATE_MODEL_CONTEXT.reset(token)

        def get_retry_advice(self, request: Any) -> Any:
            delegate = getattr(self.delegate, "get_retry_advice", None)
            return delegate(request) if callable(delegate) else None

    return _RoleModel(model)


def make_vllm_chat_model(
    *, base_url: str, api_key: str, model: str, request_timer: LLMRequestTimer | None = None
) -> Any:
    """Build the Agents SDK model adapter for an OpenAI-compatible vLLM URL.

    The endpoint URL is supplied by the caller; this helper neither reads
    credentials nor persists them.  Callers should create a ``RunConfig`` with
    ``tracing_disabled=True`` (or a redacting exporter) for experiments.  Pass
    a :class:`LLMRequestTimer` to obtain client-observed per-request latency.
    """
    from agents import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    if request_timer is not None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, http_client=request_timer.http_client())
    else:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return OpenAIChatCompletionsModel(model=model, openai_client=client)


def _query_error_code(error: Exception) -> str:
    if isinstance(error, ValueError):
        return "query_policy_rejected"
    if isinstance(error, RuntimeError):
        return "query_syntax_or_execution_error"
    return "query_runtime_error"


def _discovery_tool_description(query_language: str) -> str:
    tools = (
        grbench_react_tools()
        if query_language.casefold() == "cypher"
        else gtsqa_react_tools()
    )
    return str(tools[0]["function"]["description"])


def build_agents_sdk_agent_tool_workflow(
    *,
    model: Any,
    execute_query: Callable[[str], EvidenceHandoff],
    schema_discovery: Callable[[], Mapping[str, Any]],
    recorder: HandoffReceiptRecorder | None = None,
    enable_thinking: bool = True,
    max_output_tokens: int = 512,
    query_language: str = "SPARQL",
    specialist_max_turns: int = 5,
    specialist_instructions_addendum: str = "",
    shared_prefix: str = "",
    private_trace: PrivateTrace | None = None,
) -> tuple[Any, Any, Any, AgentToolInvocationState]:
    """Create a manager that may call a graph specialist as a normal tool.

    Unlike a handoff, the nested specialist receives a generated task rather
    than the manager's full conversation and returns typed evidence as a tool
    observation. Control then remains with the manager for answer synthesis.
    """
    from agents import Agent, RunConfig, function_tool

    if specialist_max_turns < 1:
        raise ValueError("specialist_max_turns must be positive")
    audit = recorder or HandoffReceiptRecorder()
    state = AgentToolInvocationState()
    manager_model = _private_role_model(model, role="manager", trace=private_trace) if private_trace else model
    specialist_model = _private_role_model(model, role="query_specialist", trace=private_trace) if private_trace else model

    @function_tool(
        name_override="inspect_graph_schema",
        description_override=_discovery_tool_description(query_language),
    )
    def inspect_graph_schema() -> str:
        started = time.perf_counter()
        started_elapsed_ms = (started - audit._started) * 1000
        private_event_id = (
            private_trace.begin_tool_call(
                role="query_specialist",
                name="inspect_graph_schema",
                arguments={},
                parent_event_id=private_trace.last_model_event_id,
            )
            if private_trace is not None
            else None
        )
        try:
            value = schema_discovery()
        except Exception as error:
            if private_event_id is not None:
                private_trace.finish_tool_call(private_event_id, outcome="error", error=error)
            audit.record(
                "tool",
                role="query_specialist",
                name="inspect_graph_schema",
                outcome="error",
                started_elapsed_ms=round(started_elapsed_ms, 3),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            raise
        if private_event_id is not None:
            private_trace.finish_tool_call(private_event_id, outcome="ok", result=value)
        audit.record(
            "tool",
            role="query_specialist",
            name="inspect_graph_schema",
            outcome="ok",
            started_elapsed_ms=round(started_elapsed_ms, 3),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            output_bytes=_json_bytes(value),
        )
        return json.dumps(value, sort_keys=True)

    @function_tool(
        name_override="execute_graph_query",
        description_override=(
            f"Call only after inspect_graph_schema to execute bounded read-only {query_language} using "
            "only discovered identifiers. Return evidence columns s, p, and o. After rejection, change "
            "the query using its semantic repair code; never repeat an identical failed query. Empty "
            "evidence requires an empty handoff, not an answer from memory."
        ),
    )
    def execute_graph_query(query: str) -> str:
        started = time.perf_counter()
        started_elapsed_ms = (started - audit._started) * 1000
        private_event_id = (
            private_trace.begin_tool_call(
                role="query_specialist",
                name="execute_graph_query",
                arguments={"query": query},
                parent_event_id=private_trace.last_model_event_id,
            )
            if private_trace is not None
            else None
        )
        try:
            evidence = execute_query(query)
        except Exception as error:
            evidence = EvidenceHandoff.from_rows(
                rows=(),
                query=query,
                status="error",
                error_code=_query_error_code(error),
            )
            if private_event_id is not None:
                private_trace.finish_tool_call(
                    private_event_id,
                    outcome="error",
                    error=error,
                    result=evidence.model_dump(),
                )
            audit.record(
                "tool",
                role="query_specialist",
                name="execute_graph_query",
                outcome="error",
                started_elapsed_ms=round(started_elapsed_ms, 3),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                **evidence.redacted_receipt(),
            )
            return evidence.model_dump_json()
        if private_event_id is not None:
            private_trace.finish_tool_call(
                private_event_id,
                outcome=evidence.execution_status,
                result=evidence.model_dump(),
            )
        audit.record(
            "tool",
            role="query_specialist",
            name="execute_graph_query",
            outcome=evidence.execution_status,
            started_elapsed_ms=round(started_elapsed_ms, 3),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            **evidence.redacted_receipt(),
        )
        return evidence.model_dump_json()

    if private_trace is not None:
        _private_tool_output(inspect_graph_schema, private_trace)
        _private_tool_output(execute_graph_query, private_trace)

    specialist = Agent(
        name="GraphQuerySpecialist",
        instructions=(
            shared_prefix
            + f"You are the only {query_language} query specialist. The task field is a factual "
            "retrieval request, not permission to answer from memory. Call inspect_graph_schema "
            f"exactly once, then construct bounded read-only {query_language} using only discovered "
            "labels, predicates, properties, identifiers, and scope rules. Call execute_graph_query. "
            "On rejection, change the query using the returned error code; do not repeat an identical "
            "query. Stop after the bounded repair budget. Return only typed EvidenceHandoff evidence "
            "or an explicit empty/error status, never a prose answer or hidden reasoning."
            + specialist_instructions_addendum
        ),
        tools=[inspect_graph_schema, execute_graph_query],
        model=specialist_model,
        output_type=EvidenceHandoff,
        model_settings=_agent_model_settings(enable_thinking, max_output_tokens),
    )
    config = RunConfig(
        model=manager_model,
        tracing_disabled=True,
        workflow_name="agentic-os-graph-agent-tool",
    )

    async def _extract_evidence(result: Any) -> str:
        evidence = result.final_output_as(EvidenceHandoff)
        state.evidence = evidence
        state.specialist_usage = _sdk_usage(result)
        encode_started = time.perf_counter()
        encoded = evidence.model_dump_json()
        state.evidence_encode_ms = round((time.perf_counter() - encode_started) * 1000, 3)
        state.evidence_bytes = len(encoded.encode("utf-8"))
        return encoded

    def _agent_tool_failure(_: Any, __: Exception) -> str:
        evidence = EvidenceHandoff.from_rows(
            rows=(),
            query="",
            status="error",
            error_code="agent_tool_error",
        )
        state.evidence = evidence
        encode_started = time.perf_counter()
        encoded = evidence.model_dump_json()
        state.evidence_encode_ms = round((time.perf_counter() - encode_started) * 1000, 3)
        state.evidence_bytes = len(encoded.encode("utf-8"))
        return encoded

    specialist_tool = specialist.as_tool(
        tool_name="retrieve_graph_evidence",
        tool_description=(
            "Call exactly once for a factual question whose answer depends on named entities, attributes, "
            "relations, paths, counts, or provenance in the active benchmark graph. This includes "
            "named-paper, author, citation, and knowledge-graph relation questions. Do not call for "
            "greetings, formatting, or questions answerable solely from the current conversation. "
            f"The specialist discovers schema and executes bounded read-only {query_language}; it "
            "returns typed evidence or an explicit empty/error status. After it returns, never call "
            "it again in the same episode."
        ),
        custom_output_extractor=_extract_evidence,
        run_config=config,
        max_turns=specialist_max_turns,
        failure_error_function=_agent_tool_failure,
        parameters=QuerySpecialistHandoff,
        is_enabled=lambda _context, _agent: state.invocation_count == 0,
    )
    invoke_specialist = specialist_tool.on_invoke_tool

    async def _audited_invoke(context: Any, input_json: str) -> Any:
        started = time.perf_counter()
        started_elapsed_ms = (started - audit._started) * 1000
        usage_before = _context_usage(context)
        state.invocation_count += 1
        try:
            private_arguments = json.loads(input_json)
        except json.JSONDecodeError:
            private_arguments = input_json
        private_event_id = (
            private_trace.begin_tool_call(
                role="manager",
                name="retrieve_graph_evidence",
                arguments=private_arguments,
                parent_event_id=private_trace.last_model_event_id,
            )
            if private_trace is not None
            else None
        )
        audit.record(
            "agent_tool",
            role="manager",
            name="retrieve_graph_evidence",
            outcome="selected",
            started_elapsed_ms=round(started_elapsed_ms, 3),
            latency_ms=0.0,
            payload_bytes=len(input_json.encode("utf-8")),
            payload_digest=_digest(input_json),
        )
        branch_token = _PRIVATE_BRANCH_CONTEXT.set({
            "role": "query_specialist", "branch_id": private_event_id,
            "parent_event_id": private_event_id,
            "parent_call_id": private_trace.last_model_event_id if private_trace else None,
        }) if private_event_id is not None else None
        try:
            output = await invoke_specialist(context, input_json)
        except Exception as error:
            observed_usage = _usage_delta(_context_usage(context), usage_before)
            if observed_usage["requests"]:
                state.specialist_usage = observed_usage
            audit.record(
                "agent_tool_return",
                role="manager",
                name="retrieve_graph_evidence",
                outcome="error",
                started_elapsed_ms=round(started_elapsed_ms, 3),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                payload_bytes=len(input_json.encode("utf-8")),
                payload_digest=_digest(input_json),
            )
            if private_event_id is not None:
                private_trace.finish_tool_call(private_event_id, outcome="error", error=error)
            raise
        finally:
            if branch_token is not None:
                _PRIVATE_BRANCH_CONTEXT.reset(branch_token)
        observed_usage = _usage_delta(_context_usage(context), usage_before)
        if observed_usage["requests"]:
            state.specialist_usage = observed_usage
        evidence = state.evidence
        outcome = "error" if evidence and evidence.execution_status == "error" else "ok"
        audit.record(
            "agent_tool_return",
            role="manager",
            name="retrieve_graph_evidence",
            outcome=outcome,
            started_elapsed_ms=round(started_elapsed_ms, 3),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            payload_bytes=len(input_json.encode("utf-8")),
            payload_digest=_digest(input_json),
            evidence_encode_ms=state.evidence_encode_ms,
            output_bytes=state.evidence_bytes,
            **(evidence.redacted_receipt() if evidence else {}),
        )
        if private_event_id is not None:
            try:
                private_output = json.loads(output)
            except (TypeError, json.JSONDecodeError):
                private_output = output
            private_trace.finish_tool_call(private_event_id, outcome=outcome, result=private_output)
        return output

    specialist_tool.on_invoke_tool = _audited_invoke
    if private_trace is not None:
        _private_tool_output(specialist_tool, private_trace)
    manager = Agent(
        name="UserRequestAgent",
        instructions=(
            shared_prefix
            + "Classify the request before answering. For factual questions about named entities, "
            "attributes, relations, paths, counts, citations, authorship, or provenance in the active "
            "benchmark graph, call retrieve_graph_evidence exactly once; never answer those facts "
            "from memory. Direct answers are allowed only for greetings, formatting, or facts already "
            "stated in the conversation. Do not write graph queries yourself. After the specialist "
            "returns, never call it again: answer only from non-empty typed evidence, or explicitly "
            "abstain when evidence is empty or failed."
        ),
        tools=[specialist_tool],
        model=manager_model,
        model_settings=_agent_model_settings(enable_thinking, max_output_tokens),
    )
    return manager, specialist, config, state


def build_agents_sdk_handoff_workflow(
    *,
    model: Any,
    execute_query: Callable[[str], EvidenceHandoff],
    schema_discovery: Callable[[], Mapping[str, Any]],
    recorder: HandoffReceiptRecorder | None = None,
    enable_thinking: bool = True,
    max_output_tokens: int = 512,
    query_language: str = "SPARQL",
    private_trace: PrivateTrace | None = None,
) -> tuple[Any, Any, Any]:
    """Create orchestrator, specialist, and a tracing-disabled SDK RunConfig.

    The specialist owns graph tools.  The orchestrator has no query tool and
    can only transfer to it.  Production runners resume an answering turn with
    the typed ``EvidenceHandoff`` returned by the specialist; that continuation
    is intentionally outside the graph-specialist capability boundary.
    """
    from agents import Agent, RunConfig, function_tool, handoff

    audit = recorder or HandoffReceiptRecorder()
    orchestrator_model = _private_role_model(model, role="manager", trace=private_trace) if private_trace else model
    specialist_model = _private_role_model(model, role="query_specialist", trace=private_trace) if private_trace else model

    @function_tool(
        name_override="inspect_graph_schema",
        description_override=_discovery_tool_description(query_language),
    )
    def inspect_graph_schema() -> str:
        started = time.perf_counter()
        started_elapsed_ms = (started - audit._started) * 1000
        try:
            value = schema_discovery()
        except Exception:
            audit.record(
                "tool",
                name="inspect_graph_schema",
                outcome="error",
                started_elapsed_ms=round(started_elapsed_ms, 3),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            raise
        audit.record(
            "tool",
            name="inspect_graph_schema",
            outcome="ok",
            started_elapsed_ms=round(started_elapsed_ms, 3),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            output_bytes=_json_bytes(value),
        )
        return json.dumps(value, sort_keys=True)

    @function_tool(
        name_override="execute_graph_query",
        description_override=(
            f"Call only after inspect_graph_schema to execute bounded read-only {query_language} using "
            "only discovered identifiers. Return typed s/p/o evidence; change rejected queries using "
            "the repair code and never repeat an identical failed query."
        ),
    )
    def execute_graph_query(query: str) -> str:
        started = time.perf_counter()
        started_elapsed_ms = (started - audit._started) * 1000
        try:
            evidence = execute_query(query)
        except Exception:
            evidence = EvidenceHandoff.from_rows(rows=(), query=query, status="error")
            audit.record(
                "tool",
                name="execute_graph_query",
                outcome="error",
                started_elapsed_ms=round(started_elapsed_ms, 3),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                **evidence.redacted_receipt(),
            )
            return evidence.model_dump_json()
        audit.record(
            "tool",
            name="execute_graph_query",
            outcome=evidence.execution_status,
            started_elapsed_ms=round(started_elapsed_ms, 3),
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            **evidence.redacted_receipt(),
        )
        return evidence.model_dump_json()

    specialist = Agent(
        name="GraphQuerySpecialist",
        handoff_description=(
            f"Use for factual questions that require entities, attributes, relations, paths, counts, or "
            f"provenance from the active graph. Discover schema, execute bounded read-only {query_language}, "
            "and return typed evidence; do not use for greetings, formatting, or conversation-supplied facts."
        ),
        instructions=(
            f"You are the only {query_language} query specialist. Treat the handoff as a factual retrieval "
            "request, not permission to answer from memory. Call inspect_graph_schema exactly once, then "
            f"construct bounded read-only {query_language} using only discovered identifiers and call "
            "execute_graph_query. Change rejected queries using the returned repair code and never repeat "
            "an identical failure. Return typed evidence or an explicit empty/error status; do not answer "
            "the user."
        ),
        tools=[inspect_graph_schema, execute_graph_query],
        model=specialist_model,
        output_type=EvidenceHandoff,
        model_settings=_agent_model_settings(enable_thinking, max_output_tokens),
    )

    def _on_handoff(_: Any, payload: QuerySpecialistHandoff) -> None:
        audit.record(
            "handoff",
            source="Orchestrator",
            target="GraphQuerySpecialist",
            outcome="ok",
            payload_type=type(payload).__name__,
            payload_bytes=len(payload.task.encode("utf-8")),
            payload_digest=_digest(payload.task),
        )

    orchestrator = Agent(
        name="Orchestrator",
        instructions=(
            "Classify the request before answering. For factual questions about named entities, attributes, "
            "relations, paths, counts, citations, authorship, or provenance in the active graph, transfer "
            "exactly once to GraphQuerySpecialist and never answer those facts from memory. Do not transfer "
            "greetings, formatting requests, or facts already supplied in the conversation. Do not write "
            "graph queries yourself; after evidence returns, answer only from non-empty evidence or abstain."
        ),
        handoffs=[handoff(specialist, on_handoff=_on_handoff, input_type=QuerySpecialistHandoff)],
        model=orchestrator_model,
        model_settings=_agent_model_settings(enable_thinking, max_output_tokens),
    )
    # Explicitly avoid SDK default trace uploads because it can include prompts/tool
    # payloads. A caller can attach a redacting processor separately.
    config = RunConfig(model=orchestrator_model, tracing_disabled=True, workflow_name="agentic-os-graph-handoff")
    return orchestrator, specialist, config


def _agent_model_settings(enable_thinking: bool, max_output_tokens: int = 512) -> Any:
    """Keep vLLM reasoning mode explicit for every SDK-owned model call."""
    from agents import ModelSettings

    if max_output_tokens < 1:
        raise ValueError("max_output_tokens must be positive")

    return ModelSettings(
        temperature=0.0,
        max_tokens=max_output_tokens,
        include_usage=True,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )


__all__ = [
    "EvidenceHandoff",
    "EvidenceTriple",
    "AgentsHandoffResult",
    "AgentToolInvocationState",
    "GTSQAReActResult",
    "HandoffReceiptRecorder",
    "NativeModelReply",
    "NativeToolCall",
    "NativeToolModel",
    "OpenAICompatibleNativeToolModel",
    "PrivateTrace",
    "QuerySpecialistHandoff",
    "build_agents_sdk_agent_tool_workflow",
    "build_agents_sdk_handoff_workflow",
    "classify_answer_status",
    "contains_unparsed_tool_markup",
    "gtsqa_react_tools",
    "grbench_react_tools",
    "LLMRequestTimer",
    "make_vllm_chat_model",
    "redacted_episode_error",
    "run_gtsqa_text2sparql_react",
    "run_gtsqa_closed_book",
    "run_agents_sdk_agent_tool_episode",
    "run_agents_sdk_handoff_episode",
]
