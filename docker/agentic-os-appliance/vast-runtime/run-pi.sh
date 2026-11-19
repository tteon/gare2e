#!/usr/bin/env bash
# Existing native path, observed wrapper and immutable S3 export; no GPU owner.
set -euo pipefail
umask 077
export AWS_SHARED_CREDENTIALS_FILE=/run/secrets/aws_credentials
export AWS_PROFILE=default
export AWS_EC2_METADATA_DISABLED=true
export NEO4J_PASSWORD="$(cat "${NEO4J_PASSWORD_FILE:-/run/secrets/neo4j_password}")"
exec python /opt/gare2e-pilot/source/docker/agentic-os-appliance/vast-runtime/run_pi.py "${1:?Supply worker config}"
