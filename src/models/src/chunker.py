from langchain.text_splitter import RecursiveCharacterTextSplitter

# from models.semantic_splitter import SemanticChunker
import pandas as pd
from multiprocessing import Pool, cpu_count


def _init_splitter(chunk_size: int, chunk_overlap: int):
    global _WORKER_SPLITTER
    _WORKER_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def _split_text_worker(text: str):
    if not text or not isinstance(text, str):
        return []
    return _WORKER_SPLITTER.split_text(text)


def chunk_abstracts(
    df: pd.DataFrame,
    chunk_size: int = 350,
    chunk_overlap: int = 20,
    parallel: bool = True,
) -> pd.DataFrame:
    """Add a column containing RecursiveCharacterTextSplitter chunks for each abstract."""
    df = df.copy()
    abstracts = df["abstract"].tolist()

    if parallel and abstracts:
        workers = max(1, min(cpu_count() - 1 or 1, len(abstracts)))
        print(f"Chunking abstracts in parallel with {workers} workers ...")
        with Pool(
            processes=workers,
            initializer=_init_splitter,
            initargs=(chunk_size, chunk_overlap),
        ) as pool:
            chunk_lists = pool.map(_split_text_worker, abstracts, chunksize=200)
    else:
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunk_lists = [
            splitter.split_text(text) if isinstance(text, str) and text.strip() else [] for text in abstracts
        ]

    df["abstract_chunks"] = chunk_lists
    total_chunks = df["abstract_chunks"].map(len).sum()
    print(f"Generated {total_chunks} total chunks (avg {total_chunks / max(len(df), 1):.2f} per row).")
    return df
