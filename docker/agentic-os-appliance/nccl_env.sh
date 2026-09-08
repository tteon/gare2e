#!/usr/bin/env bash
# Resolve NCCL environment for the co-located appliance from GPU topology.
#
# History: V14 hung forever right after "vLLM is using nccl" on a 2x A100 PCIe
# host whose GPU P2P path was unusable, so the appliance froze
# NCCL_P2P_DISABLE=1 (SHM/socket fallback).  lab-s4 then landed on a 2x A100
# SXM host with NVLink (NV12), where that freeze makes every TP=2 allreduce
# skip NVLink and biases "TP=2 monolith vs 2x TP=1 replicas" against TP=2
# (lab request 008, session B).
#
# Policy (SEOCHO_NCCL_P2P_MODE):
#   auto     (default) enable P2P only when every GPU pair in `nvidia-smi topo -m`
#            is an NV* (NVLink) link; otherwise disable it (PCIe/SYS/NODE/PHB).
#   disable  always NCCL_P2P_DISABLE=1 (the V14 fallback).
#   enable   always NCCL_P2P_DISABLE=0 (operator asserts the fabric is sound).
# An explicit NCCL_P2P_DISABLE in the environment wins over every mode.
# The decision and its reason are exported as SEOCHO_NCCL_P2P_DECISION so the
# gpu-topology artifact records why the run had the fabric it had.

seocho_nccl_topology_links() {
  # Print one token per off-diagonal GPU pair (upper triangle), e.g. NV12 PIX SYS.
  local topo="${1:-}"
  [ -n "$topo" ] || topo="$(nvidia-smi topo -m 2>/dev/null || true)"
  printf '%s\n' "$topo" | awk '
    { gsub(/\033\[[0-9;]*m/, "") }              # nvidia-smi underlines the header
    !seen_header && $1 ~ /GPU0$/ {                 # header row (may keep stray escapes)
      for (i = 1; i <= NF; i++) if ($i ~ /GPU[0-9]+$/) ngpu++
      seen_header = 1; next
    }
    seen_header && $1 ~ /^GPU[0-9]+$/ {
      row++
      for (i = 2; i <= ngpu + 1; i++) {        # GPU columns only (NIC columns excluded)
        if (i - 1 <= row) continue              # diagonal (X) and lower triangle
        print $i
      }
    }'
}

seocho_resolve_nccl_env() {
  local mode="${SEOCHO_NCCL_P2P_MODE:-auto}"
  local topo="${1:-}"
  export NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
  if [ -n "${NCCL_P2P_DISABLE:-}" ]; then
    export SEOCHO_NCCL_P2P_DECISION="explicit:NCCL_P2P_DISABLE=${NCCL_P2P_DISABLE}"
    return 0
  fi
  case "$mode" in
    disable)
      export NCCL_P2P_DISABLE=1 SEOCHO_NCCL_P2P_DECISION="mode=disable"
      ;;
    enable)
      export NCCL_P2P_DISABLE=0 SEOCHO_NCCL_P2P_DECISION="mode=enable"
      ;;
    auto)
      local links n_links n_nvlink
      links="$(seocho_nccl_topology_links "$topo")"
      n_links=$(printf '%s\n' "$links" | grep -c . || true)
      n_nvlink=$(printf '%s\n' "$links" | grep -c '^NV[0-9]' || true)
      if [ "$n_links" -gt 0 ] && [ "$n_links" -eq "$n_nvlink" ]; then
        export NCCL_P2P_DISABLE=0
        export SEOCHO_NCCL_P2P_DECISION="mode=auto links=${n_links} nvlink=${n_nvlink} -> p2p enabled (all pairs NVLink)"
      else
        export NCCL_P2P_DISABLE=1
        export SEOCHO_NCCL_P2P_DECISION="mode=auto links=${n_links} nvlink=${n_nvlink} -> p2p disabled (non-NVLink pair or unknown topology; V14 fallback)"
      fi
      ;;
    *)
      echo "nccl_env.sh: unknown SEOCHO_NCCL_P2P_MODE='$mode' (auto|disable|enable)" >&2
      return 2
      ;;
  esac
}
