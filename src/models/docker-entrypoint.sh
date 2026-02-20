#!/bin/bash
set -e
cd /app/src # Navigate to the source directory
echo "Container is running!!!"
echo "Architecture: $(uname -m)" # Log architecture

echo "Environment ready! Virtual environment activated."
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

# Activate virtual environment
echo "Activating virtual environment..."
source /.venv/bin/activate

# Default ChromaDB settings (overridable via env)
DB_PATH="${CHROMA_DB_PATH:-/chroma/chroma}"
HOST="${CHROMA_SERVER_HOST:-0.0.0.0}"
PORT="${CHROMA_SERVER_PORT:-8000}"

mkdir -p "$DB_PATH"

if [ $# -eq 0 ]; then
  echo "Starting ChromaDB at ${HOST}:${PORT}, path: ${DB_PATH}"
  exec chroma run --host "$HOST" --port "$PORT" --path "$DB_PATH"
fi

# Otherwise, run provided command
exec "$@"
