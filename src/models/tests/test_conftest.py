"""
Pytest configuration helpers.

Adds ``src/models/src`` to sys.path so that modules like ``chunker`` can be
imported with ``from chunker import chunk_abstracts`` during tests (matching
how application code runs inside the container).
"""

from __future__ import annotations

import sys
from pathlib import Path

import os
from dotenv import load_dotenv

load_dotenv()

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "/dev/null"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOTS = [
    PROJECT_ROOT,
    PROJECT_ROOT / "src",
]

for path in MODULE_ROOTS:
    # if path.exists():
    path_str = str(path)
    if path.exists() and path_str not in sys.path:
        sys.path.insert(0, path_str)
