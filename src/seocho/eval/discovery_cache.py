"""Frozen discovery-context and paired-scheduling contracts for graph agents.

The direct and SEOCHO arms must not synthesize schema context independently at
run time.  This module freezes one response per case and makes the negative
context control auditable.  It deliberately contains no graph executor and no
SEOCHO runtime dependency, so the same artifact is usable by direct baselines.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .agentic_os_artifacts import canonical_digest, write_manifest


DISCOVERY_ARMS = ("B0", "C-", "S1")
# Optional arms a record may carry in addition (all records of a cache must agree):
# O1 = ontology-enriched vocabulary (lab 016), keyed by ``o1_response``.
OPTIONAL_DISCOVERY_ARMS = ("O1",)
_OPTIONAL_RECORD_KEYS = {"O1": "o1_response"}


def _expected_arms(contract: Mapping[str, Any]) -> tuple[str, ...]:
    arms = tuple(str(a) for a in contract.get("tool_availability", ()))
    if arms[: len(DISCOVERY_ARMS)] != DISCOVERY_ARMS or any(a not in OPTIONAL_DISCOVERY_ARMS for a in arms[len(DISCOVERY_ARMS):]):
        raise ValueError("all B0/C-/S1 arms must expose the same discovery tool")
    return arms
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_SAFE_NEGATIVE_WORDS = ("amber", "birch", "cobalt", "dawn", "elm", "fable", "granite", "harbor")


class TokenCounter(Protocol):
    """Counts tokens using the serving model's tokenizer."""

    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class WhitespaceTokenCounter:
    """Offline staging counter; never evidence for a serving-token claim."""

    def count(self, text: str) -> int:
        return len(text.split())


