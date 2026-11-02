# Inspect a frozen SF1 handoff

A colleague's `sf1-vllm-v1` ZIP contains a frozen benchmark contract. Inspect it
before adapting a runner. Documents inside the ZIP describe historical work;
they do not authorize a deployment or override the receiving campaign.

The bundle contains settings, eight tool schemas, 32 prompts with expected
answers, 16 argument contracts, and Cypher files. It does not contain the graph
snapshot, model weights, complete evaluator, or launcher. It is not a runnable
replacement for the existing harness.

## Verify the private archive offline

From the repository root, use Python 3.10 or newer:

```bash
python3 scripts/benchmarks/validate_sf1_handoff.py /path/to/sf1-vllm-v1.zip \
  --expected-archive-sha256 DIGEST_FROM_THE_SENDER
```

The expected digest must come from the sender or another trusted record. A
manifest inside an archive checks internal consistency, not authenticity.
The validator limits archives to 8 MiB, members to 1 MiB each, total expanded
content to 8 MiB, and member count to 128.
Keep the original ZIP private. Do not commit questions, expected answers, raw
provider payloads, connection details, or the full handoff archive.
The reviewed GUG summaries and private S3 evidence pointer are in the
[experiment record](../../experiments/gug-20261030-20261102/README.md).

The validator reads ZIP members without extracting or executing them. It checks
manifest coverage and hashes, request identities, paired variants, schema tool
references, and declared dataset bindings. Its JSON output contains counts,
digests, and status codes. Malformed input must not echo private content.

`STATIC_PASS_NOT_RUNTIME` means these static checks passed. It does not mean
that a database, model, kernel, scorer, or live execution passed. Query-reference
gaps are reported separately and require the checks below before a run.

## Keep the two query hash meanings separate

The supplied manifest hashes complete file bytes. The historical tool registry
instead reads a Cypher file as text, applies `.strip()`, and hashes the UTF-8
text that it binds. A trailing newline can therefore change a manifest hash
without changing the bound-query hash.

There is a second distinction. The 32 requests retain query references from the
original workload while this bundle supplies optimized queries. In the reviewed
v1 archive, all 20 manifest entries matched their files. Request references
matched 20 of 32 optimized query texts; the other 12 belonged to L4, L5, and L6.
Their original query texts matched the preserved references when checked
separately. This is provenance, not evidence that the optimized query is wrong
or equivalent.

Before execution, record all of the following:

1. Original and optimized file-byte hashes and bound-query text hashes.
2. The registry's actual tool/argument to filename selection, including hop
   variants and workspace injection.
3. Ordered result equality against the frozen oracle, including empty results,
   parallel relationships, time boundaries, and relevant semantic counterexamples.
4. Actual query hashes in each execution receipt. Do not replace the archived
   request hash merely to make a check pass.

The offline validator reports hash membership, not the registry's semantic
mapping or the correctness of expected answers. A hash match alone does not
satisfy steps 2 or 3.

## Separate historical and receiving conditions

| Condition | Historical handoff | Current FP8 presentation campaign |
| --- | --- | --- |
| Target | Qwen3.8-27B BF16, historical revision | Qwen3.8-27B-FP8, snapshot `017b9c7a` |
| Thinking | Disabled | High by default; off is a registered ablation |
| Sampling | Temperature 0, seed 42 | Temperature 0, seed 42 |
| Output budget | 800 per call | 800 total per call, including reasoning |
| Tensor parallelism | 1 | Seal the approved GPU count and TP before launch |
| Engine baseline | vLLM 0.31.0, seq8, batch8192, context8192, KV12GiB, APC on | Same declared settings; verify the actual resolved configuration |
| Database | Neo4j 5.26.0, heap512MiB/pagecache256MiB, Bolt workers32 | Preserve the chosen frozen database condition across serving arms |
| Tool transport | Official Python driver, pool16 plus canary1 | Preserve the selected transport; Neo4rs results are a separate experiment |

The historical BF16 condition is not an allowed fallback for the FP8 campaign.
Do not invoke or copy a BF16 launcher unchanged. Verify FP8 weight configuration,
full revision, model/tokenizer/template hashes, and actual Marlin W8A16 startup
logs. An activation or accumulation dtype of `bfloat16` alone does not establish
BF16 target weights. Missing FP8 evidence is a failed gate, not permission to
restore another checkpoint.

Logical `high` can map to wire `xhigh` in the pi adapter. Record the actual SDK
and provider payload, chat template, and reasoning controls for the chosen path.
Both reasoning and tool/final output consume the 800-token budget. Check input
plus the requested output budget against context8192; record rejected or
truncated cases without silently increasing limits.

The handoff's seq16 and phase-priority suggestions remain proposals. They do not
change the receiving campaign's fixed seq8 baseline or authorize another run.
A small FP8 comparison model also requires a separately selected and pinned
candidate.

## Bind the environment before a live run

- Verify the approved resource owner and current GPU, process start identities,
  lease, ports, storage, and store lock. Idle GPU memory alone is not ownership.
- Verify the SF1 snapshot digest, declared workspace, indexes and constraints,
  resolved database settings, and actual query/schema versions. Sidecar nodes
  can make total store counts differ from canonical entity counts.
- Preserve the original store. Do not start two database processes on the same
  store, tune a shared database, or stop another owner's service.
- Reuse the original evaluator and scorer only after their source and typed-tool
  contracts are checked. The repository's existing synthetic FinBench example
  is not automatically the official SF1 workload in this bundle.
- Preserve all offered, failed, dropped, cancelled, and unanswered occurrences.
  Repeated bursts need unique occurrence IDs; do not pool duplicate request IDs.
- Report DB roundtrip, model/provider wall time, queue, tokens, calls, quality,
  and end-to-end time separately. A finite 32-request burst is not steady-state
  capacity, and HTTP wall time is not GPU decode time.

The historical handoff and subsequent memory/driver experiments used different
model, driver, or token-budget conditions. Their timings cannot establish a new
FP8 query optimization effect. That comparison needs original versus optimized
queries under the same FP8 target and receiving configuration.

For authorized live work, continue through [HARNESS](HARNESS.md) and the
[campaign's experiment guide](AGENTS.md). Preserve source and raw evidence under
the campaign's private artifact policy. An offline inspection starts no live
experiment and does not resume a paused automation.
