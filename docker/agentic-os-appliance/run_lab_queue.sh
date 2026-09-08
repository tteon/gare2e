#!/usr/bin/env bash
# Execute an uploaded pass queue on the appliance, unattended.
#
# lab-s2 (2026-09-05) idled for 3.5 paid hours because experiment passes were
# to be issued live from a controller session that died.  The queue makes the
# appliance self-sufficient: every pass is declared up front, runs in order,
# and when the queue ends this script exits, the entrypoint cleans up, and the
# lifecycle runner collects artifacts and destroys the instance.  No hold.
#
# Queue file: TSV, one pass per line:  PASS_ID <TAB> KIND <TAB> PAYLOAD
#   KIND=e2e  PAYLOAD = space-separated KEY=VALUE env for run_lab_pass.sh
#   KIND=cmd  PAYLOAD = a bash command line run from SEOCHO_REPO_ROOT
# Lines starting with # are ignored.  Failures do not stop the queue unless
# SEOCHO_QUEUE_STOP_ON_FAIL=1.  Receipt: /artifacts/queue-receipt.jsonl.
set -uo pipefail
QUEUE="${1:?queue file}"
: "${SEOCHO_REPO_ROOT:=/workspace/seocho}"
RECEIPT="${SEOCHO_APPLIANCE_ARTIFACTS:-/artifacts}/queue-receipt.jsonl"
mkdir -p "$(dirname "$RECEIPT")"
failures=0
while IFS=$'\t' read -r pass_id kind payload; do
  [ -z "$pass_id" ] && continue
  case "$pass_id" in \#*) continue;; esac
  started="$(date +%s.%N)"
  case "$kind" in
    e2e)
      # shellcheck disable=SC2086
      ( cd "$SEOCHO_REPO_ROOT" && env PASS_ID="$pass_id" $payload bash "$SEOCHO_REPO_ROOT/docker/agentic-os-appliance/run_lab_pass.sh" )
      status=$?;;
    cmd)
      ( cd "$SEOCHO_REPO_ROOT" && bash -c "$payload" )
      status=$?;;
    *) status=2;;
  esac
  finished="$(date +%s.%N)"
  printf '{"pass_id":"%s","kind":"%s","exit_code":%s,"wall_s":%s,"finished_unix_s":%s}\n' \
    "$pass_id" "$kind" "$status" "$(python3 -c "print(round($finished-$started,3))")" "$finished" >> "$RECEIPT"
  if [ "$status" -ne 0 ]; then
    failures=$((failures+1))
    [ "${SEOCHO_QUEUE_STOP_ON_FAIL:-0}" = "1" ] && break
  fi
done < "$QUEUE"
[ "$failures" -eq 0 ] && exit 0 || exit 4
