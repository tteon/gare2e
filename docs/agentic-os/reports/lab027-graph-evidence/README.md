# Lab027: query evidence and exact-result reuse

The existing gare2e platform now connects task timelines to query attempts,
actual DB submissions, normalized PROFILE plans, nullable counters, evaluations
and evidence-linked insights. The first live pilot validated this path and
found **no exact-result reuse in the 24 agent comparison sessions**. Repeated
fixed lookups did reuse results. These are different conclusions.

## Measured result

| Diagnostic | Observation | Interpretation |
| --- | --- | --- |
| Fixed lookup, 20 invocations per cell | RAW: 20 DB submissions; reuse: 1 submission + 19 hits, in both repetitions | Exact reuse works for a repeated complete result on this snapshot |
| Fixed result parity | All six cells have the same result SHA256 | Fixed-query output parity only |
| Lookup median, including observer overhead | RAW 4.781 / 4.367 ms; reuse 0.446 / 0.443 ms | Local fixed-query diagnostic, not agent E2E improvement |
| Observer OFF medians | 4.574 / 3.649 ms | Cache/order drift prevents a clean overhead estimate |
| Text2Cypher, 4 cells × 3 questions | 13 attempts / 11 submissions per cell; 2 pre-submission policy rejections; 0 reuse | Failed attempts and actual DB failures are distinguishable |
| GraphCoT, 4 cells × 3 questions | 6 attempts / 6 submissions per cell; 0 reuse | This small cohort offers no exact reuse opportunity |
| Answer text parity | Each lane's three later cells match its first RAW cell on all 3 questions | Exact output text equality; correctness remains unscored |
| Fixed diagnostic PROFILE | 4 plans; each has NodeIndexSeek, 2 DB Hits, 4 page hits, 0 misses; counters 4/4 operators | The replay is indexed and warm; this does not describe cache misses in the timed agent queries |
| Request trace linkage | 119 / 122 client requests have one exact server parent/trace match | 3 requests in the last GraphCoT RAW cell have missing server spans; cause unverified |

The observation OFF cell's DB submission count is unknown, not zero. Query
attempts cover the executor boundary; invalid tool names/arguments rejected
before reaching the executor remain tool failures in existing receipts.

Results are in [summary.json](summary.json). It contains 16 cells: six fixed
diagnostics, two one-case agent gates and eight agent comparison cells. There
are 32 tasks and 167 query attempts, including four separate PROFILE replays.
The SPARQL readiness/tool gate is preserved separately and is not a treatment
comparison. All 24 comparison sessions reached `final`; this is not a quality
score. No cold-cache, concurrency, parallel-runtime, throughput or p99 claim is
made. Apparent agent timing variation cannot be credited to a cache that had
zero hits.

## Conditions and provenance

- A100 SXM4 80 GB × 2, NVLink; AMD EPYC 7742 host, 64 effective CPU cores,
  provider-reported 512,983 MB available CPU RAM. No dedicated CPU affinity or
  CPU quota was imposed by this pilot; host contention is not controlled.
- vLLM `0.27.1`, Qwen/Qwen3.6-27B revision
  `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, TP2, maxseq32, maxlen32768.
  Preflight image ID `0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967`.
- DozerDB `graphstack/dozerdb:5.26.3.0` runtime archive, Neo4j binary5.26.3
  verified in exact-archive local preflight; the same archive was checksum
  verified remotely. Heap8g/pagecache24g; live connectivity, EXPLAIN, index
  readiness and import-equivalence gates passed.
- GRBench Physics: 2,762,144 nodes / 67,340,016 relationships, source counts
  matched. Import manifest SHA256
  `8a7ca303e90f677ae4d451c6a260cde8f7aa79c3819c1614a0319418b854aee6`.
- K=1; first three questions of the preserved file, not a representative W1–W8
  cohort. RAW / reuse / reuse / RAW within each lane; 8 turns, 512 output
  tokens/request, 16384 budget, thinking off, seed0. Fixed lookups and one-case
  gates precede the cells; caches are uncontrolled warm.
- Source base8480676c; deployed archive SHA256
  `26b5d525f2d6996651d306693a49581d50f5d4af48b9e9d217de8d2ec7965e86`.
  Post-run importer/UI fixes, canonical tool IDs, manifest configuration and
  statement digest normalization were locally tested, not rerun on the GPU.
  Original query IDs/trace IDs and original files remain unchanged.

## Observability validation and gaps

The optional local stack ran Collector0.119.0, Tempo2.7.2, Prometheus3.1.0 and
Grafana11.5.1. Actual viewer HTTP trace/log delivery, Tempo lookup, Prometheus
scrape and Grafana health passed. Recorded pilot evidence was then imported:
298 agent/client spans +119 server spans, 32 traces, 167 query log records.
This was a post-run import, not a claim of live remote streaming. Example
verified trace: `6d7d2b868840f78683b929bf2087efcd`.

The local registry retains the original30 conditions /10,821 tasks, the lab021
audit's two conditions /120 tasks /317 profiles, and this pilot. Total48
conditions /10,973 tasks /484 query attempts. Historical and new runs remain
separate. Prometheus shows registry inventory, not workload throughput.

Preserved host CSVs contain process CPU/RSS/thread/read/write counters and host
memory/load; receipts retain process attribution and vLLM metric windows.
No per-query PMU, CPU-stall, DRAM bandwidth, NUMA locality, storage-page mapping,
GPU kernel capture or causal DB→GPU benefit was established. Agent query plans
were not profiled in this timed pilot; only the fixed diagnostics have plans.
LangSmith connectivity and a deployed team endpoint remain unvalidated. Quality
requires a versioned evaluator with evidence scores before performance promotion.

## Cost and lifecycle

The user authorized a fresh USD100 total ceiling. Pilot1 rejected a stale offer
before creation: $0. Pilot2 instance52552894 ran1339.352s at the provider's
disk-inclusive$2.665926/h: **$0.991837 hourly estimate**. Network-transfer charges
and final invoice are not included. No additional paid run is scheduled.

Remote workload exit0. The122-file essential archive (200,586 bytes) passed
remote/local SHA256 verification:
`a346526903fddd88ee0629038b037ad805fe1b634312ec85faf6664280a662f2`.
The lifecycle's overall exit2 / `_PartialArtifacts` records the explicitly
skipped bulk transfer; it is not a workload failure. Preserve that limitation.
Instance destruction and absence were verified, and the independent watchdog
stopped with `parent_teardown_verified_absent`.

Private original evidence: `.seocho/benchmarks/graph-evidence-20260925/pilot-2/`.
Reviewed export with original server-span joins: sibling`pilot-2-reviewed/`.
No private query text, parameters, predictions or provider credentials are in
this report. Reproduce the summary with
`scripts/benchmarks/summarize_graph_evidence_pilot.py`; reproduce platform input
with `export_graph_query_evidence.py --server-spans <original collector JSONL>`.

The next useful optimization gate is a preregistered iterative/repair cohort
and evaluator. Ontology rewrites require validated physical schema mappings;
batching/prefetch require bounded fetch costs and evidence-retention checks.
They are not presented as implemented A/B/C/D/E treatments.

## Validation

Final focused regression suite:83 passed. Required basic CI:1112 passed,
3 skipped; repository documentation/import/layout/identity contracts passed.
Browser checked48 conditions and the live LLM-server/query timeline with zero
JavaScript errors. Tests validate contracts; only the actual live receipts
support the diagnostic measurements above.
