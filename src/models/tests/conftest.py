"""Ensure tests can import the `src` package shipped with the models image."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
# tests/ -> models/ -> src/ -> repo root
MODEL_ROOT = HERE.parents[1]  # /app/src/models
REPO_ROOT = HERE.parents[3]  # /app
REPO_SRC = REPO_ROOT / "src"  # /app/src
MODEL_SRC = MODEL_ROOT / "src"  # /app/src/models/src

paths = [MODEL_ROOT, MODEL_SRC, REPO_SRC]
sys.path[:0] = [str(path) for path in paths if path.exists() and str(path) not in sys.path]
