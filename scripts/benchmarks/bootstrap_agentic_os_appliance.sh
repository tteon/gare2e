#!/usr/bin/env bash
# Bootstrap a no-Docker Vast vLLM container into the co-located appliance.
# Inputs are copied to /workspace/bootstrap before this script is called.
set -euo pipefail

: "${SEOCHO_BOOTSTRAP_ROOT:=/workspace/bootstrap}"
: "${SEOCHO_APPLIANCE_ROOT:=/workspace/seocho}"
: "${SEOCHO_DOZERDB_RUNTIME:=$SEOCHO_BOOTSTRAP_ROOT/dozerdb-runtime.tar.gz}"
: "${SEOCHO_SOURCE_BUNDLE:=$SEOCHO_BOOTSTRAP_ROOT/seocho-source.tar.gz}"
: "${SEOCHO_APPLIANCE_ARTIFACTS:=/artifacts}"
: "${SEOCHO_VENV:=/workspace/seocho-venv}"

umask 077
mkdir -p "$SEOCHO_APPLIANCE_ARTIFACTS"
BOOTSTRAP_PHASE="init"
BOOTSTRAP_PHASE_LOG="$SEOCHO_APPLIANCE_ARTIFACTS/bootstrap-phases.private.tsv"

record_phase() {
  BOOTSTRAP_PHASE="$1"
  printf '%s\tphase\t%s\t0\n' "$(date -Is)" "$BOOTSTRAP_PHASE" >> "$BOOTSTRAP_PHASE_LOG"
}

bootstrap_error() {
  local status=$?
  local line="$1"
  trap - ERR
  printf '%s\terror\t%s\t%s\tline=%s\n' \
    "$(date -Is)" "$BOOTSTRAP_PHASE" "$status" "$line" >> "$BOOTSTRAP_PHASE_LOG"
  printf 'bootstrap failed: phase=%s line=%s exit=%s\n' \
    "$BOOTSTRAP_PHASE" "$line" "$status" >&2
  exit "$status"
}
trap 'bootstrap_error "$LINENO"' ERR

bootstrap_fatal() {
  local status="$1"
  shift
  trap - ERR
  printf '%s\terror\t%s\t%s\texplicit\n' \
    "$(date -Is)" "$BOOTSTRAP_PHASE" "$status" >> "$BOOTSTRAP_PHASE_LOG"
  printf '%s\n' "$*" >&2
  exit "$status"
}

record_phase "binary_preflight"
for required_command in apt-get bash date grep python3 tar; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    bootstrap_fatal 127 "missing bootstrap command: $required_command"
  fi
done

# This password exists only inside the short-lived appliance and is never
# printed or written to benchmark receipts.  Operators may still supply one
# explicitly when recovering a controlled instance.
if [ -z "${NEO4J_PASSWORD:-}" ]; then
  # The vLLM base image is guaranteed to provide Python, but not OpenSSL.
  # Generate the ephemeral appliance-only secret before apt dependencies are
  # installed without adding a bootstrap-time binary dependency.
  NEO4J_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  export NEO4J_PASSWORD
fi

record_phase "input_preflight"
if [ ! -f "$SEOCHO_DOZERDB_RUNTIME" ] || [ ! -f "$SEOCHO_SOURCE_BUNDLE" ]; then
  bootstrap_fatal 2 "missing DozerDB runtime or SEOCHO source bundle"
fi
for required_member in \
  scripts/benchmarks/bootstrap_agentic_os_appliance.sh \
  scripts/benchmarks/run_agentic_os_colocated_e2e.sh \
  scripts/benchmarks/validate_agentic_os_one_case_gate.py \
  docker/agentic-os-appliance/entrypoint.sh \
  docker/agentic-os-appliance/cache_control.sh \
  runtime/policy.py \
  scripts/benchmarks/run_gtsqa_text2sparql_react.py \
  scripts/benchmarks/run_direct_react_text2cypher.py \
  pyproject.toml; do
  if ! tar -tzf "$SEOCHO_SOURCE_BUNDLE" | grep -Fx "$required_member" >/dev/null; then
    bootstrap_fatal 2 "source bundle is missing required member: $required_member"
  fi
