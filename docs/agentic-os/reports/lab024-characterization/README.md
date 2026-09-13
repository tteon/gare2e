# Lab 024 — linked characterization results

Open [the interactive report](index.html): select one pass and one task to connect
Agent Execution, Runtime Performance and Tool Call views. All chart/table data
are embedded; the compressed CSVs contain the full content-free task/request/tool
records. [Compact comparison table](../../../../traces/lab-s22b/session-summary/characterization.csv).

## Observed outcome

| Application K | Baseline output tok/s/GPU | MTP output tok/s/GPU | MTP change |
|---|---:|---:|---:|
| 8 | 130.30 | 163.31 | +25.3% |
| 16 | 225.64 | 223.52 | −0.9% |
| 32 | 331.47 | 268.29 | −19.1% |
| 64 | 441.45 | 289.20 | −34.5% |

Baseline throughput still rises at K64: a saturation point is **not established**.
These are one ordered pass per condition, with approximately 89 seconds of
validated common counter/completion observations. They are exploratory results;
there are no independent repetitions or confidence intervals. Autonomous outputs
and completed-task composition change across arms, so this is not a pure
constant-output kernel speedup comparison.

At K64, fixed-payload replay gives 440.27 / 440.75 / 433.92 output tok/s/GPU for
serving-only / immediate tool-stub / real DB. The small observed gap is consistent
with sustained request supply in this window; it does not prove DB latency never
matters. This replay fixes subsequent request payloads and generated lengths;
it is not autonomous answer-quality evidence.

At K64, grammar constrained fractions 0/.5/1 give 462.96 / 135.19 / 66.22
output tok/s/GPU under 16 logical CPU affinity, versus 463.37 / 136.97 / 65.57
under 4 CPUs. A large grammar-associated throughput difference remains; these
measurements do **not** isolate grammar step CPU time or demonstrate CPU scaling.
Actual selected ratios and token distributions are in the report. The comparison
uses one grammar protocol throughout; it is distinct from tool-calling baseline.
The additional grammar K8 comparison was not deployed.

Three passes (block32, block128, chunk8192) lost GPU sampling before the window.
Their engine counters/task rates survive, but the primary GPU-normalized rate is
unknown; do not silently substitute allocated GPU count for observed telemetry.
Chunk2048 has 431.54 output tok/s/GPU. This does not establish a block/chunk optimum.

## Three characterization views

- **Agent Execution:** 7,834 task attempts across the 22 comparison passes, with
  LLM/tool call counts, per-task input/output tokens, maximum actual input length,
  output-request kinds, repair counts, termination and answer status. Token means
  use tasks fully contained in the common window; throughput counts completions
  in the window, including boundary tasks. In the slowest grammar arms only 5–6
  tasks are fully contained: their means and termination proportions are strongly
  susceptible to completion bias. `answered` is not semantic accuracy; no answer
  judge ran. Reasoning/argument and context component token counts are unknown.
- **Runtime Performance:** 33,633 client requests, 33,616 unique exact server
  matches; all 7,156 requests in the contained-task cohorts match. Queue, TTFT,
  prefill and decode are separate request observations. TTFT overlaps queue and
  prefill. Request durations overlap across tasks and cannot be summed as GPU
  occupancy. The same-task timeline overlays sampled running/waiting/KV state;
  running requests are not scheduled decode sequences. The remaining 17 unmatched
  requests are outside the contained cohorts and keep null server timings.
- **Tool Call:** 26,014 tool events, per-call elapsed/DB sub-stage timing, returned
  rows/bytes, error versus valid empty result, and linked preceding LLM request.
  Existing serial offsets recover parent IDs with explicit provenance. Lexical
  Cypher shapes are recovered only from exact raw/validated query digest matches
  to the 90 private captures. Coverage varies and is shown per pass; grammar
  shapes are unclassified. Result bytes are not token counts. The historical
  `pool_acquire_ms` is session creation, not actual pool wait. PROFILE was not
  injected into these throughput passes.

