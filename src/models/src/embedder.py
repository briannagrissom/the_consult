import os

# import math
import time
from typing import Callable, List, Optional, Sequence, Tuple

# Iterable

from langchain_openai import OpenAIEmbeddings
from openai import APIError

# from tqdm import tqdm


EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "1536"))
DEFAULT_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "100"))
MAX_RETRIES = int(os.environ.get("EMBEDDING_MAX_RETRIES", "5"))
RETRY_DELAY = float(os.environ.get("EMBEDDING_RETRY_DELAY", "5.0"))

_client = None


def _get_client() -> OpenAIEmbeddings:
    global _client
    if _client is None:
        _client = OpenAIEmbeddings(model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSION)
    return _client


def _valid_chunks(chunks: Sequence[str]) -> List[str]:
    texts = []
    for chunk in chunks:
        if isinstance(chunk, str) and chunk.strip():
            texts.append(chunk)
    return texts


def embed_texts(
    texts: Sequence[str],
    dimensionality: int = EMBEDDING_DIMENSION,  # kept for signature but unused; fixed on the client at construction
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_retries: int = MAX_RETRIES,
    retry_delay: float = RETRY_DELAY,
    progress_desc: str | None = "Embedding chunks",
    on_batch: Optional[Callable[[List[str], List[List[float]]], None]] = None,
) -> List[List[float]]:
    payload = _valid_chunks(texts)
    if not payload:
        return []

    client = _get_client()
    total = len(payload)
    embeddings: List[List[float]] = []

    for start in range(0, total, batch_size):
        batch = payload[start : start + batch_size]
        attempt = 0
        while True:
            try:
                batch_embeddings = client.embed_documents(batch)
                break
            except APIError:
                attempt += 1
                if attempt > max_retries:
                    raise
                time.sleep(retry_delay * (2 ** (attempt - 1)))

        embeddings.extend(batch_embeddings)
        if on_batch:
            on_batch(batch, batch_embeddings)
        if progress_desc:
            print(f"{progress_desc}: {min(start + batch_size, total)}/{total}")

    return embeddings


def flatten_chunk_lists(
    chunk_lists: Sequence[Sequence[str]],
) -> Tuple[List[Tuple[int, int]], List[str], List[int]]:
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

    return chunk_map, chunk_texts, chunk_sizes


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
    chunk_map, chunk_texts, chunk_sizes = flatten_chunk_lists(chunk_lists)

    embeddings = embed_texts(
        chunk_texts,
        dimensionality=dimensionality,
        batch_size=batch_size,
        progress_desc=progress_desc,
    )

    return chunk_map, chunk_texts, embeddings, chunk_sizes
