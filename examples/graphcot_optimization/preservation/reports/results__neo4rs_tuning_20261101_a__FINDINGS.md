> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/neo4rs_tuning_20261101_a/FINDINGS.md`; SHA256 `386501e9e1d67af2fe378d34757791aca445ee06ac1ff26f832adc5c444ba085`.

# First matrix: SF1 neo4rs optimization (2026-11-02)

182/182 measured cells completed; raw10128 =9139correct completions +989failures +0drops;0incorrect successful responses. Warmups490/490correct. Errors:416transaction-memory,558deadline,15Bolt NoThreadsAvailable. Raw payloads were matched again against input arguments and expected rows; captured Python source hashes verified. See run/analysis.json, audit.json, REPORT.md, optimization-matrix.png and immutable evidence-main.zip (SHA2560089c8522e3b5280090a02fc3c397e18668c6f8832e80db2a4e002d9d581e1ac).

## Findings within this workload

C32 mixed80 and L4positive32 finite cohorts, three blocks each. Single baseline mixed240/240 and fanin61/96. Increasing heap512→1024MiB achieved mixed240/240 and fanin96/96 (median9.504s/9.146s). More workers64, Rustthreads4 or fetch100 did not eliminate fan-in memory failures. Pool8 got93/96fanin, with deadlines; its mixed234/240 also shows that throttling is not free within15s request budgets. L4cap4 alone60/96fanin and222/240mixed failed deadlines.

Pagecache256→1024MiB mixed240/240, median3.900s versus baseline9.267s, but fanin52/96: cache alone does not provide robust concentrated-load success. Do not compare the shorter failed fan-in time as an equal-success speedup. This motivates measuring heap+cache together.

Full factorial L4cap0/4 ×pool8/16 ×L3base/rewrite ×heap512/1024, pagecache256 fixed: only pool16/cap0/heap1024 candidates (base and L3) had0failures across all336 tuning requests. Selected base query based on the prespecified worst normalized median score; the L3 candidate is very close, not a meaningful universal winner. Selected base tuning median mixed9.868s/fanin9.416s; baseline failed56/96fanin, so no equal-success fan-in speedup claimed.

Reused40 validation atC8/C32, three blocks per arm: selected240/240 and baseline240/240. Median batch atC8 8.802s vs8.797s; C32 8.991s vs9.394s. Candidate C32 per-tool p95/baseline ratios: fanin0.957, shared access1.067, rapid path0.825, ordered path0.955, reachability1.064; no medianp95 regression above10% in this reusedvalidation. Only3blocks and repeated known contracts, no unseen or steady-load guarantee.

## Causal limits and pending work

Heap also changes Neo4j's default transaction-memory budget, so this is an operational profile effect, not isolated GC causality. Sampled JVM physical read_bytes deltas sum0; no physical disk bottleneck demonstrated. GC aggregate includes setup and diagnostics (max pause194.666ms), not query-causal attribution.

271resource samples recorded a foreign GPU job PID996731 during wall1793545613–1793546163. Our study is DB-only, but CPU/DRAM/cgroup resources are shared; affinity does not reserve exclusive cores. No foreign process was stopped. Timing differences require this caveat; no vLLM/GPU improvement or full-server exclusivity claim.

Main owned DB/adapter ports closed at cleanup. Baseline persistent service remains intentionally stopped while followup uses the same owned clone. Followup <instance-path> (PID1020082/start335695917) tests heap candidate+cache+index+adaptiveL4; still running. Do not close gug-p96, deploy a universal winner, or mark model E2E complete.
