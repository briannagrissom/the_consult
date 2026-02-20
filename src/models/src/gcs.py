from google.cloud import storage
import json
import pandas as pd
from io import BytesIO
from tqdm import tqdm
from typing import Generator, Dict, Any  # Iterable


def read_parquet_from_gcs(bucket_name, parquet_folder):
    """Reads a Parquet file from GCS and returns a pandas DataFrame."""
    client = storage.Client()
    bucket = client.get_bucket(bucket_name)

    # List all Parquet files in the specified folder
    parquet_blobs = [blob for blob in bucket.list_blobs(prefix=parquet_folder) if blob.name.endswith(".parquet")]

    print(f"Found {len(parquet_blobs)} parquet files in gs://{bucket_name}/{parquet_folder}")

    alldf = pd.DataFrame()
    for blob in parquet_blobs:
        parquet_bytes = blob.download_as_bytes()
        df = pd.read_parquet(BytesIO(parquet_bytes))
        print(f"Read {len(df)} rows from {blob.name}")
        alldf = pd.concat([alldf, df], ignore_index=True)

    return alldf


def read_backup_from_gcs(bucket_name, backup_prefix):
    """Reads backup files from GCS and returns a list of their contents."""
    client = storage.Client()
    # list the available buckets
    buckets = list(client.list_buckets())
    print(f"Available buckets: {[bucket.name for bucket in buckets]}")
    bucket = client.get_bucket(bucket_name)

    backup_blobs = [
        blob
        for blob in bucket.list_blobs(prefix=backup_prefix)
        if blob.name.endswith(".jsonl") or blob.name.endswith(".json")
    ]

    backups = []
    for blob in tqdm(backup_blobs):
        content = blob.download_as_text()
        print(f"Read backup file {blob.name} with size {len(content)} characters")
        lines = content.splitlines()
        for line in lines:
            line = line.strip()
            line = json.loads(line)
            backups.append(line)  # Store as JSON object

    return backups


def stream_backup_from_gcs(bucket_name: str, backup_prefix: str) -> Generator[Dict[str, Any], None, None]:
    """
    Streams backup files from GCS line-by-line to avoid loading everything into memory.
    Yields parsed JSON objects one at a time.
    """
    client = storage.Client()
    buckets = list(client.list_buckets())
    print(f"Available buckets: {[bucket.name for bucket in buckets]}")
    bucket = client.get_bucket(bucket_name)

    backup_blobs = [
        blob
        for blob in bucket.list_blobs(prefix=backup_prefix)
        if blob.name.endswith(".jsonl") or blob.name.endswith(".json")
    ]

    for blob in tqdm(backup_blobs):
        print(f"Streaming backup file {blob.name} (size: {blob.size} bytes)")
        with blob.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
