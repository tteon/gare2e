---
id: 027
title: Graph query evidence and exact-result reuse in the existing platform
state: diagnostic pilot complete; broad optimization modes staged
created: 2026-09-25
bead: seocho-ix30
---

## Authorization and continuity

The user approved extending gare2e and adding clear logs, traces and insights,
with a new USD100 total ceiling. This is separate from lab026's budget. Reuse
the existing agent runners, DozerDB executor, OTel trace identity and immutable
experiment platform. No storage-engine change, second benchmark framework or
automatic SaaS upload. Architecture:
[graph evidence contract](../../GRAPH_OPTIMIZATION_OBSERVABILITY.md).

Closest evidence: lab021's 317 PROFILE replays / 2,309 operators and lab017's
fixed-template GraphCoT lane. Original lab021 counters are complete; zero misses
remain an actual observation. Its receipts lack parent topology. The parser's
missing-to-zero bug affects incomplete payloads and is corrected separately.
The platform already contained 30 conditions / 10,821 tasks before this work.

## Incremental hypothesis and exact change

Can bounded, session-local reuse avoid repeated database submissions while
returning exactly the same complete result on a frozen snapshot? The only
intervention is `GARE2E_QUERY_MODE=exact_result_reuse`, with 64 entries / 1 MB
maximum. RAW gets identical observation. Agent tool invocations still occur.
Do not call this a tool-call reduction or general multi-turn planner.

First run six fixed-lookup cells: observation OFF / RAW / reuse / reuse / RAW /
OFF, 20 lookups each, result hashes equal. Separate PROFILE replays follow each
observed cell. Then one-case service/trace/tool gates followed by RAW / reuse /
reuse / RAW for Text2Cypher and GraphCoT, three questions each, K=1. These small
cells diagnose actual reuse opportunities; they do not establish p99, quality
noninferiority or GPU throughput. Warmup/cache state is uncontrolled warm.

Pinned live stack: vLLM0.27.1, Qwen3.6-27B revision
`6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, TP2, maxseq32, maxlen32768,
DozerDB heap8g/pagecache24g, GRBench Physics snapshot from hashed bulk inputs.
Agent limits: 8 turns, 512 output tokens/request, 16384 token budget, thinking
off, seed0. Profile replay is outside timed lookups. Final output is unscored
unless a versioned evaluator is attached.

## Gates and cost policy

Use the existing Vast lifecycle and independent watchdog, readiness for every
named service, checksum-verified staging and collection, and verified absence
after destruction. Pilot1 rejected a stale offer before creation ($0). Pilot2
has a7200s lifetime at at most$3.60/h, so hourly compute/storage estimate is
bounded by$7.20. Provider data-transfer charges are additional and must be
reported separately from the hourly estimate; ample room remains within$100.
Do not launch another instance while ownership is unresolved.

## Acceptance and limitations

- Every opted-in attempt has query/session/trace identity, outcome and timing;
  validation failures, auxiliary queries, PROFILE and cache hits stay distinct.
- Missing counters remain unknown. Replays never substitute for original-query
  cache behavior. Reuse is disabled for failures, volatile or capped results.
- Fixed-query result parity is required; agent correctness is a separate gate.
- Logs/plans/quality/insights are inspectable in the existing platform. Real
  local Collector, Tempo, Prometheus and Grafana integration must pass.
- General QUERY_OPT, ONTOLOGY_OPT, batching and budgeted prefetch remain proposed
  interventions until semantic preconditions and measurement gates exist.

## Result

Implementation, actual local integration and the live pilot passed. Essential
artifacts were checksum verified and instance absence confirmed. Hourly cost
estimate $0.991837; network charges/invoice unverified. Agent comparison sessions
had zero cache hits; fixed lookup reuse preserved results and avoided19/20 DB
submissions. See [result and limitations](../../reports/lab027-graph-evidence/README.md).
Deployed source archive SHA256:
`26b5d525f2d6996651d306693a49581d50f5d4af48b9e9d217de8d2ec7965e86`.
Post-deployment viewer/adapter validation fixes are not attributed to that bundle.
