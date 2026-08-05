import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from .src.chunker import chunk_abstracts
from .src.embedder import embed_texts, flatten_chunk_lists
from .src.gcs import read_parquet_from_gcs

# ChromaDB
import chromadb
from google.cloud import storage

# ! Configuration variables
GCP_PROJECT = GCP_PROJECT = os.environ.get("GCP_PROJECT", "local-test-project")
BUCKET_NAME = os.environ.get("PROJECT_BUCKET_NAME", "pubmed-bucket-ac215")  # GCS bucket name
PARQUET_FOLDER = os.environ.get(
    "PARQUET_SOURCE_PREFIX", "pubmed/filtered_oct23/2020-01-01_2025-12-31"
)  # only process parquet files in this folder
PARQUET_FILENAME = os.environ.get("PARQUET_FILENAME")  # if set, ingest only this one file within PARQUET_FOLDER
CHROMADB_HOST = os.environ.get("CHROMADB_HOST", "35.193.38.202")
CHROMADB_PORT = int(os.environ.get("CHROMADB_PORT", "8000"))
CHROMADB_BATCH_SIZE = int(os.environ.get("CHROMADB_BATCH_SIZE", "50"))
CHROMADB_COLLECTION = "pubmed_abstract"

BACKUP_ENABLED = os.environ.get("ENABLE_GCS_BACKUP", "true").lower() in {"1", "true", "yes"}
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET_NAME", BUCKET_NAME)
BACKUP_PREFIX = os.environ.get("BACKUP_PREFIX", f"chromadb_backups/{CHROMADB_COLLECTION}")

CHECKPOINT_DIR = os.environ.get("EMBEDDING_CHECKPOINT_DIR", os.path.join(os.path.dirname(__file__), "checkpoints"))

METADATA_COLUMNS = [
    "pmid",
    "title",
    "journal_title",
    "publication_date",
    "author_list_full",
    "coi_statement",
    "coi_flag",
    "pubmed_url",
    "is_last_year",
    "is_last_5_years",
    "is_top_journal",
]


def connect_to_chromadb():
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
    return client


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _base_id(df: pd.DataFrame, row_idx: int) -> str:
    row = df.iloc[row_idx]
    pmid_value = _stringify(row["pmid"]) if "pmid" in row else None
    return pmid_value or f"row-{row_idx}"


def _chunk_id(df: pd.DataFrame, row_idx: int, chunk_idx: int) -> str:
    return f"{_base_id(df, row_idx)}-{chunk_idx}"


def _build_chunk_records(
    df: pd.DataFrame,
    chunk_map: Sequence[Tuple[int, int]],
    chunk_texts: Sequence[str],
    embeddings: Sequence[Sequence[float]],
) -> List[Dict[str, Any]]:
    if len(chunk_map) != len(chunk_texts) or len(chunk_map) != len(embeddings):
        raise ValueError("Chunk map, texts, and embeddings must have identical lengths.")

    records: List[Dict[str, Any]] = []
    for (row_idx, chunk_idx), chunk_text, embedding in zip(chunk_map, chunk_texts, embeddings):
        row = df.iloc[row_idx]
        base_id = _base_id(df, row_idx)
        metadata: Dict[str, Any] = {}

        for column in METADATA_COLUMNS:
            if column in df.columns:
                column_value = _stringify(row[column])
                if column_value is not None:
                    metadata[column] = column_value

        metadata["pmid"] = metadata.get("pmid", base_id)
        metadata["chunk_index"] = chunk_idx
        metadata["chunk_char_count"] = len(chunk_text)

        records.append(
            {
                "id": f"{base_id}-{chunk_idx}",
                "document": chunk_text,
                "metadata": metadata,
                "embedding": list(embedding),
            }
        )
    return records


def _upload_records(collection, records: Sequence[Dict[str, Any]], batch_size: int = CHROMADB_BATCH_SIZE):
    total_records = len(records)
    if total_records == 0:
        print("No chunk records to upload to ChromaDB.")
        return

    checkpoint_path = _upload_checkpoint_path()
    uploaded_ids = _load_uploaded_ids(checkpoint_path)
    pending = [item for item in records if item["id"] not in uploaded_ids]
    if uploaded_ids:
        print(f"Resuming upload: {total_records - len(pending)}/{total_records} chunks already uploaded.")

    if not pending:
        print("All chunks already uploaded to ChromaDB.")
        return

    print(f"Uploading {len(pending)} chunk embeddings to ChromaDB (batch size={batch_size}) ...")
    with open(checkpoint_path, "a", encoding="utf-8") as checkpoint_file:
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            # upsert (not add) so re-running after a partial upload doesn't fail on duplicate ids
            collection.upsert(
                ids=[item["id"] for item in batch],
                documents=[item["document"] for item in batch],
                metadatas=[item["metadata"] for item in batch],
                embeddings=[item["embedding"] for item in batch],
            )
            for item in batch:
                checkpoint_file.write(item["id"] + "\n")
            checkpoint_file.flush()
            done = len(uploaded_ids) + min(start + batch_size, len(pending))
            print(f"Inserted {min(start + batch_size, len(pending))}/{len(pending)} pending chunks ({done}/{total_records} total).")


