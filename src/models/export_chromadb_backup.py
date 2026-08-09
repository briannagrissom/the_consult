"""Export a local ChromaDB collection to the JSONL backup format jsonl_to_chromadb.py restores.

Streams a collection out in pages so a large corpus never has to fit in memory, writing
one JSON object per line:

    {"id": ..., "document": ..., "metadata": {...}, "embedding": [...]}

That is the shape ``jsonl_to_chromadb.py`` reads on its non-``--semantic`` path, and the
same shape ``parquet_to_chromadb.py`` already writes for its own GCS backups.

Typical use -- move a locally built vector DB into the deployed cluster without paying to
re-embed everything:

    # from src/models, with a local ChromaDB running
    PYTHONPATH=.. uv run python -m models.export_chromadb_backup \
        --out backup.jsonl --upload gs://<bucket>/chromadb_backups/pubmed_abstract/

``--limit`` writes only the first N records, which is useful for measuring the output
size before committing to a full multi-gigabyte export.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import chromadb

CHROMADB_HOST = os.environ.get("CHROMADB_HOST", "localhost")
CHROMADB_PORT = int(os.environ.get("CHROMADB_PORT", "8000"))
DEFAULT_COLLECTION = os.environ.get("CHROMADB_COLLECTION", "pubmed_abstract")


def export_collection(collection, out_path: str, page_size: int, limit: int | None) -> int:
    total = collection.count()
    target = min(total, limit) if limit else total
    print(f"Collection holds {total} records; exporting {target}.")

    written = 0
    with open(out_path, "w", encoding="utf-8") as handle:
        offset = 0
        while written < target:
            page = collection.get(
                include=["documents", "metadatas", "embeddings"],
                limit=min(page_size, target - written),
                offset=offset,
            )
            ids = page.get("ids") or []
            if not ids:
                break

            documents = page.get("documents") or []
            metadatas = page.get("metadatas") or []
            embeddings = page.get("embeddings")
            # chromadb returns embeddings as a numpy array; `or []` would raise on its
            # ambiguous truth value, so normalise explicitly.
            if embeddings is None:
                embeddings = []

            for idx, record_id in enumerate(ids):
                embedding = embeddings[idx]
                handle.write(
                    json.dumps(
                        {
                            "id": record_id,
                            "document": documents[idx] if idx < len(documents) else None,
                            "metadata": metadatas[idx] if idx < len(metadatas) else {},
                            # tolist() for numpy rows; json can't serialise ndarray
                            "embedding": embedding.tolist() if hasattr(embedding, "tolist") else list(embedding),
                        }
                    )
                    + "\n"
                )

            written += len(ids)
            offset += len(ids)
            print(f"  wrote {written}/{target}", end="\r", flush=True)

    print(f"\nWrote {written} records to {out_path}")
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Collection to export")
    parser.add_argument("--out", required=True, help="Output .jsonl path")
    parser.add_argument("--page-size", type=int, default=500, help="Records fetched per page")
    parser.add_argument("--limit", type=int, default=None, help="Export only the first N records")
    parser.add_argument("--upload", default=None, help="GCS prefix to upload the file to, e.g. gs://bucket/prefix/")
    args = parser.parse_args(argv)

    print(f"Connecting to ChromaDB at {CHROMADB_HOST}:{CHROMADB_PORT}")
    client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
    collection = client.get_collection(name=args.collection)

    written = export_collection(collection, args.out, args.page_size, args.limit)
    if written == 0:
        print("Nothing exported; skipping upload.")
        sys.exit(1)

    size_mb = os.path.getsize(args.out) / (1024 * 1024)
    print(f"File size: {size_mb:.1f} MiB ({size_mb / max(written, 1) * 1000:.2f} KiB per record)")

    if args.upload:
        from google.cloud import storage

        target = args.upload.rstrip("/")
        assert target.startswith("gs://"), "--upload must be a gs:// URI"
        bucket_name, _, prefix = target[len("gs://") :].partition("/")
        blob_name = f"{prefix}/{os.path.basename(args.out)}" if prefix else os.path.basename(args.out)

        print(f"Uploading to gs://{bucket_name}/{blob_name} ...")
        client = storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        blob.upload_from_filename(args.out)
        print(f"Uploaded to gs://{bucket_name}/{blob_name}")


if __name__ == "__main__":
    main()
