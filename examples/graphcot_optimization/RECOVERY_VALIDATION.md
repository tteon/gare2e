# Recovery runbook validation — 2026-11-04

This extends the preservation snapshot; it does not create new performance evidence.
The [runbook](FRESH_INSTANCE_RUNBOOK.md) includes executable recovery helpers rather
than advising operators to run historical, host-specific controllers unchanged.

| Gate | Evidence / scope |
|---|---|
| Encrypted archive restoration | Previously GitHub-readback/decrypted/object+path hashes verified, full materialization and restored original pilot validator passed; [receipt](preservation/VERIFICATION.json) |
| Recovery unit/example tests | 13 passed: streaming exact64bit IDs, UTC month boundary, scope/hash rejection, model file/path verification, stage copy-before-patch and no overwrite, existing example/preservation contracts |
| Actual source staging | Canonical SF1 hash checked; staged the original archived source layout; frozen32=32/hard40=40 cases, authenticated evaluator patch and contract export succeeded. No source modified |
| Live Neo4j 5.26.0 | Fresh isolated native instance on the existing authorized server, authenticated; 3 Account nodes /4 transfers including parallel transfers and two UTC months; sidecar gives6 nodes/11 relationships |
| Full fixture audit | Every property/label/endpoint/multiplicity and baseline schema matched. Nonempty import refused; intentional property corruption detected; owned PID/start-time checked before stop; test port closed |
| Query equivalence | Package `sf1_optimized` Cypher file hashes equal restored frozen handoff queries |
| Model pin metadata | Public pinned repository revision reachable, file SHA manifest retained; weights not downloaded or inferred in this task |
| Docker/native distribution pins | Official Neo4j archive checksum and linux/amd64 image manifest checked; Docker container path itself not run locally |

The sanitized [live fixture receipt](evidence/recovery-live-fixture.json) identifies
the recovery helper SHA and exact observed schema. This tiny fixture is not SF1,
not a throughput/latency measurement, and not evidence of production readiness.

## Remaining gates

- Full SF1 import/audit on the colleague's **new instance** remains to be run.
- Local Rust toolchain and Docker daemon were unavailable. Adapter build/tests
  and Docker startup commands require verification on the destination.
- No model serving/E2E run was performed. The staged standalone evaluator is
  portable for explicit endpoints/auth, but does not include the old controller's
  ownership/2-second foreign-GPU guard, reset policy or lifecycle orchestration.
  These must be adapted/tested before shared-GPU matrix execution.
- Reimport changes physical page/store layout and cache/history. A fresh B0 and
  counterbalanced repetitions are required before claiming a speedup.
- The prior repository basic CI result was1091passed/5skipped/2failed, with two
  unchanged env-contract failures (four declarations missing from `.env.example`).
  This recovery slice runs focused tests, Ruff, Python compilation and doc contracts;
  it does not claim the full repository CI is green.

Raw fixture console/ownership files remain outside Git. No credential, private
host address, restored raw prompt/answer, live DB store or recovery key is uploaded.
