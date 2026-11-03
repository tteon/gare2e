> Historical reviewed export. Private paths/endpoints and original attachment links were sanitized. This is not a new measurement. Original: `results/neo4rs_memory_e2e_20261102_a/FINDINGS.md`; SHA256 `42a4356e60bbd3788c8efb6945365329470cca194160594b1d84f10d9849b082`.

# Fixed-BF16 memory E2E: DB improvement did not become a universal E2E gain

2026-11-02. Beads gug-7sf. **24/24 cells,864/864correct,0failed/dropped; warmup156/156correct.** Actual1020 independent contexts and2040 model calls; all2040 calls joined to engine enqueue/scheduling. All432 measured baseline/candidate input pairs had identical evidence bytes/hash, prompt/completion token counts and engine scheduled-token counts. No generation truncation:1020tool_calls finishes and1020stop finishes. Validation [see original archived attachment], full comparison [see original archived attachment], mechanism audit [see original archived attachment].

Same Qwen/Qwen3.8-27B pinned revision,18weight LFS hashes, BF16/seq8/batch8192/context8192/KV12GiB/OMP4; same eight tool descriptions/schemas, fixed Cypher, actual model-selected args and release neo4rs. Only heap/page cache operational profile changes: baseline512/256MiB, candidate1024/1024MiB. Pool/admission16, workers32, fetch1000, Rust thread1, DB15s client/20s server, retry0. No additional index, rewrite, model or scheduler policy optimization. Instrumentation uses the existing observational wrapper in both arms.

## E2E outcome

Time is finite-cohort batch completion, median of3blocks; positive percentage means candidate took longer. No steady-capacity interpretation.

| Workload | Users C | Baseline seconds | Candidate seconds | Candidate change |
|---|---:|---:|---:|---:|
| Frozen32 /8tools |8|48.671|48.678|+0.02%|
| Frozen32 /8tools |32|46.578|46.038|-1.16%|
| Hard40 /5tools |8|131.709|130.668|-0.79%|
| Hard40 /5tools |32|132.933|137.297|**+3.28%**|

Hard40/C32 candidate was slower in every paired repetition: +4.54%,+4.04%,+0.40% (median paired change+4.04%; distinct from ratio of condition medians+3.28%). Baseline batch range131.345-135.980s; candidate136.525-138.292s. Three shared-host blocks are descriptive repeated evidence, not a confidence interval or general optimum.

Hard40/C32 overall request p95 nevertheless fell94.362->92.491s (~1.98%). Thus p95 and cohort drain can move differently. No tool exceeded the prespecified10% regression threshold for the median paired per-tool p95; detailed small-sample ratios remain in mechanism-audit.json. Do not replace tail/fairness measures with one throughput number.

## DB time really fell, but the actual regime was different from DB-only pressure

Hard40/C8 fan-in tool HTTP median across block medians:3.527->1.394s (~2.53x shorter); C32:3.377->1.384s (~2.44x shorter). These spans include HTTP/client/adapter and are not pure server execution. Overall mixed DB medians are much smaller because other tools/results are light; Hard40/C32 overall DB p50~28.25->14.08ms.

Observed peak overlapping tool HTTP spans were only3-4 across all cells despite userC32. Model slots8 smooth tool arrivals. Queue metrics peaked at24waiting requests for C32 and0for C8. Canary2163/2163correct, monitor errors0. This is not a reproduction of32 simultaneous L4 database queries; there is no basis to claim the E2E DB was saturated or that CPU scheduler computation was the measured bottleneck.

## Critical tail explains the regression more precisely

In all6 Hard40/C32 cells the same last request was e2e-hard-2-positive-09 (time-ordered three-hop paths), with identical evidence and1728final output tokens. Its final-engine queue baseline->candidate was42.847->50.253s,43.960->49.845s,48.164->48.710s. Final decode was~63.5-64.3s across all6. In block1, tool result became ready23.904->23.572s from cohort start, while engine admission moved68.055->73.474s; completion132.927->138.285s. Earlier evidence did not guarantee earlier model execution.

Final enqueue order changed in26/40,26/40,13/40positions by paired block. The tail still had31final calls and11968output tokens enqueued ahead in every arm. Counts alone do not describe their order, overlap or remaining work. The measured facts are the changed order, longer tail queue, equal evidence/tokens and near-equal tail decode. Return timing altering admission/batching is a supported explanation to test, not a separately isolated causal scheduler intervention. Median final queue across all requests actually improved in Hard40/C32 (~15.03->13.02s), which is compatible with a worse critical tail.

Overview figure [see original archived attachment] and all3 critical-tail timelines [see original archived attachment]; PDFs beside each image. The timelines show the same request in every repetition, not a selected worst case.

## Decision

Keep heap/cache candidate as a validated DB tuning profile, but **do not promote it to a universal E2E optimization**. Current baseline service was restored for comparison. A vLLM collaborator should receive both memory arms, identical evidence/tokens, actual tool-return and final-enqueue traces, and objective-specific outcomes (batch drain, p95, fairness). Final reentry/length-aware admission or batching are follow-up hypotheses, not tested solutions in this study. Existing lifecycle fault/steady gates remain open; gug-w06 remains the scheduling follow-up rather than silently starting a new policy experiment.

## Controls, provenance and limits

Frozen32 is the original32known cases; Hard40 is40previously exposed hard cases across5tools,4positive/4negative each, capped20rows with independent oracle recomputation. Both arms have max2048output tokens/180s episode ceiling; tokenizer preflight max input4554 and expected answer1741. Historical800-token studies are not matched controls. Semantic counterexample tests were preserved from preceding DB work; this controlled one-tool/two-model-pass evaluation is not free-form multi-turn correctness or unseen generalization.

Preflight corrections occurred before any timed cell: offline template args must be mapping, token count must use BatchEncoding input_ids, and synthetic replay test time origin must include executor handoff. Earlier preparer/budget/source/test failures are retained in preparation-history and logs. No timed cells were edited or resumed with changed code.

Archive artifacts.zip SHA256 ac34046883c2aba87292e91778c03fcf48bb5f6e650ab1b446a42da071a76197;2930file hashes independently verified after safe extraction. Captured source/config hashes, actual metadata/instance IDs, raw requests/streams, scheduler events, resource/metrics and GC logs are preserved. Within this new run checkpoint bytes are pinned/verified; original historical weight byte hashes were not recorded, so identity to the oldest weights relies on the prior pinned source lineage.

Two-second GPU ownership guard saw no foreign job; CPU/DRAM/cgroup remain shared and affinity is not exclusive reservation. No physical-disk/NUMA/GC-algorithm or CPU-scheduler causality claim. Three finite closed-loop blocks cannot establish sustained capacity or a global optimal setting.

Owned model port18000closed, GPUempty, checkpoint holder stopped after consumers, stale owned RAM links removed,77experiment ownership records inactive. Baseline2owners intentionally remain alive at <instance-path> original H512/P256/workers32. Temporary fixtures/indexes0, remaining business transactions0, original install-smoke4083nodes/27429edges/full properties identical and frozen20files match. Clone label/type counts unchanged; no fresh complete SF1-property checksum was computed. No foreign process/checkpoint deletion or Git/Dolt remote sync.
