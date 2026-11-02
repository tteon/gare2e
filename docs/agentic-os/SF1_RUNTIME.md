# Prepare the frozen SF1 runtime

Use these opt-in interfaces to adapt a frozen SF1 harness. They do not change
the SDK's default agent, rewrite prompts, replace the scorer, or start a server.
Retrieve original inputs and query texts through the [private handoff](SF1_HANDOFF.md).

## Prepare the FP8 deployment plan

Supply an externally reviewed manifest with a full 40-character `revision`
beginning with `017b9c7a` and a `files` object mapping relative filenames to
SHA-256 digests. Include every model/tokenizer JSON, template, vocabulary and
safetensor file. Include `config.json`, `tokenizer.json`, `tokenizer_config.json`,
`chat_template.jinja`, and `generation_config.json`. Keep the manifest private.

Run this on the host containing the pinned local checkpoint:

```bash
PYTHONPATH=src python scripts/benchmarks/prepare_sf1_fp8.py \
  --model-manifest /private/model-manifest.json \
  --expected-manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
  --gpu-count 2 --tp 2 --max-requests 2 --deadline-ms 180000
```

The command hashes local files, checks FP8 quantization and FP8 weight headers,
and emits `server_argv`, offline environment overrides, and a native `pi_profile`.
It never downloads, restores, launches, or stops anything. `--tp` and
`--gpu-count` are required and must match. These arguments record a choice;
they do not grant resource authorization or prove that GPUs are available.

The plan fixes FP8 weights, vLLM 0.31.0, seed42, context8192, seq8, batch8192,
KV12GiB and APC. The checkpoint, tokenizer, template and generation config all
use `/models/gare2e-pi-precision-fp8-017b9c7a`. The activation dtype remains
`bfloat16`. Weight loading uses safetensors. There is no BF16 target fallback.

Pass the emitted `pi_profile` to the existing native case config's `profile`
field. High is the default and maps to wire `xhigh`; `--thinking off` creates
the registered ablation profile. Seed42 reaches every provider request. Both
reasoning and answer tokens consume the per-call 800-output budget. The total
session accounting ceiling also allows input tokens up to the context limit.
Set request count and deadline from the registered harness, rather than using
this example's two-call contract for every graph agent.

The owning lifecycle runner must consume the command and environment, verify
vLLM's installed version, current GPU/TP allocation and actual resolved settings,
and collect startup logs. To compare that evidence, add both
`--resolved-settings /private/resolved.json` and
`--startup-log /private/server.log`. Resolved settings use the exact keys and
types returned by `SF1FP8Profile.serving_settings()`. The startup check requires
an actual `Using MarlinFP8LinearKernel` or `Using MarlinFP8ScaledMMLinearKernel`
selection line. A generic reference to Marlin is insufficient. The kernel
names follow the pinned [vLLM FP8 implementation](https://github.com/vllm-project/vllm/blob/v0.31.0/vllm/model_executor/layers/quantization/fp8.py).

`STATIC_PASS_RUNTIME_PENDING` checks files only.
`EVIDENCE_MATCH_NOT_LIVE_VERIFIED` compares owner-supplied runtime evidence.
Neither status replaces live model/tool, trace, metrics, ownership, cleanup or
S3/Notion readback gates in [HARNESS](HARNESS.md). Missing kernel evidence stops
the comparison; it never selects another checkpoint or kernel silently.

## Bind private tool definitions

`seocho.integrations.finbench` provides these caller-owned records:

- `QueryTemplate` pins a relative path, file-byte SHA and stripped-text SHA.
- `QueryPair` links original and optional optimized templates.
- `ToolBinding` carries the original function name, description and numeric
  argument schema, finite template variants and cross-field relations.
- `FrozenToolRegistry` loads only those explicit bindings below `query_root`.
  It validates actual arguments and injects a trusted workspace parameter.
- `FrozenQueryExecutor` uses a shared synchronous Neo4j driver, READ routing
  and a transaction timeout. Success and failure receipts contain query hashes,
  row counts and client/server timing boundaries. Query text and rows stay private.

Construct all eight bindings from the private schemas and independently reviewed
template pins. Map `max_hops` to prewritten variants with `selector`; never
interpolate it into Cypher. Declare `start_ts < end_ts` and
`account_min <= account_max` with `ArgumentRelation`. The adapter accepts bounded
numeric properties only and rejects unsupported schema constraints.

Use the actual model-selected function and arguments for dispatch:

```python
from seocho.integrations.finbench import FrozenQueryExecutor, StageAdmission

executor = FrozenQueryExecutor(driver, database="neo4j", on_record=private_receipts.append)
gates = StageAdmission(on_record=private_admissions.append)  # No extra admission by default.

def dispatch(name, arguments, *, arm):
    bound = registry.bind(name, arguments, arm=arm)
    with gates.enter("tool"):
        result = executor.execute(bound)
    return result.rows
```

The harness owns `registry`, `driver` and private receipt sinks. For a selected
experimental admission arm, explicitly configure `tool_slots` and `final_slots`
and hold a `gates.enter("final")` lease only around the existing final LLM call.
`on_record` receives queue timing before execution, including admission failures.
`StageAdmissionTimeout.record` also preserves queue timing on rejection. For an
async harness, use `await gates.run("tool", lambda: executor.execute(bound))`
and the same method around a synchronous final LLM call. Do not wrap `enter`
with plain `asyncio.to_thread`: cancellation could leave a queued call running.
`run` prevents dispatch while cancellation is pending in the admission queue
and drains its worker before propagating cancellation. Already executing work
finishes under the caller's DB/HTTP timeout; cancellation does not interrupt
a synchronous query or release its permit early. Retain the harness's original
deadline and cancellation accounting, including time spent draining the worker.

READ routing is not an authorization or static query-safety proof. Keep reviewed
templates, tenant predicates and read-only DB credentials. Keep gold answers
outside dispatch. `ordered_rows_equal` is an additional strict result check,
including types and ordering; do not replace the original frozen scorer with it.
Preserve original request hashes in optimized arms and record selected hashes
separately. The optional `expected_original_sha256` assertion refers to the
actual selected template's trusted provenance. Do not use the requested case's
expected tool/query hash as a dispatch filter; a wrong model-selected tool must
remain an offered failure under the original harness. Hash identity alone does
not prove query equivalence.

## Preserve generation controls and connection settings

`OpenAICompatibleBackend` and `VLLMBackend` accept `request_policy="strict"`.
In strict mode, synchronous and asynchronous completion propagate provider
errors without dropping reasoning controls, removing response format, changing
the prompt, or increasing the output budget. The task-hint compatibility helper
also propagates unsupported-keyword errors rather than dropping controls.
The OpenAI SDK can retry transport errors with the same payload. Its behavior
is separate from condition-changing compatibility retries. This policy is not
an automatic SF1 generation profile; the native path uses `pi_profile` above.

The default `request_policy="compatible"` retains historical behavior.
`Neo4jGraphStore` now accepts explicit `max_connection_pool_size` and
`connection_acquisition_timeout` keyword arguments. Omit them to retain driver
defaults. The frozen Python-driver condition can use pool16 and a separately
owned canary connection; the store does not create the canary automatically.

No speedup follows from these interfaces alone. Query optimization, stage
admission and any serving candidate need separate matched FP8 comparisons.
Heap/pagecache enlargement, seq16 and phase-priority are not enabled by default.
