# Task / difficulty loader

Offline entry point: `scripts/benchmarks/load_benchmark_tasks.py`.
No downloads, model calls, DB mutation or experiment execution. Existing
benchmark implementations remain unchanged. Use local source snapshots.

| `--task` | `--source` | Difficulty policy |
|---|---|---|
| gtsqa | Official graph-bearing Parquet | Existing structural classifier: easy/medium/hard |
| grbench | Official qid/question/answer JSONL | Existing MAPLE/Physics template classifier; unmatched=unlabeled |
| finbench | Optional agent_interaction.py; repository default | Existing easy/medium/hard question labels |
| finder | SEOCHO FinDER JSON array | Existing beginner/advanced scenario classifier |
| graphrag-bench | Directory with FB/MC/MS/OE/TF.jsonl | unlabeled; question type is separate metadata |
| finance | Private finance JSON array | unlabeled |

These labels are dataset-specific. Beginner/advanced are not silently converted
to easy/hard. GRBench template labels must not be generalized to unmatched
domains. GTSQA classification uses gold-query structure offline for sampling;
do not supply classification features to the agent as inference hints.

From the repository root:

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/load_benchmark_tasks.py \
  --task gtsqa \
  --source .seocho/datasets/gtsqa/gtsqa-with-graphs/test-00000-of-00001.parquet \
  --limit-per-difficulty 10 --seed 42

PYTHONPATH=src .venv/bin/python scripts/benchmarks/load_benchmark_tasks.py \
  --task finbench --difficulty hard

PYTHONPATH=src .venv/bin/python scripts/benchmarks/load_benchmark_tasks.py \
  --task finder --source /path/to/finder.json --difficulty advanced
```

CLI prints a content-free reference manifest (IDs, counts, source hashes,
difficulty provenance); it does not export questions, answers or rationale.
Keep output local. `--limit-per-difficulty` samples each available stratum
without replacement after filtering, deterministically for a fixed seed and
snapshot. It does not create disjoint train/dev/test splits; persist and reuse
case IDs for those. An unavailable requested stratum raises an error.

Python callers can import `load_tasks` from the script directory. Each
`TaskCase` has `case_id`, `difficulty`, `difficulty_source`, `metadata`, and
`payload`. Payload is evaluator-only and can contain gold. Never pass it
wholesale to a model. `reference()` excludes payload. GTSQA payload is a source
locator; load selected graphs with existing `load_gtsqa_cases(case_ids=...)`.
FinBench returns templates: bind account `{a}`, workspace and graph scale
using the original runner. This loader does not provision a graph or calculate
reference-query results. FinDER/finance expect the existing SEOCHO JSON schema:
`id,text,question,expected_answer,category,reasoning_type`; raw HF FinDER rows
must first be adapted to that schema. Categories follow the existing classifier
(e.g. `Company Overview`, including its space).

Checks:

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/test_load_benchmark_tasks.py
```

All six adapters, deterministic sampling, unlabeled/empty selection handling,
duplicate IDs and answer exclusion are tested using synthetic local fixtures.
Real local readback: GTSQA 1622 cases (easy150/medium945/hard527);
FinBench13 templates (easy4/medium4/hard5). This validates loading only,
not benchmark accuracy or runtime readiness. Full basic CI was not run;
validation is scoped to these new offline scripts to avoid unrelated shared
workspace changes. No existing campaign cohort was changed.

## Labeling previously unlabeled cases

Opt in with `--infer-unlabeled`. This fills only missing labels using the
**local question-operations-v1 heuristic**, not official benchmark difficulty
or the existing gold-query structural rubric. Existing labels stay unchanged.
Every automatic proposal has `review_status=needs_review`, its score, detected
signal names and rubric version. Question length, question format (MC/OE/etc.),
gold answers, rationale, observed tokens and model success are not inputs.

Provisional score (English/Korean cues, each signal counted once):

| Requested operation | Points |
|---|---:|
| Explicit lookup, name, definition | 0 |
| Count, sum, average | +1 |
| Comparison, difference, ratio/growth | +1 |
| Temporal/value/exclusion constraint | +1 |
| Maximum/minimum/ranking | +2 |
| Explanation, justification, synthesis | +2 |
| Explicit cross-source integration | +1 |

Detected lookup with score0 → easy; score1–2 → medium; score≥3 → hard.
No recognizable cue → unlabeled. These are hypotheses about requested
operations, not recovered query plans. A simple-looking lookup may require
many hops, and words inside a quoted passage may trigger a false positive.
Review those cases before using labels for a final study. Matching numerical
thresholds does not make these scores comparable to GTSQA gold-query scores.

Review guidance: easy means one directly supported lookup; medium means a
bounded aggregation/comparison or short dependency; hard means multiple
linked operations, cross-source synthesis or compound constraints. Verify
against the task and available corpus/graph, not merely the heuristic score.
Do not infer difficulty from the subject area or answer format alone.

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/load_benchmark_tasks.py \
  --task graphrag-bench --source /path/to/questions --infer-unlabeled
```

For reviewer-approved labels, keep a local JSON sidecar keyed by exact case ID:

```json
{
  "graphrag-bench:OE:000001": {
    "difficulty": "hard",
    "reason": "Requires comparing two independent sources then resolving a conflicting condition.",
    "reviewer": "reviewer-name"
  }
}
```

Pass `--labels /path/to/reviewed-labels.json`, optionally together with
`--infer-unlabeled` for the remaining cases. Explicit annotations take priority.
Unknown case IDs, missing reasons/reviewers and attempts to overwrite an
existing native label fail. Annotation reasons/reviewer strings are not
copied to the CLI reference manifest; the local annotation file and both
classifier source files are SHA-pinned in the manifest. Keep the sidecar
with the source snapshot. A `reviewed` marker records the supplied annotation;
it does not itself prove an independent review took place.

Filtering/sampling happens after annotation. Default behavior without these
flags is unchanged. No production GraphRAG-Bench/private-finance corpus was
available in the checked dataset directory for this addition: tests use
synthetic cases, not a claim that those full corpora have been labeled.
