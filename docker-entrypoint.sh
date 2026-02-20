#!/bin/bash
set -e

# Define environment variables
export PATH="/home/app/.local/bin:$PATH"

echo "Container is running!!!"

# Activate virtual environment
echo "Activating virtual environment..."
source /app/.venv/bin/activate

# cd /app/src # Navigate to the source directory
echo "Container is running!!!"
echo "Architecture: $(uname -m)" # Log architecture

echo "Environment ready! Virtual environment activated."
echo "Python version: $(python --version)"
echo "UV version: $(uv --version)"

# If arguments were passed, run them
if [ $# -gt 0 ]; then
  exec "$@"
fi

# No command passed → run server
if [ "${DEV}" = "1" ]; then
  echo "Starting API in DEV mode..."
  uvicorn api.server:app --app-dir src/llm-api --host 0.0.0.0 --port 8081 --reload
else
  echo "Starting API in PROD mode..."
  uvicorn api.server:app --app-dir src/llm-api --host 0.0.0.0 --port 8081 --workers 4
fi
