#!/usr/bin/env bash
# Content-free vLLM /metrics scrape for speculative-decoding counters (labs 020/022): counters only, every 2 s.
out=/artifacts/e2e/spec-decode-metrics.json; mkdir -p /artifacts/e2e
while true; do
  ts=$(date +%s.%N)
  curl -s --max-time 3 localhost:8000/metrics | grep -E '^vllm:(spec_decode|generation_tokens_total|num_preemptions)' | sed 's/{[^}]*}//' | awk -v ts="$ts" '{printf "{\"ts\":%s,\"metric\":\"%s\",\"value\":%s}\n", ts, $1, $2}' >> "$out"
  sleep 2
done
