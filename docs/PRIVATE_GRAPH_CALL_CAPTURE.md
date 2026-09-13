# Private graph-call capture

The GTSQA single-agent and manager/specialist runners retain actual agent calls
through `PrivateTrace` and the existing `LLMRequestTimer` HTTP hooks. Public
receipts remain content-free. No prompts, tools, graph inputs, model settings,
turn limits, or sampling parameters are changed by enabling storage.

## Connect the current harness

Set `SEOCHO_PRIVATE_TRACE_CONFIG` to an absolute path to an **ignored, local**
JSON configuration before launching the existing workload/schedule. The schedule
inherits this environment and invokes these collection-enabled entry points:

- `scripts/benchmarks/run_gtsqa_text2sparql_react.py`
- `scripts/benchmarks/run_gtsqa_agents_sdk_handoff.py`

No schedule, deployment, or serving restart is required by this code change.
Already-running processes do not load new code/configuration. The GPU owner
must choose when to use the updated checkout. Other graph benchmark entry
points are not connected by this change.

Example configuration shape (replace placeholders with verified local values;
never commit an actual configuration or credential):

```json
{
  "spool_root": "/absolute/ignored/outputs/private-traces",
  "bucket": "EXISTING_BUCKET",
  "authorized_prefix": "EXISTING_PREFIX",
  "prefix": "EXISTING_PREFIX/private/graph-calls",
  "capture_token_ids": true,
  "flush_max_objects": 20,
  "flush_max_seconds": 30,
  "frozen_case_ids": ["FROZEN_CASE_ID"],
  "serving_provenance": {
    "model": "PINNED_MODEL_ID",
    "model_revision": "PINNED_REVISION",
    "tokenizer_sha256": "EXACT_TOKENIZER_ARTIFACT_DIGEST",
    "template_sha256": "EFFECTIVE_SERVER_TEMPLATE_DIGEST",
    "source_sha256": "SERVING_PROVENANCE_ARTIFACT_DIGEST"
  },
  "selection": {
    "CASE_ID": {
      "source_dataset": "Graphcore/GTSQA",
      "source_revision": "PINNED_DATASET_REVISION",
      "source_split": "train",
      "split": "train",
      "dataset_sha256": "RUNNER_PARQUET_DIGEST",
      "case_sha256": "CASE_DIGEST",
      "graph_sha256": "GRAPH_DIGEST",
      "seed_group": "PREASSIGNED_GROUP"
    }
  }
}
```

`capture_token_ids` requests only the endpoint's `return_token_ids` response
metadata. Enable it only when that endpoint supports it; there is no automatic
retry with changed settings on an unsupported endpoint. Otherwise preserve the
call with unavailable IDs and `training_eligible=false`. No token IDs or
reasoning are reconstructed. Single-agent `--capture-token-ids` and
`--capture-wire-bytes` also remain available for local-only collection.

The optional S3 uploader uses boto3's existing AWS environment/profile/role
configuration. Install/use boto3 in the collection environment if absent; it
is imported only during upload. It does not load `.env` automatically. Export
only the existing required AWS settings locally, without logging them. The
private configuration contains no credentials. No bucket policy, ACL, public
access, or IAM changes are performed. The prefix must be a descendant of the
existing authorized prefix under `/private/`; this is a namespace check, not a
claim that the uploader audited the bucket's access policy.

## Source split and eligibility

Preassign the **whole selection registry** by case, graph, and seed-group,
including existing train and held-out cases, before expanding calls. A group
assigned to different splits is rejected at startup. Never label an eval/test
source as train: `source_split` records the original source partition, while
`split` records the selection partition. Official train held-out cases use
`source_split=train, split=validation`. Provide the full frozen evaluation ID
exclusion list; do not include frozen answers or traces in this registry.