@dataclass(frozen=True)
class VLLMTokenizer:
    """Small client for vLLM's OpenAI-compatible ``/tokenize`` endpoint."""

    base_url: str
    model: str
    timeout_seconds: int = 30

    def count(self, text: str) -> int:
        request = Request(
            f"{self.base_url.rstrip('/')}/tokenize",
            data=json.dumps({"model": self.model, "prompt": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - configured benchmark endpoint
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"vLLM tokenize HTTP {error.code}: {detail}") from error
        tokens = payload.get("tokens")
        if not isinstance(tokens, list):
            raise RuntimeError("vLLM tokenize response did not contain a token list")
        return len(tokens)


def _canonical_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("discovery response must be a JSON object")
    return dict(value)


def _semantic_words(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set().union(*(_semantic_words(item) for item in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_semantic_words(item) for item in value)) if value else set()
    if isinstance(value, str):
        return {word.casefold() for word in _WORD.findall(value)}
    return set()


def _negative_like(value: Any, *, ordinal: list[int]) -> Any:
    """Mirror JSON shape while replacing all semantic leaf values.

    Object keys are part of the tool schema and intentionally remain stable;
    values are replaced using a closed synthetic vocabulary that cannot encode
    labels, relation names, entity IDs, or answer strings.
    """
    if isinstance(value, Mapping):
        return {str(key): _negative_like(item, ordinal=ordinal) for key, item in value.items()}
    if isinstance(value, list):
        return [_negative_like(item, ordinal=ordinal) for item in value]
    if isinstance(value, str):
        # Preserve an approximate lexical footprint before the exact global
        # token-match step.  Large schema responses (for example GTSQA
        # relation catalogs) otherwise collapse every label and URI to one
        # word, leaving a gap too large for the bounded filler knob.
        lexical_units = max(1, len(_WORD.findall(value)))
        words = [
            _SAFE_NEGATIVE_WORDS[(ordinal[0] + index) % len(_SAFE_NEGATIVE_WORDS)]
            for index in range(lexical_units)
        ]
        ordinal[0] += lexical_units
        return " ".join(words)
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return None


def _replace_first_string(value: Any, replacement: str) -> tuple[Any, bool]:
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        done = False
        for key, item in value.items():
            if not done:
                new_item, done = _replace_first_string(item, replacement)
                copied[str(key)] = new_item
            else:
                copied[str(key)] = item
        return copied, done
    if isinstance(value, list):
        copied_list: list[Any] = []
        done = False
        for item in value:
            if not done:
                new_item, done = _replace_first_string(item, replacement)
                copied_list.append(new_item)
            else:
                copied_list.append(item)
        return copied_list, done
    if isinstance(value, str):
        return replacement, True
    return value, False


def _structure(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _structure(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_structure(item) for item in value]
    return type(value).__name__


def build_negative_control(
    semantic_response: Mapping[str, Any],
    *,
    token_counter: TokenCounter,
    max_attempts: int = 2_048,
) -> dict[str, Any]:
    """Build a shape-identical irrelevant response with an exact token count.

    Exact matching is intentionally a hard gate.  If a serving tokenizer cannot
    represent an exact match through the fixed synthetic vocabulary, callers
    must revise the response template rather than silently accept a mismatch.
    """
    semantic = _json_payload(semantic_response)
    target = token_counter.count(_canonical_text(semantic))
    negative = _negative_like(semantic, ordinal=[0])
    if _structure(negative) != _structure(semantic):
        raise AssertionError("negative control did not preserve JSON structure")
    if token_counter.count(_canonical_text(negative)) == target:
        return negative
    # A single string leaf is deliberately reserved as a semantically inert
    # lexical-length knob.  Its position is stable, and its vocabulary remains
    # irrelevant.  We only accept an exact model-token count.
    def candidate_at(size: int, vocabulary: tuple[str, ...]) -> tuple[dict[str, Any], int]:
        filler = " ".join(vocabulary[index % len(vocabulary)] for index in range(size))
        candidate, changed = _replace_first_string(negative, filler)
        if not changed:
            raise AssertionError("negative control has no string leaf for token matching")
        return candidate, token_counter.count(_canonical_text(candidate))

    # Appending a whitespace-delimited safe word makes the candidate token
    # count non-decreasing for the serving tokenizers used by the benchmark.
    # Find the lower bound instead of issuing up to 512 remote /tokenize calls
    # per case.  We still verify an exact match and fail closed if a tokenizer
    # skips the target count.
    diagnostics: list[tuple[int, int]] = []
    # Different safe tokens have different segmentation boundaries.  Trying a
    # small fixed set avoids a false impossibility when one vocabulary jumps
    # over the target by two tokens (observed with Qwen on GTSQA catalogs).
    for vocabulary in (_SAFE_NEGATIVE_WORDS, ("amber",), ("x",)):
        low, high = 0, max_attempts
        while low < high:
            midpoint = (low + high) // 2
            _, observed = candidate_at(midpoint, vocabulary)
            if observed < target:
                low = midpoint + 1
            else:
                high = midpoint
        candidate, observed = candidate_at(low, vocabulary)
        diagnostics.append((low, observed))
        if observed == target:
            return candidate
    raise ValueError(
        "cannot construct an exact token-matched C- response; "
        f"target={target}, lower_bounds={diagnostics}; "
        "revise the discovery response template"
    )


def _contains_forbidden(value: Any, forbidden_terms: Iterable[str]) -> list[str]:
    text = _canonical_text(_json_payload(value)).casefold()
    return sorted({term for term in forbidden_terms if term and str(term).casefold() in text})


def _cache_digest(cache: Mapping[str, Any]) -> str:
    return canonical_digest(cache)


def build_discovery_cache(
    *,
    records: Sequence[Mapping[str, Any]],
    graph_digest: str,
    tool_schema: Mapping[str, Any],
    token_counter: TokenCounter,
    taxonomy_digest: str | None = None,
    tool_name: str = "inspect_graph_schema",
    max_calls: int = 1,
    byte_cap: int = 12_288,
    latency_cap_ms: int = 1_000,
) -> dict[str, Any]:
    """Freeze B0/C-/S1 discovery responses keyed by graph/taxonomy digest.

    Each input record requires ``qid``, ``b0_response`` and ``s1_response``;
    optional ``forbidden_terms`` should include the case answer and any known
    graph labels/relations/entity IDs that C- is forbidden to reveal.
    """
    if not records:
        raise ValueError("at least one discovery record is required")
    if not graph_digest:
        raise ValueError("graph_digest is required")
    if max_calls < 1 or byte_cap < 1 or latency_cap_ms < 1:
        raise ValueError("discovery caps must be positive")
    present_optional = tuple(
        arm for arm in OPTIONAL_DISCOVERY_ARMS if all(_OPTIONAL_RECORD_KEYS[arm] in source for source in records)
    )
    partial = [
        arm for arm in OPTIONAL_DISCOVERY_ARMS
        if arm not in present_optional and any(_OPTIONAL_RECORD_KEYS[arm] in source for source in records)
    ]
    if partial:
        raise ValueError(f"optional discovery arms must be present on every record or none: {', '.join(partial)}")
    contract = {
        "tool_name": tool_name,
        "tool_schema_digest": canonical_digest(tool_schema),
        "max_calls": max_calls,
        "byte_cap": byte_cap,
        "latency_cap_ms": latency_cap_ms,
        "tool_availability": [*DISCOVERY_ARMS, *present_optional],
    }
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in records:
        qid = str(source.get("qid", "")).strip()
        if not qid or qid in seen:
            raise ValueError("each discovery record must have a unique non-empty qid")
        seen.add(qid)
        b0 = _json_payload(source.get("b0_response"))
        s1 = _json_payload(source.get("s1_response"))
        negative = build_negative_control(s1, token_counter=token_counter)
        forbidden = tuple(str(item) for item in source.get("forbidden_terms", ()) if str(item).strip())
        leaks = _contains_forbidden(negative, forbidden)
        if leaks:
            raise ValueError(f"C- response for {qid} contains forbidden terms: {', '.join(leaks)}")
        responses = {"B0": b0, "C-": negative, "S1": s1}
        for arm in present_optional:
            responses[arm] = _json_payload(source.get(_OPTIONAL_RECORD_KEYS[arm]))
        encoded = {arm: _canonical_text(response) for arm, response in responses.items()}
        byte_sizes = {arm: len(text.encode("utf-8")) for arm, text in encoded.items()}
        oversized = [arm for arm, size in byte_sizes.items() if size > byte_cap]
        if oversized:
            raise ValueError(f"discovery response exceeds byte cap for {qid}: {', '.join(oversized)}")
        tokens = {arm: token_counter.count(text) for arm, text in encoded.items()}
        if tokens["C-"] != tokens["S1"]:
            raise AssertionError("C- token count differs from S1")
        cases.append(
            {
                "qid": qid,
                "responses": responses,
                "response_sha256": {arm: hashlib.sha256(text.encode("utf-8")).hexdigest() for arm, text in encoded.items()},
                "bytes": byte_sizes,
                "tokens": tokens,
                "forbidden_terms_sha256": canonical_digest(sorted(term.casefold() for term in forbidden)),
                "structure_sha256": canonical_digest(_structure(s1)),
            }
        )
    cache = {
        "schema_version": "seocho.agentic_os.discovery_cache.v1",
        "graph_digest": graph_digest,
        "taxonomy_digest": taxonomy_digest,
        "tokenizer_provenance": type(token_counter).__name__,
        "contract": contract,
        "cases": cases,
    }
    return {**cache, "cache_digest": _cache_digest(cache)}


def validate_discovery_cache(
    cache: Mapping[str, Any],
    *,
    token_counter: TokenCounter,
    qids: Iterable[str] | None = None,
) -> None:
    """Fail closed when a frozen cache no longer satisfies its matching gate."""
    if cache.get("schema_version") != "seocho.agentic_os.discovery_cache.v1":
        raise ValueError("unsupported discovery-cache schema version")
    contract = cache.get("contract")
    if not isinstance(contract, Mapping):
        raise ValueError("all B0/C-/S1 arms must expose the same discovery tool")
    arms = _expected_arms(contract)
    selected_qids = {str(qid) for qid in qids} if qids is not None else None
    observed_qids: set[str] = set()
    for case in cache.get("cases", ()):  # type: ignore[union-attr]
        if not isinstance(case, Mapping):
            raise ValueError("invalid discovery-cache case")
        qid = str(case.get("qid", ""))
        if selected_qids is not None and qid not in selected_qids:
            continue
        observed_qids.add(qid)
        responses = case.get("responses")
        declared_tokens = case.get("tokens")
        if not isinstance(responses, Mapping) or not isinstance(declared_tokens, Mapping):
            raise ValueError("discovery-cache case lacks responses or tokens")
        if set(responses) != set(arms):
            raise ValueError("discovery-cache case does not contain all arms")
        if _structure(responses["C-"]) != _structure(responses["S1"]):
            raise ValueError("C- structure differs from S1")
        observed = {arm: token_counter.count(_canonical_text(_json_payload(responses[arm]))) for arm in arms}
        if observed != {arm: int(declared_tokens[arm]) for arm in arms}:
            raise ValueError(f"tokenizer drift or corrupted discovery cache for {case.get('qid')}")
        if observed["C-"] != observed["S1"]:
            raise ValueError("C- and S1 token counts differ")
    if selected_qids is not None and observed_qids != selected_qids:
        missing = sorted(selected_qids - observed_qids)
        raise ValueError(f"discovery cache lacks selected qids: {missing}")
    expected = str(cache.get("cache_digest", ""))
    without_digest = {key: value for key, value in cache.items() if key != "cache_digest"}
    if expected != _cache_digest(without_digest):
        raise ValueError("discovery-cache digest mismatch")


@dataclass(frozen=True)
class CohortCase:
    qid: str
    stratum: str


def build_cohort_manifest(
    *,
    dataset: str,
    cases: Sequence[CohortCase],
    exclusions: Mapping[str, str] | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Freeze included cases, strata, and explicit exclusions before running."""
    if not dataset or not cases:
        raise ValueError("dataset and at least one cohort case are required")
    qids = [case.qid for case in cases]
    if any(not qid for qid in qids) or len(set(qids)) != len(qids):
        raise ValueError("cohort qids must be unique and non-empty")
    manifest = {
        "schema_version": "seocho.agentic_os.cohort.v1",
        "dataset": dataset,
        "seed": seed,
        "cases": [{"qid": case.qid, "stratum": case.stratum} for case in sorted(cases, key=lambda item: item.qid)],
        "exclusions": dict(sorted((exclusions or {}).items())),
    }
    return {**manifest, "cohort_digest": canonical_digest(manifest)}


def build_paired_schedule(
    *,
    cohort: Mapping[str, Any],
    arms: Sequence[str],
    seeds: Sequence[int],
    repetitions: int = 3,
    cache_phases: Sequence[str] = ("cold", "warm"),
) -> list[dict[str, Any]]:
    """Create a deterministic, randomized paired execution skeleton.

    Every ``qid × seed × cache_phase × repetition`` unit contains every arm
    exactly once.  The arm order is independently shuffled per paired unit,
    while units are round-robin interleaved by stratum to avoid a contiguous
    block from one graph/topic dominating a service metrics window.
    """
    if repetitions < 1 or not arms or not seeds:
        raise ValueError("arms, seeds, and positive repetitions are required")
    if len(set(arms)) != len(arms) or len(set(seeds)) != len(seeds):
        raise ValueError("arms and seeds must be unique")
    if any(phase not in {"cold", "warm", "none"} for phase in cache_phases):
        raise ValueError("invalid cache phase")
    cases = cohort.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("invalid cohort manifest")
    cohort_digest = str(cohort.get("cohort_digest", ""))
    if not cohort_digest:
        raise ValueError("cohort manifest must include a digest")
    units: list[tuple[str, str, int, str, int]] = []
    for case in cases:
        qid, stratum = str(case["qid"]), str(case.get("stratum", "unstratified"))
        for seed in seeds:
            for phase in cache_phases:
                for repetition in range(repetitions):
                    units.append((stratum, qid, seed, phase, repetition))
    units.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]))
    rows: list[dict[str, Any]] = []
    for unit_index, (stratum, qid, seed, phase, repetition) in enumerate(units):
        ordered_arms = list(arms)
        unit_seed = int(hashlib.sha256(f"{cohort_digest}|{qid}|{seed}|{phase}|{repetition}".encode()).hexdigest()[:16], 16)
        random.Random(unit_seed).shuffle(ordered_arms)
        for paired_ordinal, arm in enumerate(ordered_arms):
            rows.append(
                {
                    "qid": qid,
                    "stratum": stratum,
                    "arm": arm,
                    "seed": seed,
                    "cache_phase": phase,
                    "repetition": repetition,
                    "paired_unit": f"{qid}:{seed}:{phase}:{repetition}",
                    "paired_ordinal": paired_ordinal,
                    "unit_ordinal": unit_index,
                    "cohort_digest": cohort_digest,
                }
            )
    return rows


def write_frozen_json(path: str | Path, payload: Mapping[str, Any]) -> str:
    """Write a frozen contract using the common immutable-manifest primitive."""
    return write_manifest(path, payload)


__all__ = [
    "CohortCase",
    "DISCOVERY_ARMS",
    "OPTIONAL_DISCOVERY_ARMS",
    "TokenCounter",
    "VLLMTokenizer",
    "WhitespaceTokenCounter",
    "build_cohort_manifest",
    "build_discovery_cache",
    "build_negative_control",
    "build_paired_schedule",
    "validate_discovery_cache",
    "write_frozen_json",
]
