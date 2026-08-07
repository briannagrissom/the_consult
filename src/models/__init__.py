"""Models package: RAG chunking, embedding, and ChromaDB ingestion.

Loads environment variables from a ``.env`` file on import.

This runs before any ``models.*`` submodule, so module-level
``os.environ.get(...)`` lookups (bucket names, ChromaDB host,
``GOOGLE_APPLICATION_CREDENTIALS``) see the values. ``load_dotenv`` does not
override variables that are already set, so a real environment (Docker,
Kubernetes, CI) always wins over a local ``.env``.
"""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Prefer a .env found by walking up from the current working directory, so
# running from a subdirectory still picks up the repo-root file. Fall back to
# the repo root relative to this file for cases where the CWD is elsewhere.
_dotenv_path = find_dotenv(usecwd=True) or (Path(__file__).resolve().parents[2] / ".env")

load_dotenv(_dotenv_path)
