# Packaging validation — 2026-11-04 KST

## Preservation extension

The user clarified instance-retirement preservation as the primary goal.
The [encrypted release snapshot](preservation/README.md) retains35,631 logical
files/22,695 unique objects. Remote candidates26,902/26,902 covered; newly fetched
3,152files all SHA-verified. Nested credential-pattern audit returned zero hits.
Seven uploaded volumes were all downloaded from GitHub again and verified through
decryption/object SHA/path mapping. Full materialization and the restored pilot
validator also passed. Scoped tests now **9passed**, including real local
age/zstd dedup round trip, tamper rejection and unsafe-path rejection. Neither
these tests nor snapshot verification is a new Neo4j/vLLM performance measurement.
Model weights/live physical DB/toolchains/peer campaigns/keys remain excluded.

## Completed local gates

- `python3 examples/graphcot_optimization/lab.py verify` — copied source SHA,
  derived oracle/figure SHA, three query sets and cached864-outcome denominator.
- `python3 -m unittest discover -s tests/seocho -p test_graphcot_optimization_example.py -v`
  — seven tests passed. Scope/64bit/hop/time boundaries, oracle counterexample,
  all query exports and failed-response accounting. Mock HTTP proves contracts only.
- `python3 examples/graphcot_optimization/prepare_neo4rs.py` — official upstream
  archive SHA and original/patched Txn SHA verified, patch applied. Downloaded
  vendor source is ignored and is not part of the published Git tree.
- `render.py --output <fresh-output>` — PNG/PDF regenerated from reviewed numeric
  exports. No DB/model/provider invocation. Figure layout inspected locally.
- Notebook validated with nbformat and all code cells executed with nbclient in
  an isolated Python3.12 environment. Source notebook contains no private outputs.
- Scoped Ruff check of lab/prepare/render/new test passed.
- `bash scripts/ci/check-doc-contracts.sh` passed.
- `bash scripts/ci/check-root-hierarchy-contract.sh` passed.
- Full `bash scripts/ci/run_basic_ci.sh` retry: **1091 passed,5 skipped,2 failed**.
  Both failures are existing `test_env_contract.py` checks: four variables read
  by unchanged `src/seocho/eval/query_evidence.py` are missing from unchanged
  `.env.example` (`GARE2E_GRAPH_SNAPSHOT`, `GARE2E_PRIVATE_REPLAY`,
  `GARE2E_QUERY_EVIDENCE_DIR`, `GARE2E_QUERY_MODE`). No SDK/env fix was folded
  into this tutorial. The first attempt lacked Ruff; declared CI dependencies
  were installed for the retry. Later basic-CI shell gates were not reached
  after the test failure; relevant docs/root/import/runtime/module gates were
  run separately as recorded in the PR.

## Explicit gaps

- No Rust toolchain was available locally; **Cargo build/test were not run** in
  this packaging task. The code/lock/recorded patch are preserved, but downloading
  the crate and passing Python contract tests do not establish Rust build compatibility.
- No live Neo4j, neo4rs HTTP or vLLM test was run for the portable wrapper.
  Historical live measurements are summarized in RESULTS.md and are not substituted
  for new-machine compatibility tests. The mock failure test is not live evidence.
- Raw archives, original private workloads, DB snapshots/stores and model weights
  are intentionally absent. Chart regeneration does not repeat raw semantic audits.
- The portable DB CLI runs a finite cohort, defaults to no warmup and does not
  implement automatic counterbalancing, open-loop arrival or a full LLM runner.
  TUTORIAL/PROTOCOL describe how to register those separate gates.
- No cloud rental, external experiment, peer termination, visibility change or
  database migration was performed.
