> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `docs/RUNTIME_MEASUREMENTS.md`; SHA256 `c100e75961291557419b5a19cb0acb75a2fa373df228d11759cef08db46294f1`.

## Measured results

Longer-warm memory controls: 300 warmup queries then 120 measured queries per cell, two reverse-order blocks. All 960 measured queries passed the oracle. Table values are the range of the two per-case medians in milliseconds.

| Heap initial=max / pagecache | L4 | L5 | L6 |
|---|---:|---:|---:|
| 512MiB / 8MiB | 20.6-21.6 | 54.8-56.4 | 62.1-63.3 |
| 512MiB / 64MiB | 21.4-21.5 | 55.9-56.0 | 61.5-63.3 |
| 512MiB / 2GiB | 20.0-22.2 | 53.7-57.4 | 59.3-64.0 |
| 2GiB / 64MiB | 19.6-20.9 | 52.0-52.7 | 58.6-59.8 |

The verified logical clone occupies 11,837,440 bytes (11.3MiB), versus about11.8GiB for the original store. Different physical layout prevents treating this as an original-store memory A/B. No consistent pagecache-size advantage appeared. The second8MiB cell had a45% L4 first-half-to-second-half increase, so8MiB is not selected as optimal. The512MiB heap/64MiB pagecache point was a fixed diagnostic control, not a global optimum.

All measured process read_bytes deltas and major-fault deltas were0 (the process interval includes the diagnostic PROFILE). This is a warm-OS-cache observation, not evidence of zero Neo4j page-cache misses. PROFILE hit and miss counters were both zero and are not treated as usable hit-ratio evidence. GC pauses during each120-query measurement totaled about14-36ms.

Original-store warm medians L1-L6:0.56/0.93/1.00/25.23/67.17/76.33ms. These separate observations cannot isolate layout or memory effects. Original JMX heap: committed512MiB, used approximately140MiB, maximum2GiB.

Client concurrency16; server worker maximum32; heap512MiB/pagecache64MiB. Each cell96 queries, two reverse-order blocks; all576 queries passed. These short DB-only batch rates are not sustainable arrival-rate estimates or agent E2E rates.

| Client pool max | Queries/s | Client p95 ms |
|---|---:|---:|
| 1 | 36.1-40.5 | 2282.5-2571.8 |
| 4 | 114.4-141.7 | 607.6-783.1 |
| 16 | 280.1-403.2 | 82.5-189.0 |

Partial-result transaction count:1; after consume:0. Explicit transaction after consuming its query:1; after rollback:0. Verified with SHOW TRANSACTIONS metadata, not worker-thread pin instrumentation.

| Query | Original median ms, blocks0/1 | Rewrite median ms, blocks0/1 | DB hits original -> rewrite |
|---|---:|---:|---:|
| L5 | 59.14 / 56.53 | 10.86 / 8.87 | 578,315 -> 83,605 |
| L6 | 59.17 / 73.61 | 11.58 / 9.84 | 720,169 -> 105,732 |

Rewrites preserved reference answers; existing semantic-counterexample validation remains separate. DB speedup is not the same percentage speedup in multi-second model E2E. Original graph node/edge content hashes are unchanged; original Neo4j remained online. Owned clone servers are stopped; their data and logs are retained for reproducibility.
