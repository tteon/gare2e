"""Shared condition contract for the SEOCHO-free graph-agent baseline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


RAW_CONDITIONS = ("closed_book", "tool_auto", "forced_plumbing")


@dataclass(frozen=True)
class RawConditionSpec:
    """One raw condition with an evidence class and native-tool policy."""

    name: str
    evidence_class: str
    tools_enabled: bool
    forced: bool

    def tool_choice(self, *, schema_observed: bool, query_executed: bool) -> str | Mapping[str, Any] | None:
        if not self.tools_enabled:
            return None
        if not self.forced or query_executed:
            return "auto"
        tool_name = "execute_graph_query" if schema_observed else "inspect_graph_schema"
        return {"type": "function", "function": {"name": tool_name}}


def raw_condition_spec(name: str, *, stage: str) -> RawConditionSpec:
    """Return the frozen condition semantics and reject invalid pooling."""
    if name not in RAW_CONDITIONS:
        raise ValueError(f"unsupported raw condition: {name}")
    if stage not in {"smoke", "main"}:
        raise ValueError(f"unsupported experiment stage: {stage}")
    if name == "forced_plumbing":
        if stage != "smoke":
            raise ValueError("forced_plumbing is smoke-only and cannot be main-comparable evidence")
        return RawConditionSpec(name, "plumbing_smoke", True, True)
    return RawConditionSpec(
        name,
        "main_comparable" if stage == "main" else "behavioral_baseline",
        name == "tool_auto",
        False,
    )


def openai_tool_fields(
    spec: RawConditionSpec,
    *,
    tools: Sequence[Mapping[str, Any]],
    schema_observed: bool,
    query_executed: bool,
    schema_tool_name: str,
    query_tool_name: str,
) -> dict[str, Any]:
    """Build only the OpenAI-compatible tool fields appropriate for a step."""
    choice = spec.tool_choice(schema_observed=schema_observed, query_executed=query_executed)
    if choice is None:
        return {}
    if isinstance(choice, Mapping):
        generic_name = str(choice["function"]["name"])
        actual_name = query_tool_name if generic_name == "execute_graph_query" else schema_tool_name
        choice = {"type": "function", "function": {"name": actual_name}}
    return {"tools": list(tools), "tool_choice": choice}


__all__ = [
    "RAW_CONDITIONS",
    "RawConditionSpec",
    "openai_tool_fields",
    "raw_condition_spec",
]
