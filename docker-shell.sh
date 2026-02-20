#!/bin/bash
set -euo pipefail

# Define environment variables
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BASE_DIR="$SCRIPT_DIR"
export IMAGE_NAME="${IMAGE_NAME:-consult-llm-api}"
export CONTAINER_NAME="${CONTAINER_NAME:-consult-llm-api}"
export HOST_PORT="${HOST_PORT:-8081}"
export CONTAINER_PORT=8081
export COMPOSE_FILE="$BASE_DIR/docker-compose.yml"

if curl -s --connect-timeout 1 http://metadata.google.internal/ >/dev/null 2>&1; then
    echo "Running on GCP VM"
    # On VM, use the VM's service account (don't set GOOGLE_APPLICATION_CREDENTIALS)
    unset GOOGLE_APPLICATION_CREDENTIALS
else
    echo "Running locally"
    # On local machine, use JSON key if it exists
    export GOOGLE_APPLICATION_CREDENTIALS="/secrets/apcomp215-project-0a74ac035654.json"
fi

# Build the Docker Image
echo "Building image: $IMAGE_NAME"
echo "Base directory: $BASE_DIR"
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "Using GCP credentials: $GOOGLE_APPLICATION_CREDENTIALS"
else
    echo "Using VM service account credentials"
fi
echo "Compose file: $COMPOSE_FILE"
echo "Image name: ${IMAGE_NAME}"
echo "Script dir: ${SCRIPT_DIR}"

docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
# docker build -t $IMAGE_NAME -f Dockerfile .

# Start an interactive bash shell in the container
echo "Running container ${CONTAINER_NAME} on port ${HOST_PORT}"
docker run --rm -it \
  --name "${CONTAINER_NAME}" \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  -e GCP_PROJECT="${GCP_PROJECT:-ac215-project}" \
  -e GCP_LOCATION="${GCP_LOCATION:-us-central1}" \
  -e API_ALLOW_ORIGINS="${API_ALLOW_ORIGINS:-http://localhost:8080}" \
  -e GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS:-}" \
  -v "$BASE_DIR/secrets:/secrets" \
  "$@" \
  "${IMAGE_NAME}"
