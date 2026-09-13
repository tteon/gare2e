"""Content-free workload labels; lexical shape is not query intent or a DB plan."""
from __future__ import annotations

import re
from typing import Any


def query_shape(query: str) -> str:
    # Ignore comments, quoted values and quoted identifiers before lexical tests.
    code = re.sub(r"(?s)/\*.*?\*/|//[^\n]*|'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"|`(?:``|[^`])*`", " ", query)
    if re.search(r"\bORDER\s+BY\b", code, re.I):
        return "ranking"
    if re.search(r"\b(count|sum|avg|min|max|collect)\s*\(", code, re.I):
        return "aggregation"
    if re.search(r"\b(shortestPath|allShortestPaths)\s*\(|\[[^\]]*\*", code, re.I):
        return "path"
    if re.search(r"\bEXISTS\b", code, re.I):
        return "existence"
    if re.search(r"\bINTERSECT\b", code, re.I):
        return "intersection"
    if re.search(r"\bMATCH\b", code, re.I):
        return "neighborhood" if re.search(r"--|\]-|<-\[", code) else "entity_lookup"
    return "other"


def context_bytes(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """UTF-8 payload lengths, never called tokens or exclusive server accounting."""
    groups = {"system_schema": 0, "user": 0, "prior_generated": 0, "tool_observation": 0, "schema_observation": 0}
    roles = {"system": "system_schema", "user": "user", "assistant": "prior_generated", "tool": "tool_observation"}
    tool_names: dict[str, str] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            tool_names[str(call.get("id"))] = str((call.get("function") or {}).get("name"))
        role = roles.get(message.get("role"))
        content = message.get("content")
        if role == "tool_observation" and tool_names.get(str(message.get("tool_call_id"))) == "inspect_graph_schema":
            role = "schema_observation"
        if role == "user" and isinstance(content, str) and content.startswith("Result: "):
            role = "tool_observation"
        if role and isinstance(content, str):
            groups[role] += len(content.encode())
    return {"unit": "utf8_content_bytes", "components": groups,
            "token_components": None, "scope": "message content only; excludes tool arguments and chat framing"}
