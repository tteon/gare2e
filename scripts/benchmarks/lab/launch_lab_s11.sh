#!/usr/bin/env bash
# Retry the fail-closed lifecycle runner until an offer is admitted and an
# instance is created. Each attempt gets its own run dir; admission misses are
# free (no instance is created before admission).
set -uo pipefail
cd /home/hadry/lab/seocho-room/seocho
B=.seocho/benchmarks/agentic-os/colocated-appliance/bootstrap; G10=examples/agentic-os/frozen/gate-10; IMP=$B/grbench-import-v1; ST=.seocho/benchmarks/agentic-os/strata-v2
RUNS=.seocho/benchmarks/agentic-os/colocated-appliance/runs
for attempt in $(seq 1 120); do
  # Ownership gate, label-scoped: the Vast key is shared with a teammate (gare2e handoff),
  # so only *our* live instances (label prefix seocho-agentic-os-) block a launch.  Print ids
  # and labels only — the raw instance JSON carries the jupyter token.
  gate=$(timeout 60 vastai show instances --raw 2>/dev/null | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print("ERROR unreadable"); sys.exit(0)
ours=[(i["id"],i.get("label")) for i in d if str(i.get("label") or "").startswith("seocho-agentic-os-")]
others=[(i["id"],i.get("label"),i.get("actual_status")) for i in d if (i["id"],i.get("label")) not in ours]
print("OURS" if ours else "CLEAR", ours, "others:", others)')
  case "$gate" in CLEAR*) echo "$(date -Is) gate: $gate";; *) echo "$(date -Is) ownership gate blocked: $gate"; exit 9;; esac
  RUN=$RUNS/lab-s11-try-$(date +%H%M%S)
  python3 scripts/benchmarks/vast_colocated_agentic_os_run.py \
    --offer-query "num_gpus=2 rentable=true cuda_max_good>=13.0 gpu_ram>=79" --gpu-name-regex "A100|RTX PRO 6000" \
    --min-num-gpus 2 --min-gpu-total-ram 163840 --min-effective-cpu 16 \
    --min-cpu-ram-mb 65536 --min-reliability 0.98 --exclude-host-id 458442 --exclude-host-id 3497 --exclude-host-id 60897 \
    --image vllm/vllm-openai:v0.27.1 --disk-gb 200 \
    --label seocho-agentic-os-lab-s11 --max-dph 2.9 \
    --startup-timeout-s 900 --max-runtime-s 14400 \
    --remote-artifacts /artifacts --local-artifacts "$RUN" \
    --upload scripts/benchmarks/bootstrap_agentic_os_appliance.sh=/workspace/bootstrap/bootstrap_agentic_os_appliance.sh \
    --upload $B/dozerdb-runtime.tar.gz=/workspace/bootstrap/dozerdb-runtime.tar.gz \
    --upload $B/seocho-source-v40.tar.gz=/workspace/bootstrap/seocho-source.tar.gz \
    --upload $IMP/grbench-nodes.csv.gz=/workspace/input/grbench-nodes.csv.gz \
    --upload $IMP/grbench-rels.csv.gz=/workspace/input/grbench-rels.csv.gz \
    --upload $IMP/grbench-import-manifest.json=/workspace/input/grbench-import-manifest.json \
    --upload .seocho/datasets/graph-cot/data/processed_data/maple/Physics/data.json=/workspace/input/grbench-questions.json \
    --upload $G10/grbench-discovery-serving.json=/workspace/input/grbench-discovery.json \
    --upload $G10/grbench-single-tool-schedule.json=/workspace/input/grbench-single-tool-schedule.json \
    --upload .seocho/datasets/gtsqa/gtsqa-with-graphs/test-00000-of-00001.parquet=/workspace/input/gtsqa-with-graphs.parquet \
    --upload $G10/gtsqa-discovery-serving.json=/workspace/input/gtsqa-discovery.json \
    --upload $G10/gtsqa-single-tool-schedule.json=/workspace/input/gtsqa-single-tool-schedule.json \
    --upload $G10/gtsqa-multi-tool-schedule.json=/workspace/input/gtsqa-multi-tool-schedule.json \
    --upload $ST/grbench-discovery-records.jsonl=/workspace/input/strata-grbench-discovery-records.jsonl \
    --upload $ST/gtsqa-discovery-records.jsonl=/workspace/input/strata-gtsqa-discovery-records.jsonl \
    --upload $ST/grbench-tool-schema.json=/workspace/input/strata-grbench-tool-schema.json \
    --upload $ST/gtsqa-tool-schema.json=/workspace/input/strata-gtsqa-tool-schema.json \
    --upload $ST/grbench-single-schedule.json=/workspace/input/strata-grbench-single-schedule.json \
    --upload $ST/gtsqa-single-schedule.json=/workspace/input/strata-gtsqa-single-schedule.json \
    --upload $ST/gtsqa-multi-schedule.json=/workspace/input/strata-gtsqa-multi-schedule.json \
    --upload $ST/session-E/lab-queue.tsv=/workspace/input/lab-queue.tsv \
    --remote-command "timeout --signal=TERM 13800 env VLLM_MODEL=Qwen/Qwen3.6-27B VLLM_MODEL_REVISION=6a9e13bd6fc8f0983b9b99948120bc37f49c13e9 VLLM_TENSOR_PARALLEL_SIZE=2 VLLM_EXTRA_ARGS='--gpu-memory-utilization 0.92 --max-model-len 32768' SEOCHO_GRBENCH_IMPORT_DIR=/workspace/input SEOCHO_GRBENCH_WORKSPACE_ID=grbench-maple-physics DOZERDB_HEAP_INITIAL_SIZE=8g DOZERDB_HEAP_MAX_SIZE=8g DOZERDB_PAGECACHE_SIZE=24g SEOCHO_REPO_ROOT=/workspace/seocho SEOCHO_APPLIANCE_INPUT=/workspace/input SEOCHO_APPLIANCE_ARTIFACTS=/artifacts SEOCHO_SPLIT_GATE_MAIN=0 SEOCHO_DISABLE_GPU_SAMPLER=1 SEOCHO_OTLP_ENDPOINT=http://127.0.0.1:4317 SEOCHO_OTEL_FILE_RECEIVER=1 bash /workspace/bootstrap/bootstrap_agentic_os_appliance.sh bash /workspace/seocho/docker/agentic-os-appliance/run_lab_queue.sh /workspace/input/lab-queue.tsv"
  rc=$?
  created=$(python3 -c "import json,sys;d=json.load(open('$RUN/vast-colocated-lifecycle.json'));print(d.get('instance_id') or '')" 2>/dev/null)
  if [ -n "$created" ]; then echo "$(date -Is) attempt $attempt: instance $created lifecycle finished rc=$rc run=$RUN"; exit $rc; fi
  msg=$(python3 -c "import json;d=json.load(open('$RUN/vast-colocated-lifecycle.json'));print(d.get('failure_message'))" 2>/dev/null)
  case "$msg" in *"absent or ambiguous"*|*"exceeds cost ceiling"*) rmdir "$RUN" 2>/dev/null || true; echo "$(date -Is) attempt $attempt: no admitted offer; retry in 60s"; sleep 60;; *) echo "$(date -Is) attempt $attempt: non-admission failure: $msg"; exit $rc;; esac
done
echo "$(date -Is) gave up after 120 attempts"; exit 8
