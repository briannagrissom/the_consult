"""Models package: RAG chunking, embedding, and ChromaDB ingestion.

Loads environment variables from a ``.env`` file on import.

This runs before any ``models.*`` submodule, so module-level
``os.environ.get(...)`` lookups (bucket names, ChromaDB host,
``GOOGLE_APPLICATION_CREDENTIALS``) see the values. ``load_dotenv`` does not
override variables that are already set, so a real environment (Docker,
Kubernetes, CI) always wins over a local ``.env``.

Finding the file has to work both in a deep checkout (``<repo>/src/models/``)
and inside the container image, where this package sits at ``/app/src/``. Walk
up and take the first ``.env`` that exists rather than indexing a fixed number
of parents, which breaks on the shallower container path.
"""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _locate_dotenv() -> str | None:
    found = find_dotenv(usecwd=True)
    if found:
        return found
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)
    return None


_dotenv_path = _locate_dotenv()
if _dotenv_path:
    load_dotenv(_dotenv_path)
