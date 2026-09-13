"""Router policy for the GRBench agent lanes (lab 019).

Input: a content-free :class:`~seocho.eval.request_analysis.RequestAnalysis`
(family / intent shape, resolved anchors, degree classes).  Output: a
:class:`RouterPlan` naming the lane, context arm and budgets the dispatcher
should run.  The policy is a table, not a model, so every decision is
auditable from the receipt; ``reasons`` lists the rules that fired.

Policy V0 (request 019, revised by labs 017 / 018 / 021):

* count / set-intersection families -> whole-query Text2Cypher with the
  SEOCHO vocabulary slice (S1) and the zero-row diagnostic hint (lab 021);
  counts get a short turn budget (one query is expected);
* one- and two-hop lookups (authors_of_paper, venue_of_paper,
  coauthor_on_paper) with a resolved anchor -> Graph-CoT hop actions (B0),
  which answered those families without repairs in lab 017; hops get a
  larger token budget because the hop lane's budget is cumulative;
* argmax / recommendation families -> Text2Cypher with relation semantics
  (O1: the only context that helped collaborators_in_year), higher row cap;
* unresolvable anchors (mentions found, none resolved) -> bounded
  Text2Cypher attempt (V0) or an immediate abstention with no LLM call (V0A);
* hub anchors -> note only: the executor already caps rows; the 5 s Bolt
  timeout is not yet a per-request knob.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .request_analysis import RequestAnalysis

POLICIES = ("V0", "V0A")
HOP_FAMILIES = ("authors_of_paper", "venue_of_paper", "coauthor_on_paper")
ARGMAX_INTENTS = ("argmax", "recommendation")
DEFAULT_MAX_TURNS = 6
DEFAULT_TOKEN_BUDGET = 16_384
HOP_MAX_TURNS = 8
HOP_TOKEN_BUDGET = 32_768
COUNT_MAX_TURNS = 4
BOUNDED_MAX_TURNS = 3
ARGMAX_ROW_CAP = 50


@dataclass
class RouterPlan:
    policy: str
    lane: str  # grbench-single | grbench-graphcot | abstain
    arm: str | None
    max_turns: int | None
    token_budget: int | None
    row_cap: int | None
    zero_row_hint: bool
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def receipt(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = "seocho.router_plan.v1"
        return data


def plan_request(analysis: RequestAnalysis, *, policy: str = "V0") -> RouterPlan:
    if policy not in POLICIES:
        raise ValueError(f"unknown router policy: {policy}")
    plan = RouterPlan(policy=policy, lane="grbench-single", arm="S1", max_turns=DEFAULT_MAX_TURNS, token_budget=DEFAULT_TOKEN_BUDGET, row_cap=None, zero_row_hint=True)
    # "unresolved" only when a resolution was actually attempted (resolvability recorded); a lexical-only analysis never abstains
    unresolved = analysis.mentions > 0 and analysis.anchors_resolved == 0 and bool(analysis.resolvability)
    intent = analysis.intent

    if unresolved:
        if policy == "V0A":
            plan.lane, plan.arm, plan.max_turns, plan.token_budget, plan.zero_row_hint = "abstain", None, 0, 0, False
            plan.reasons.append("anchors_unresolved->abstain")
            return plan
        plan.max_turns = BOUNDED_MAX_TURNS
        plan.reasons.append("anchors_unresolved->bounded_text2cypher_s1")
    elif intent == "count":
        plan.max_turns = COUNT_MAX_TURNS
        plan.reasons.append("intent_count->text2cypher_s1_short")
    elif intent == "lookup" and analysis.family in HOP_FAMILIES and analysis.anchors_resolved > 0:
        plan.lane, plan.arm = "grbench-graphcot", "B0"
        plan.max_turns, plan.token_budget, plan.zero_row_hint = HOP_MAX_TURNS, HOP_TOKEN_BUDGET, False
        plan.reasons.append("intent_lookup_resolved->graphcot_hops_b0")
    elif intent in ARGMAX_INTENTS:
        plan.arm, plan.row_cap = "O1", ARGMAX_ROW_CAP
        plan.reasons.append("intent_argmax->text2cypher_o1_rowcap50")
    elif intent == "set_intersection":
        plan.reasons.append("intent_set_intersection->text2cypher_s1")
    elif intent == "lookup":
        plan.reasons.append("intent_lookup_multihop_or_unanchored->text2cypher_s1")
    else:
        plan.reasons.append("family_unmatched->text2cypher_s1_default")

    if analysis.degree_class_max == "hub":
        plan.notes.append("hub_anchor: row cap already bounded; per-request Bolt timeout not yet a runner knob")
    return plan


__all__ = ["ARGMAX_INTENTS", "HOP_FAMILIES", "POLICIES", "RouterPlan", "plan_request"]
