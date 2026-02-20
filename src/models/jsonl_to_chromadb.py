import os
import chromadb
from chromadb import PersistentClient
from argparse import ArgumentParser

# import json
from .src.gcs import stream_backup_from_gcs

# from .src.gcs import read_backup_from_gcs

# Configuration variables
GCP_PROJECT = os.environ.get("GCP_PROJECT", "apcomp215-project")
BUCKET_NAME = os.environ.get("PROJECT_BUCKET_NAME", "pubmed-bucket-ac215")  # GCS bucket name
CHROMADB_HOST = os.environ.get("CHROMADB_HOST", "35.193.38.202")
CHROMADB_PORT = int(os.environ.get("CHROMADB_PORT", "8000"))
CHROMADB_BATCH_SIZE = int(os.environ.get("CHROMADB_BATCH_SIZE", "25"))
CHROMADB_COLLECTION = "pubmed_abstract_semantic"
CHROMA_LOCAL_PATH = os.environ.get("CHROMA_LOCAL_PATH")  # when set, use embedded client

BACKUP_ENABLED = os.environ.get("ENABLE_GCS_BACKUP", "true").lower() in {"1", "true", "yes"}
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET_NAME", BUCKET_NAME)
BACKUP_PREFIX = os.environ.get("BACKUP_PREFIX", f"chromadb_backups/{CHROMADB_COLLECTION}")


def connect_to_chromadb():
    if CHROMA_LOCAL_PATH:
        print(f"Using local Chroma PersistentClient at {CHROMA_LOCAL_PATH}")
        client = PersistentClient(path=CHROMA_LOCAL_PATH)
    else:
        print(f"Using remote Chroma HttpClient at {CHROMADB_HOST}:{CHROMADB_PORT}")
        client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
    return client


def load_backups_to_chromadb(client, semantic=True):
    if not BACKUP_ENABLED:
        print("Backup loading is disabled.")
        return

    collection = client.get_or_create_collection(name=CHROMADB_COLLECTION)
    print(f"Loading backups into ChromaDB collection '{CHROMADB_COLLECTION}'...")

    batch = []
    total_loaded = 0
    for item in stream_backup_from_gcs(BACKUP_BUCKET, BACKUP_PREFIX):
        batch.append(item)
        if len(batch) >= CHROMADB_BATCH_SIZE:
            _add_batch(collection, batch, semantic)
            total_loaded += len(batch)
            print(f"Loaded {total_loaded} records so far...")
            batch = []

    if batch:
        _add_batch(collection, batch, semantic)
        total_loaded += len(batch)

    print(f"Finished loading {total_loaded} records into '{CHROMADB_COLLECTION}'.")


def _add_batch(collection, batch, semantic):
    ids = [item["id"] for item in batch]
    if semantic:
        try:
            embeddings = [item["embeddings_semantic"] for item in batch]
        except KeyError as e:
            print(e)
            return
    else:
        embeddings = [item["embedding"] for item in batch]
    metadatas = [item["metadata"] for item in batch]
    documents = [item["document"] for item in batch]

    collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)


def main():

    parser = ArgumentParser()
    parser.add_argument("--semantic", action="store_true", help="Use semantic embeddings")
    args = parser.parse_args()

    print("Connecting to ChromaDB...")
    client = connect_to_chromadb()

    print(f"Loading backups to ChromaDB... {CHROMADB_COLLECTION}")
    load_backups_to_chromadb(client, semantic=args.semantic)


if __name__ == "__main__":
    main()