The three categories follow [Agentic AI Workload Characteristics, §4–6](https://arxiv.org/pdf/2605.26297).
The paper's reported resident-context measurements are not evidence for this
experiment's high-concurrency or offloading behavior.

## Runtime scope and missing measurements

This run used co-located application/DozerDB/vLLM services on one rented machine;
process/service telemetry and request stages are distinguished, but this is not
a separate-server or independent CPU/DRAM allocation experiment. DB heap8g and
pagecache24g stayed fixed. Host file-cache/RSS are not DB page-cache occupancy.

The measured maximum KV usage across common windows is 44.3% (MTP K64), with zero
preemptions in every observed window. The maximum actual input in contained
cohorts is 4,743 tokens, despite max_model_len32768. No long-context KV-capacity,
eviction/offloading, or DB/KV DRAM allocation intervention ran. These data do not
refute offloading benefits under longer contexts, reuse and pressure.

Engine step decode-sequence counts, grammar CPU sections, GPU kernel gaps,
actual DB page-cache occupancy/GC and KV transfer latency/bytes remain unobserved.
Latest explicit task clocks, parent tool IDs and context byte composition were
implemented after deployment and **not applied to this live run**. Historical
case clocks are recovered from the process-window timestamp where available;
coordinator fallback has startup uncertainty. Original receipts are preserved.

## Conditions and provenance

- Dataset: existing MAPLE Physics 90-question cohort, balanced repeated backlog;
  workspace `grbench-maple-physics`, graph digest
  `bc267591dc3d7aaf8c46262e1effa24b2cc2362b66f967d9f686ca70413efa5e`.
- A100 SXM4 80GB ×2, NVLink300, TP2, 32 logical CPUs, host RAM257943 MB.
- Qwen/Qwen3.6-27B revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`;
  bf16, vLLM0.27.1 image ID
  `sha256:0a51ea5b4ae2dc5d81890e5173f54203d2a3ae0cfffe51b8fd2afd4391bfd967`.
- Neo4j5.26.3 / Dozer appliance, runtime bundle SHA256
  `b15a6e6c9dacac1a35c804220e2e16e0e1dc3805d006c3ab4d69f39365916751`.
- Single agent, S1, non-reasoning; max6 turns, output2048/request,
  budget16384/task; prefix cache enabled, max_num_seqs128,
  max_num_batched_tokens8192 except named chunk arms, max_model_len32768.
- Duration-bounded dynamic FIFO, persistent workers except fresh-k8, warmup after
  every worker first completion +10 s; 90 s dispatch window then drain.
- Paid source commit `bdfb4877`, source-v53 SHA256
  `09c3a7a7c126d1724c45c060894d6f9aec8358d72e800080b0e8fb10c718b9cf`.
- Recovered archive SHA256
  `5b1ad6d6b5fc35f851d3184b78588c2e22ee5d5e1f07c79573ff05339b229c07`;
  all51,590 unique regular files locally reconciled, zero missing/mismatch.
  The independent remote digest checkpoint was not persisted, so lifecycle is
  `artifacts_partial_reconciled`, not a fully verified completed collection.
- Session24 passes exited0 (22 comparisons + gate/capture); budget reserve stopped
  before TP2-control and replicas2. Independent watchdog destroyed owned instance
 50866184 and verified absence; estimated hourly cost $12.1227, not a final invoice.
  No owned instance remains running; colleague resources were untouched.

## Reproduce the derived report

From a checkout with the reporting code, using the preserved local run directory:

```sh
PYTHONPATH=src uv run python scripts/benchmarks/summarize_agentic_os_characterization.py \
  --run-root "$RUN_ROOT" --output-dir "$DERIVED_ROOT" --use-private-captures
```

Private capture contents are read locally only to derive lexical labels; they are
not copied into this report. Omit `--use-private-captures` to keep unrecoverable
query shapes unknown. Original receipt and collector files are never overwritten.
The [derived manifest](../../../../traces/lab-s22b/session-summary/manifest.json)
records file hashes. Local rendering was verified in headless Chrome and the
report JavaScript passed `node --check`; mock tests cover contracts only.
