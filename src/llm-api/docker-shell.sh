#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BASE_DIR="$SCRIPT_DIR"
export IMAGE_NAME="${IMAGE_NAME:-consult-llm-api}"
export CONTAINER_NAME="${CONTAINER_NAME:-consult-llm-api}"
export HOST_PORT="${HOST_PORT:-8081}"
export CONTAINER_PORT=8081
# export SECRETS_DIR="${SECRETS_DIR:-$BASE_DIR/../../secrets}"

# Detect if running on GCP VM
if curl -s --connect-timeout 1 http://metadata.google.internal/ >/dev/null 2>&1; then
    echo "☁️ Running on GCP VM"
    # On VM, use the VM's service account (don't set GOOGLE_APPLICATION_CREDENTIALS)
    unset GOOGLE_APPLICATION_CREDENTIALS
else
    echo "💻 Running locally"
    # On local machine, use JSON key if it exists
    # export GOOGLE_APPLICATION_CREDENTIALS="/secrets/apcomp215-project-0a74ac035654.json"
    export GOOGLE_APPLICATION_CREDENTIALS="/secrets/llm-service-account_matlin.json"
fi



echo "🏗️  Building LLM API image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"

echo "🚀 Running container ${CONTAINER_NAME} on port ${HOST_PORT}"
docker run --rm -it \
  --name "${CONTAINER_NAME}" \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  -e GCP_PROJECT="${GCP_PROJECT:-ac215-ms4}" \
  -e GCP_LOCATION="${GCP_LOCATION:-us-central1}" \
  -e API_ALLOW_ORIGINS="${API_ALLOW_ORIGINS:-http://localhost:8080}" \
  -e GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-}" \
  -v "../../secrets:/secrets" \
  "$@" \
  "${IMAGE_NAME}"