done

record_phase "os_dependencies"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl procps util-linux tini
record_phase "dozer_runtime_extract"
tar -xzf "$SEOCHO_DOZERDB_RUNTIME" -C /
record_phase "source_extract"
mkdir -p "$SEOCHO_APPLIANCE_ROOT"
tar -xzf "$SEOCHO_SOURCE_BUNDLE" -C "$SEOCHO_APPLIANCE_ROOT"
record_phase "runtime_layout_preflight"
for required_path in \
  /opt/java/openjdk/bin/java \
  /var/lib/neo4j/bin/neo4j \
  /var/lib/neo4j/bin/neo4j-admin \
  /var/lib/neo4j/bin/cypher-shell \
  "$SEOCHO_APPLIANCE_ROOT/docker/agentic-os-appliance/entrypoint.sh"; do
  if [ ! -x "$required_path" ]; then
    bootstrap_fatal 2 "runtime is missing executable: $required_path"
  fi
done
record_phase "python_dependencies"
python3 -m venv --system-site-packages "$SEOCHO_VENV"
"$SEOCHO_VENV/bin/python" -m pip freeze --all \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-before.private.txt"
"$SEOCHO_VENV/bin/python" -m pip check \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-before.private.log" 2>&1 || true
"$SEOCHO_VENV/bin/python" -m pip install --no-cache-dir \
  "$SEOCHO_APPLIANCE_ROOT" \
  "openai-agents" "neo4j" "pyarrow" "pyoxigraph"
"$SEOCHO_VENV/bin/python" -m pip freeze --all \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-after.private.txt"
# The upstream vLLM image currently carries known optional-package conflicts,
# so preserve the complete post-install result instead of pretending the base
# image is conflict-free.  Import and live-service gates below remain fatal.
if "$SEOCHO_VENV/bin/python" -m pip check \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check.private.log" 2>&1; then
  printf 'pass\n' > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-status.private.txt"
else
  printf 'upstream_or_new_conflicts_present\n' \
    > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-status.private.txt"
fi
sort -u "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-before.private.log" \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-before-sorted.private.txt"
sort -u "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check.private.log" \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-after-sorted.private.txt"
comm -13 \
  "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-before-sorted.private.txt" \
  "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-after-sorted.private.txt" \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-new-conflicts.private.txt"
if [ -s "$SEOCHO_APPLIANCE_ARTIFACTS/pip-check-new-conflicts.private.txt" ]; then
  bootstrap_fatal 2 "benchmark dependency install introduced new pip conflicts"
fi
export PATH="$SEOCHO_VENV/bin:$PATH"

# Import preflight: every lane runner must import inside this exact venv with
# the repo root on sys.path *before* a GPU-minute is spent on vLLM start-up.
# V16 lost all four GTSQA gate cells to a missing ``runtime`` package that
# only surfaced after 17 minutes of model loading.
record_phase "import_preflight"
if ! "$SEOCHO_VENV/bin/python" - "$SEOCHO_APPLIANCE_ROOT" <<'PY' \
  > "$SEOCHO_APPLIANCE_ARTIFACTS/import-preflight.private.log" 2>&1
import importlib, sys
sys.path.insert(0, sys.argv[1])
for name in (
    "runtime.policy",
    "seocho.eval.discovery_cache",
    "seocho.eval.gtsqa",
    "seocho.eval.oxigraph_runtime",
    "seocho.eval.agentic_graph_agents",
    "seocho.eval.agentic_os_artifacts",
    "pyoxigraph",
    "pyarrow.parquet",
    "neo4j",
    "agents",
):
    importlib.import_module(name)
    print("ok", name)
PY
then
  bootstrap_fatal 2 "lane runner imports failed inside the appliance venv; see import-preflight.private.log"
fi

record_phase "appliance_entrypoint"
exec bash "$SEOCHO_APPLIANCE_ROOT/docker/agentic-os-appliance/entrypoint.sh" "$@"
