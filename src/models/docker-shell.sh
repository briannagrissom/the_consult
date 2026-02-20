#!/bin/bash
set -e

# -----------------------
# CONFIGURATION
# -----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BASE_DIR="$SCRIPT_DIR"
export PERSISTENT_DIR="$BASE_DIR/../persistent-folder"
export SECRETS_DIR="$BASE_DIR/../../secrets"
export GCP_PROJECT="apcomp215-project"
export IMAGE_NAME="llm-rag-cli"
export SERVICE_NAME="llm-rag-cli"
export COMPOSE_FILE="$BASE_DIR/docker-compose.yml"

# Detect if running on GCP VM
if curl -s --connect-timeout 1 http://metadata.google.internal/ >/dev/null 2>&1; then
    echo "☁️ Running on GCP VM"
    # On VM, use the VM's service account (don't set GOOGLE_APPLICATION_CREDENTIALS)
    unset GOOGLE_APPLICATION_CREDENTIALS
else
    echo "💻 Running locally"
    # On local machine, use JSON key if it exists
    export GOOGLE_APPLICATION_CREDENTIALS="/secrets/apcomp215-project-0a74ac035654.json"
fi

# -----------------------
# LOGGING
# -----------------------
echo "🏗️  Building image: $IMAGE_NAME"
echo "📂 Base directory: $BASE_DIR"
if [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "🔐 Using GCP credentials: $GOOGLE_APPLICATION_CREDENTIALS"
else
    echo "🔐 Using VM service account credentials"
fi
echo "🧠 Compose file: $COMPOSE_FILE"
echo ""

# -----------------------
# NETWORK SETUP
# -----------------------
docker network inspect llm-rag-network >/dev/null 2>&1 || {
  echo "🌐 Creating network: llm-rag-network"
  docker network create llm-rag-network
}

# -----------------------
# BUILD
# -----------------------
echo "🏗️  Building Docker image..."
docker build -t $IMAGE_NAME -f "$BASE_DIR/Dockerfile" "$BASE_DIR"

# -----------------------
# RUN
# -----------------------
# Ensure ChromaDB is running before launching the CLI container
echo "🗄️  Starting ChromaDB service..."
docker-compose -f "$COMPOSE_FILE" up -d chromadb

echo "🚀 Starting service: $SERVICE_NAME"
docker-compose -f "$COMPOSE_FILE" run --rm --service-ports $SERVICE_NAME -v "../../secrets:/secrets"
