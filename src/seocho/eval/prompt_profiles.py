"""Prompt-engineering profiles for the graph-agent benchmark lanes.

The prompt-engineering (PE) arm of the PE-vs-context-engineering study puts
everything the model must remember into the system prompt and then lets the
model manage its own multi-turn context.  ``baseline`` is the prompt the raw
B0 lanes have always used.  ``strengthened`` is the strongest fair PE we are
willing to give it: the scope rule stated with a concrete pattern, an explicit
read-only whitelist reminder, a repair table keyed by validator error code, and
a finalisation rule.  Nothing here injects schema or evidence; that would be
context engineering and belongs to the treatment arms.

Profiles are content-free with respect to the benchmark (no entities, no
answers) and are recorded in receipts by name and digest.
"""

from __future__ import annotations

import hashlib

PROMPT_PROFILES: tuple[str, ...] = ("baseline", "strengthened")

_STRENGTHENED_CYPHER = (
    " Scope rule, restated because it is enforced by a validator: every node and relationship "
    "pattern must carry the workspace filter, for example "
    "MATCH (a:GRBenchNode {_workspace_id: $workspace_id})-[r:GRBENCH_REL {_workspace_id: $workspace_id}]->"
    "(b:GRBenchNode {_workspace_id: $workspace_id}); the tool binds $workspace_id. Only MATCH/OPTIONAL "
    "MATCH/WITH/WHERE/RETURN/ORDER BY/LIMIT are accepted; any write clause, CALL, or LOAD is rejected. "
    "Repair table: missing_workspace_scope -> add the filter to the pattern that lacks it; "
    "query_policy_rejected -> remove the disallowed clause and keep the same intent; a syntax error -> "
    "fix only the reported token. Finalisation rule: as soon as execute_cypher returns non-empty rows, "
    "answer in one short sentence from those rows and stop; do not run further queries to confirm. "
    "Keep reasoning brief; the output budget is shared with the answer."
)

_STRENGTHENED_SPARQL = (
    " Scope rule, restated because it is enforced by a validator: use only identifiers returned by "
    "inspect_graph_schema, project exactly ?s ?p ?o, and keep the query a bounded read-only SELECT "
    "with a LIMIT; UPDATE, INSERT, DELETE, SERVICE, and unbounded patterns are rejected. Repair table: "
    "a policy rejection -> remove the disallowed construct and keep the intent; an unknown identifier -> "
    "replace it with one from the schema slice; a syntax error -> fix only the reported token. "
    "Finalisation rule: as soon as execute_sparql returns non-empty triples, answer in one short "
    "sentence from them and stop. Keep reasoning brief; the output budget is shared with the answer."
)


def system_prompt_addendum(profile: str, query_language: str) -> str:
    """Return the text appended to the baseline system prompt for ``profile``."""
    if profile not in PROMPT_PROFILES:
        raise ValueError(f"unknown prompt profile: {profile}")
    if profile == "baseline":
        return ""
    language = query_language.casefold()
    if language == "cypher":
        return _STRENGTHENED_CYPHER
    if language == "sparql":
        return _STRENGTHENED_SPARQL
    raise ValueError(f"unsupported query language for prompt profile: {query_language}")


def prompt_profile_receipt(profile: str, query_language: str) -> dict[str, str]:
    """Content-free receipt fields: profile name and digest of the addendum."""
    addendum = system_prompt_addendum(profile, query_language)
    return {
        "prompt_profile": profile,
        "prompt_profile_addendum_sha256": hashlib.sha256(addendum.encode("utf-8")).hexdigest(),
    }


__all__ = ["PROMPT_PROFILES", "prompt_profile_receipt", "system_prompt_addendum"]
