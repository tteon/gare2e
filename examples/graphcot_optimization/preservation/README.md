# Full research preservation — 2026-11-04

**새 인스턴스로 이전:** [준비물·복원·neo4rs·재현 명령 순서](../FRESH_INSTANCE_RUNBOOK.md).

The tutorial is a small reviewed example. This separate preservation snapshot
retains the original GUG code, configurations, raw experiment results, interrupted
runs, diagnostics, figures and available FinBench inputs/snapshots before the
original instance disappears. It is not a new experiment or a software release.

## Three layers

1. Read the reviewed [results](../RESULTS.md) and [study catalog](STUDIES.md) in Git.
   The [21 historical findings/method reports](reports/README.md) are also readable
   directly in Git, with original source hashes and sanitized operator references.
2. Download the encrypted assets from the private repository's
   [preservation release](https://github.com/tteon/gare2e/releases/tag/gug-research-preservation-20261104).
3. Use the separately held private age identity to restore original files.
   **The key is deliberately absent from Git, the release and the instance.**

Original prompts, responses and operator metadata are private evidence, not
public tutorial assets. They are preserved inside encrypted release attachments.
No plaintext credentials, SSH private keys, age identity, model weights, live
database files, other collaborators' campaigns or local Beads DB are published.
The retained canonical snapshots/import code support logical graph reconstruction;
this is not an online physical Neo4j backup or a model-weight mirror.

## Restore on another machine

Requirements: authorized repository access, Python3.10+, `age` and `zstd` on PATH.
Have the separately backed-up age identity available before retiring any other
copy of the backup. Downloading attachments without the identity is insufficient.

```bash
gh release download gug-research-preservation-20261104 \
  --repo tteon/gare2e --dir /path/to/new-download-directory

python3 /path/to/new-download-directory/restore_research_backup.py verify \
  --manifest /path/to/new-download-directory/BACKUP-MANIFEST.json \
  --identity /path/to/private-recovery.agekey

python3 /path/to/new-download-directory/restore_research_backup.py restore \
  --manifest /path/to/new-download-directory/BACKUP-MANIFEST.json \
  --identity /path/to/private-recovery.agekey \
  --output /path/to/new-restoration-directory
```

The output directory must not exist. Verification checks downloaded ciphertext
size/SHA256, authenticated age decryption, decompression, every content object's
SHA256/size and every original path mapping. Source directories are reconstructed
under `local/` and `remote/`; exact duplicate file bytes are stored once. A
`restoration-index.json` records original paths, sizes, SHA256, mode and mtime.
Restored files initially use restrictive permissions and hard links to identical
content. This preserves file bytes/path identity, not a complete filesystem image.
Copy a file before independently modifying one of its hard-linked duplicates.
Do not restore stale paths over an active instance or blindly restart old controllers.

## Scope and limitations

- `local/` contains the research workspace's selected source, docs, queries,
  configs, handoff and result tree, including existing raw archives. Foreign
  training inspection source is excluded and is not GUG experiment evidence.
- `remote/` contains source/artifacts found only in the owned GUG run roots at
  collection time. Other remote files are mapped to byte-identical local files
  in the encrypted coverage index, rather than downloaded again.
- Interrupted window/parameter/driver attempts remain partial. Saving them
  does not complete their protocol or change their scientific conclusions.
- Model weights, toolchain/venv caches and active DB stores/transaction logs
  are omitted. Recovering a live environment still requires the pinned model,
  dependencies, owned resources and dataset/store readiness checks.
- Known token/private-key/literal-credential patterns and nested archive entries
  were reviewed before encryption. This is a heuristic review, not a proof that
  arbitrary content can be published without further review. Keep decrypted raw
  evidence private; do not put it back into Git.
- The manifest is the preservation completeness receipt. GitHub upload success
  alone is not a restore test; readback/decryption verification is recorded in
  the release notes and final verification receipt.

The restore helper and age/zstd do not need the original instance. The original
checkpoint paths and RAM holders are not serving-ready after process teardown.

## Snapshot receipt

35,631 logical files /22,695 unique SHA256 objects;5,157,195,060 logical bytes.
All26,902 remote candidate files are covered by byte-identical local evidence
or3,152 newly downloaded and hash-verified remote-only files. These counts apply
to the explicitly selected GUG evidence scope, not the entire instance.
Seven encrypted volumes total1,672,089,562 bytes; the exact values are in
[BACKUP-MANIFEST.json](BACKUP-MANIFEST.json) and [COLLECTION-RECEIPT.json](COLLECTION-RECEIPT.json).

**Verified:** every encrypted volume was downloaded again from GitHub and fully
decrypted; all22,695 object hashes and35,631 path mappings matched. A separate
full materialized restoration also passed the original pilot result validator.
See [VERIFICATION.json](VERIFICATION.json). No new inference or DB query was run.

## Final incremental preservation — 2026-11-05

The original instance was stopped (disk retained) after backup verification.
[Project closeout](../PROJECT_CLOSEOUT.md) and [final supplemental release](https://github.com/tteon/gare2e/releases/tag/gug-retirement-20261105)
retain the last GC log and final inventory/status plus recent illustrative artifacts.
31logical files/26objects, one encrypted volume5,620,225bytes; this supplements
the original seven-volume backup. Restore into a separate new directory, never
over an active DB. Same separately held age identity; no key/weights/physicalDB.
[Supplement manifest](retirement-20261105/BACKUP-MANIFEST.json),
[stop receipt](retirement-20261105/INSTANCE-STATUS.json),
[verification](retirement-20261105/VERIFICATION.json).
