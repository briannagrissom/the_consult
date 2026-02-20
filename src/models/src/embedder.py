import os

# import math
import time
from typing import List, Sequence, Tuple

# Iterable

from google import genai
from google.genai import types, errors

# from tqdm import tqdm


GCP_PROJECT = os.environ.get("GCP_PROJECT", "local-test-project")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-005")
EMBEDDING_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "256"))
DEFAULT_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "100"))
MAX_RETRIES = int(os.environ.get("EMBEDDING_MAX_RETRIES", "5"))
RETRY_DELAY = float(os.environ.get("EMBEDDING_RETRY_DELAY", "5.0"))

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GCP_PROJECT:
            raise RuntimeError("Environment variable GCP_PROJECT must be set before creating the embedder client.")

        _client = genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION,
        )
    return _client


def _valid_chunks(chunks: Sequence[str]) -> List[str]:
    texts = []
    for chunk in chunks:
        if isinstance(chunk, str) and chunk.strip():
            texts.append(chunk)
    return texts


def embed_texts(
    texts: Sequence[str],
    dimensionality: int = EMBEDDING_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE,  # kept for signature but unused
    max_retries: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY,
    progress_desc: str | None = "Embedding chunks",
) -> List[List[float]]:
    payload = _valid_chunks(texts)
    if not payload:
        return []

    client = _get_client()
    attempt = 0

    while True:
        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=payload,  # entire payload in one call
                config=types.EmbedContentConfig(output_dimensionality=dimensionality),
            )
            return [e.values for e in response.embeddings]

        except errors.APIError:
            attempt += 1
            if attempt > max_retries:
                raise
            time.sleep(retry_delay * (2 ** (attempt - 1)))


def embed_chunk_lists(
    chunk_lists: Sequence[Sequence[str]],
    dimensionality: int = EMBEDDING_DIMENSION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_desc: str | None = "Embedding chunks",
) -> Tuple[
    List[Tuple[int, int]],
    List[str],
    List[List[float]],
    List[int],
]:
    chunk_map: List[Tuple[int, int]] = []
    chunk_texts: List[str] = []
    chunk_sizes: List[int] = []

    for row_idx, chunks in enumerate(chunk_lists):
        if not chunks:
            chunk_sizes.append(0)
            continue
        filtered = [c for c in chunks if isinstance(c, str) and c.strip()]
        chunk_sizes.append(len(filtered))
        for chunk_idx, c in enumerate(filtered):
            chunk_map.append((row_idx, chunk_idx))
            chunk_texts.append(c)

    embeddings = embed_texts(
        chunk_texts,
        dimensionality=dimensionality,
        batch_size=batch_size,
        progress_desc=progress_desc,
    )

    return chunk_map, chunk_texts, embeddings, chunk_sizes