Graph digests use `canonical_digest([edge.to_triple() for edge in
case.graph_edges])`. Case digests use `canonical_digest({"question":
case.question, "graph_sha256": graph_digest})`; parquet digests hash the exact
file bytes. These are checked against the actual runner inputs. Source row and
shard digests may additionally be recorded as `source_row_sha256` and
`source_shard_sha256`. The registry and serving provenance must come from the
operator's verified source audit and pinned server artifacts; storage does
not independently query the server's weight revision. Arbitrary extra config
fields are not copied into samples.

Missing/mismatched provenance, missing exclusions, a non-train split, failed
attempts, missing exact wire JSON, unavailable/mismatched endpoint token IDs,
missing usage/finish reason, or unresolved emitted tool calls make a sample
ineligible. QA accuracy is **not** an eligibility condition. Validation and
other source splits remain stored separately in provenance and are never
promoted to training automatically. Retain `ineligible_reasons` in downstream
processing and run the independent dataset audit before training.

## Stored records and recovery

An append-only mode-0600 journal records event revisions before and after
calls; its directory is mode 0700. A process interrupted during a request can
leave an explicit incomplete call. A torn final journal line is recoverable;
invalid complete lines fail rather than silently dropping earlier data.

Episode finalization writes an immutable, SHA-addressed JSONL payload and a
manifest. **Each JSONL row is one HTTP attempt**, with stable sample/call IDs,
retry linkage, agent/branch/parent IDs, full SDK and exact HTTP JSON bodies,
messages/tools, provided reasoning/native tool calls, exact tool return values,
endpoint token IDs, usage/finish reason and provenance. Failed socket attempts
retain the outgoing request with an unavailable response. Each row contains
its episode's tool events so terminal tool results remain available even when
there is no subsequent model request. Native model responses are preserved
verbatim even if the existing runner normalizes them before its next call.

The manifest has payload bytes/SHA, sample IDs and per-row SHA, total calls,
and training-eligible calls. Deduplicate by sample ID and SHA, not by question
text. Re-sealing an unchanged journal produces the same payload. Recovery of a
previously partial episode can create a newer payload containing overlapping
sample IDs; retain history and use the completed revision, never sum both.
`previous_same_tool_event_id` is an ordering link, not an assertion that every
repeated tool is a retry.

The runner flushes at ordinary process exit, outside model requests. Failed
uploads stay queued. A payload and its manifest are acknowledged locally only
after both are downloaded and their SHA-256 verified. Existing `.uploaded`
receipts skip acknowledged payloads. `.failure.json` records only error class
and time; `delivery-status.json` reports verified/failed/pending counts.
Partial uploads are safely retried at the same content-addressed keys.

Resume delivery without inference:

```sh
python -m seocho.eval.private_trace_store --config /absolute/ignored/capture.private.json
```

After the journal writer has **stopped**, recover interrupted episodes too:

```sh
python -m seocho.eval.private_trace_store --config /absolute/ignored/capture.private.json --recover
```

Flush bounds are per invocation: maximum objects and admission deadline. One
in-flight object can exceed that deadline by its finite SDK operations
(connect timeout 3s, read timeout 5s, one SDK attempt). Reinvoke until pending
is zero. Journal fsync and body capture add instrumentation overhead; no
latency-preservation claim is made. Never delete the spool before verifying
all intended objects. Keep it under an ignored output path.

Approximately 5,000 calls can come from many turns across diverse problems;
problem count, call count, and optimizer steps are separate. This implementation
does not generate additional problems, launch training, or change a frozen
schedule just to reach a count.

## Validation evidence

Contract tests exercise real runner/Agents SDK boundaries with mocked model
responses, exact tool output capture, manager/specialist linkage, HTTP retries,
secret canaries, split rejection, interrupted spool recovery and S3 failures.
Mock responses are not training data or live service compatibility evidence.
A separate existing-S3 smoke uploaded **one preserved real graph episode,
two calls**, then verified the downloaded payload and manifest SHA. Those
historical imports remain ineligible pending requalification. Newly generated
calls in that smoke: **0**. No GPU or serving lifecycle operation was used.
