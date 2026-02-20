#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="${IMAGE_NAME:-consult-frontend}"
HOST_PORT="${HOST_PORT:-8080}"
CONTAINER_PORT=80

echo "🏗️  Building frontend image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"

echo "🚀 Starting container on http://localhost:${HOST_PORT}"
docker run --rm -it \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  "${IMAGE_NAME}" \
  "$@"