def _checkpoint_slug() -> str:
    scope = f"{PARQUET_FOLDER}/{PARQUET_FILENAME}" if PARQUET_FILENAME else PARQUET_FOLDER
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{CHROMADB_COLLECTION}__{BUCKET_NAME}__{scope}")


def _checkpoint_path() -> str:
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"{_checkpoint_slug()}.jsonl")


def _upload_checkpoint_path() -> str:
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"{_checkpoint_slug()}.uploaded.txt")


def _load_checkpoint(path: str) -> Dict[str, List[float]]:
    embedded_by_id: Dict[str, List[float]] = {}
    if not os.path.exists(path):
        return embedded_by_id
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            embedded_by_id[record["id"]] = record["embedding"]
    return embedded_by_id


def _load_uploaded_ids(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _clear_checkpoint(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def _backup_records_to_gcs(records: Sequence[Dict[str, Any]]):
    if not BACKUP_ENABLED:
        print("GCS backup disabled via ENABLE_GCS_BACKUP.")
        return
    if not records:
        print("No records available for GCS backup.")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    blob_name = f"{BACKUP_PREFIX.rstrip('/')}/{CHROMADB_COLLECTION}-{timestamp}.jsonl"

    print(f"Backing up {len(records)} records to gs://{BACKUP_BUCKET}/{blob_name} ...")
    storage_client = storage.Client()
    bucket = storage_client.bucket(BACKUP_BUCKET)

    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmpfile:
        temp_path = tmpfile.name
        for record in records:
            tmpfile.write(json.dumps(record))
            tmpfile.write("\n")

    try:
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(temp_path)
        print(f"✅ Backup uploaded to gs://{BACKUP_BUCKET}/{blob_name}")
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def main():
    df = read_parquet_from_gcs(BUCKET_NAME, PARQUET_FOLDER, filename=PARQUET_FILENAME)
    print(f"Found {len(df)} rows in the combined DataFrame.")

    print("Chunking abstracts ...")
    df = chunk_abstracts(df)
    print(df[["pmid", "abstract_chunks"]].head())

    client = connect_to_chromadb()

    # Create or get collection
    collection = client.get_or_create_collection(name=CHROMADB_COLLECTION, metadata={"hnsw:space": "cosine"})

    print(f"Using ChromaDB collection: {CHROMADB_COLLECTION}")

    chunk_lists = df["abstract_chunks"].tolist()
    chunk_map, chunk_texts, _chunk_sizes = flatten_chunk_lists(chunk_lists)
    if not chunk_texts:
        print("No non-empty chunks found after chunking. Exiting without uploading data.")
        return

    record_ids = [_chunk_id(df, row_idx, chunk_idx) for row_idx, chunk_idx in chunk_map]

    checkpoint_path = _checkpoint_path()
    embedded_by_id = _load_checkpoint(checkpoint_path)
    if embedded_by_id:
        print(f"Resuming from checkpoint: {len(embedded_by_id)} chunks already embedded ({checkpoint_path}).")

    pending_indices = [i for i, rid in enumerate(record_ids) if rid not in embedded_by_id]
    print(f"{len(pending_indices)} of {len(record_ids)} chunks need embedding.")

    if pending_indices:
        pending_texts = [chunk_texts[i] for i in pending_indices]
        pending_ids = [record_ids[i] for i in pending_indices]
        cursor = {"pos": 0}

        with open(checkpoint_path, "a", encoding="utf-8") as checkpoint_file:

            def _on_batch(_batch_texts, batch_embeddings):
                start = cursor["pos"]
                for offset, embedding in enumerate(batch_embeddings):
                    rid = pending_ids[start + offset]
                    embedded_by_id[rid] = embedding
                    checkpoint_file.write(json.dumps({"id": rid, "embedding": embedding}) + "\n")
                checkpoint_file.flush()
                cursor["pos"] += len(batch_embeddings)

            embed_texts(pending_texts, on_batch=_on_batch)

    embeddings = [embedded_by_id[rid] for rid in record_ids]

    print(f"Embedding complete for {len(chunk_texts)} chunks.")
    chunk_records = _build_chunk_records(df, chunk_map, chunk_texts, embeddings)
    _upload_records(collection, chunk_records)
    _backup_records_to_gcs(chunk_records)

    _clear_checkpoint(checkpoint_path)
    _clear_checkpoint(_upload_checkpoint_path())


if __name__ == "__main__":
    main()
