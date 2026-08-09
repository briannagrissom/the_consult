"""API package.

Loads environment variables from a ``.env`` file on import.

This runs before any ``api.*`` submodule is imported, so module-level
``os.environ.get(...)`` lookups in ``server.py`` and ``rag_module.py`` see the
values. ``load_dotenv`` does not override variables that are already set, so a
real environment (Docker, Kubernetes, CI) always wins over a local ``.env``.

Finding the file has to work in two very different layouts: a deep checkout
(``<repo>/src/llm-api/api/``) and the container image, where this package sits
at ``/app/api/``. So walk up from here and take the first ``.env`` that exists
rather than indexing a fixed number of parents -- the container has too few
levels for that, and a hardcoded index raises IndexError at import time.
"""

from pathlib import Path

from dotenv import find_dotenv, load_dotenv


def _locate_dotenv() -> str | None:
    # Prefer a .env found by walking up from the current working directory.
    found = find_dotenv(usecwd=True)
    if found:
        return found
    # Otherwise walk up from this file to the filesystem root.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file():
            return str(candidate)
    return None


_dotenv_path = _locate_dotenv()
if _dotenv_path:
    load_dotenv(_dotenv_path)
