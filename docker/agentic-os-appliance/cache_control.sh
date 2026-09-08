#!/usr/bin/env bash
# Reset only vLLM's local prefix cache for schedule entries labelled cold.
# The endpoint is loopback-only and enabled solely inside the benchmark
# appliance.  Database page-cache state is deliberately not relabelled.
set -euo pipefail
umask 077

phase="${1:?cache phase required}"
paired_unit="${2:?paired unit required}"
: "${SEOCHO_APPLIANCE_ARTIFACTS:=/artifacts}"
: "${VLLM_PORT:=8000}"
receipt="$SEOCHO_APPLIANCE_ARTIFACTS/e2e/cache-control.private.tsv"
mkdir -p "$(dirname "$receipt")"

case "$phase" in
  cold)
    response="$(curl --fail --silent --show-error --request POST \
      "http://127.0.0.1:${VLLM_PORT}/reset_prefix_cache")"
    python3 -c 'import json,sys; assert json.loads(sys.argv[1]).get("success") is True' "$response"
    action="vllm_prefix_cache_reset"
    ;;
  warm|none)
    action="preserve_vllm_prefix_cache"
    ;;
  *)
    printf 'unsupported cache phase: %s\n' "$phase" >&2
    exit 2
    ;;
esac

unit_digest="$(printf '%s' "$paired_unit" | sha256sum | awk '{print $1}')"
printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "$phase" "$action" "$unit_digest" >> "$receipt"
