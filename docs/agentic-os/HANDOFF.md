# AgenticOS graph-agent e2e — handoff package

What another engineer needs to run the same e2e (co-located Vast appliance:
vLLM + DozerDB + embedded Oxigraph, 8 one-case gate cells, telemetry
decomposition, serving sweeps). Everything in this repo is code and procedure;
the data and credentials below travel separately.

## 1. Access

| item | how |
|---|---|
| this repo (`tteon/gare2e`, private) | GitHub invite; clone, then `uv sync --extra local --extra ci` |
| Vast.ai account | an API key (`vastai set api-key …`) and an SSH public key registered in the Vast console — the runner uses direct SSH/SCP, never `vastai copy`. A shared key means shared billing and shared visibility of instances; the runner's §0 ownership gate assumes the account has no other live instances, so prefer a per-person key or a Vast team sub-account |
| Hugging Face | anonymous download of `Qwen/Qwen3.6-27B` works; a token (`HF_TOKEN`) only speeds it up |
| local tools | `uv`, `docker` (only for the local preflight, which boots the exact vLLM image once), `vastai` CLI, a systemd user session with lingering (`loginctl enable-linger`) so paid runs outlive the shell |

## 2. Data (mostly online; only two derived files)

The five frozen gate-10 files are **in this repo** (`examples/agentic-os/frozen/gate-10/`).
The two benchmark datasets are public downloads. The remaining two inputs are
derived locally by scripts in this repo. Digests are the first 16 hex of sha256
of the copies used for V13–V17 / lab-s2.

| path under `.seocho/` | size | sha256[:16] | provenance / how to regenerate |
|---|---|---|---|
| `datasets/graph-cot/graph-environments/maple/Physics/graph.json` | 4.5 GB | `e188b3b88af63c10` | GRBench MAPLE/Physics official graph (HF `PeterJinGo/GRBench`, Graph-CoT repo) |
| `datasets/graph-cot/data/processed_data/maple/Physics/data.json` | 36 KB | `f6ccb8217d533f7c` | GRBench MAPLE/Physics questions (same source) |
| `datasets/gtsqa/gtsqa-with-graphs/test-00000-of-00001.parquet` | 794 MB | `07e50a155267f46d` | GTSQA "with graphs" test split (HF) |
| `benchmarks/agentic-os/colocated-appliance/bootstrap/dozerdb-runtime.tar.gz` | 327 MB | `b15a6e6c9dacac1a` | curated rootfs of `graphstack/dozerdb:5.26.3.0` (`/var/lib/neo4j`, `/opt/java/openjdk`, `/startup`); regenerate with `scripts/benchmarks/export_dozerdb_appliance_rootfs.py` |
| `…/bootstrap/grbench-import-v1/grbench-nodes.csv.gz` | 461 MB | `cc3f0585bff06c7d` | regenerate from graph.json: `scripts/benchmarks/export_grbench_import_csv.py --workspace-id grbench-maple-physics` |
| `…/bootstrap/grbench-import-v1/grbench-rels.csv.gz` | 455 MB | `10932fe1c86517fd` | same exporter run |
| `…/bootstrap/grbench-import-v1/grbench-import-manifest.json` | 4 KB | `8a7ca303e90f677a` | same exporter run (carries the workspace id the runners query) |
| `examples/agentic-os/frozen/gate-10/grbench-discovery-serving.json` (in repo) | 140 KB | `4d72bf1e567ee269` | frozen discovery cache, serving tokens validated by vLLM 0.27.1 for the pinned revision |
| `examples/agentic-os/frozen/gate-10/grbench-single-tool-schedule.json` (in repo) | 12 KB | `501d9061627fbe5b` | frozen paired schedule |
| `examples/agentic-os/frozen/gate-10/gtsqa-discovery-serving.json` (in repo) | 876 KB | `b3aca1e543f321ca` | frozen discovery cache |
| `examples/agentic-os/frozen/gate-10/gtsqa-single-tool-schedule.json` (in repo) | 24 KB | `6fad984bfe7aff51` | frozen paired schedule |
| `examples/agentic-os/frozen/gate-10/gtsqa-multi-tool-schedule.json` (in repo) | 8 KB | `c065d4a19e0e439c` | frozen paired schedule |

So the minimal handoff is: repo access + a Vast API key. Download the two
public datasets, run the two derivation scripts (DozerDB rootfs export needs
Docker; the CSV export needs ~10 GB RAM and a few minutes), and the frozen
files come with the clone.

## 3. Fixed configuration (do not change silently)

- Model `Qwen/Qwen3.6-27B` at revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, served by `vllm/vllm-openai:v0.27.1`, TP=2 on 2× A100 (80 GB total), `--max-model-len 32768`, native tool calling (`--enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3`), prefix caching on.
- GRBench workspace id `grbench-maple-physics` end to end.
- Admission floors: 2 GPUs, ≥ 81,920 MiB GPU RAM, ≥ 16 effective CPUs, ≥ 64 GiB host RAM, reliability ≥ 0.98, ≤ $2.25/h; excluded hosts 458442 and 3497 (NCCL hangs).

## 4. Runbook

Follow `.claude/skills/vast-agentic-e2e/SKILL.md` in order: §0 ownership gate → §1 inputs and bundle (`prepare_agentic_os_appliance_bundle.py`, `preflight_agentic_os_appliance.py`) → §2 pin the model revision → §3 read-only offer check → §4 launch as a `systemd-run --user` unit → §5 wait on the receipt → §6 teardown truth first, then evidence. Gate-only first (`SEOCHO_E2E_PHASE=gate`, ~25–35 min, ≈ $1–2); the matrix only after the gate verdict is understood. For iteration use §4b (persistent instance with an uploaded pass queue) and §4c (declarative serving sweep, `examples/agentic-os/serving-sweep.yaml`).

Analysis after any run: `join_agentic_os_gpu_samples.py` → `analyze_agentic_os_attempt_windows.py` (tables + per-cell timelines), `catalog_agentic_os_results.py` for funnel statistics.

## 5. What to expect

- Cold appliance to services-ready: 7–20 min (bulk import ≈ 2.5 min, vLLM weights + compile 4–17 min depending on host cache).
- Eight gate cells: ≈ 15 min. Raw B0 typically triggers the graph tool 8/8 and passes validation in ~3/8; that is the baseline the treatments are measured against, not a bug.
- Every failure class seen so far and its fix is in `.claude/skills/vast-agentic-e2e/references/incidents.md`; read it before changing bootstrap, image, transport, or teardown.
- Cost discipline: create only right before a validated run; the runner destroys and verifies absence itself; a session whose controller may die must upload a pass queue instead of driving the appliance interactively.

## 6. Handoff checklist

- [ ] repo invite accepted, `uv sync` done, lane tests green (`pytest tests/seocho/test_agentic_os_*.py tests/seocho/test_vast_*.py …`)
- [ ] data package copied to the paths above; digests verified
- [ ] Vast API key + SSH key set; `vastai show instances --raw` returns `[]`
- [ ] local preflight passed on the built bundle
- [ ] first gate-only run completed and torn down; `one-case-gate.json` read
