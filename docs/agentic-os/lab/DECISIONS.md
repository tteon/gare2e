# Decisions

| date | decision | source |
|---|---|---|
| 2026-09-03 | GRBench workspace id is `grbench-maple-physics` end-to-end; v6 GRBench rows are plumbing evidence only | v6 receipt audit |
| 2026-09-03 | GRBench graph ships as exporter CSV and is bulk-imported offline on the appliance | V13 post-mortem |
| 2026-09-03 | Paid runners launch as `systemd-run --user` units, never as session children | V13 post-mortem |
| 2026-09-03 | Iteration moves to a persistent instance after the e2e is shown to run end-to-end (achieved in V17) | chat |
| 2026-09-03 | Collaboration runs through `.agents/lab/` (trigger C: file watch when live, `/lab` otherwise) | chat |
| 2026-09-05 | One-case gate B0 criterion = `attempted` (option A); validation-failure rate reported as baseline metric | lab 001, chat |
| 2026-09-05 | Persistent lab instance approved: $12 / 6 h per session; PE-vs-CE matrix (lab 006) approved; first pass PE-0 + PE-1 | chat |
| 2026-09-08 | e2e lane shared via private repo `tteon/gare2e` (code + skills + docs only; no WIP snapshot, no lab board, no receipts). Public `tteon/seocho` not touched. | chat |
| 2026-09-08 | Difficulty rubric: structural score (hops, anchors, patterns, aggregation, filter, argmax) → easy/medium/hard, identical for Cypher and SPARQL, never from observed behaviour; stratified cohort n=10 per stratum per lane (strata-v1) | lab 007, chat |
- 2026-09-12 — Paid Vast instances must have ≥ 2 GPUs joined by NVLink (bw_nvlink > 0). No PCIe-only pairs (RTX PRO 6000, A100 PCIe). Enforced by `--require-nvlink` in the lifecycle runner and the launcher offer query. (user, chat)
- 2026-09-12 — Host 458442 (A100 SXM4 NVLink pairs, JP) is admissible again for lab runs; the V14 NCCL hang predates the topology-gated NCCL_P2P_DISABLE (nccl_env.sh). (user, chat)
- 2026-09-12 — Experiments focus on the single-agent lanes (grbench-single, gtsqa-single) from now on; the multi-agent lanes (manager + specialist) are dropped from new runs — too hard to control, and lab-s11/s13 showed 0 evidence in every cell regardless of schema or serving. Multi-agent evidence stays on the record for the talk's "adaptive depth, not more agents" point. (user, chat)
- 2026-09-12 — Naming: the measured lanes are "iterative Text2Cypher / Text2SPARQL loops in the GraphCoT style", not the original Graph-CoT hop actions. Reports carry this note; a hop-tool lane (RetrieveNode / NodeFeature / NeighbourCheck / NodeDegree over DozerDB) is built as lab 017 for a direct comparison. (user, chat)
- 2026-09-12 — When no A100 NVLink pair is admissible, H100/H200 NVLink pairs may be used (higher $/h accepted); NVLink-only rule unchanged. GPU-RAM floor for "2× 80 GB" is 160 000 MB. (user, chat)
