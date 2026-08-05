"""Filter, flag, and publish parsed PubMed records as the pipeline's final Parquet output.

This is Step 4 of the pipeline described in ``docker-shell.sh`` / ``README.md``.
It picks up where ``parse_pm_ftp.py`` leaves off:

1. Loads every pickled DataFrame from ``--data-dir`` (default
   ``outputs/pubmed_baseline_ftp_parsed``) and concatenates them.
2. Drops rows with a missing/empty ``abstract`` or an unparsable
   ``publication_date``.
3. Keeps only rows whose ``publication_date`` falls within ``[--from, --to]``.
4. Adds three boolean flag columns computed against a reference date
   (``--reference-date``, default: the date the script runs):
     - ``is_last_year``      -> publication_date within 1 year of the reference date
     - ``is_last_5_years``   -> publication_date within 5 years of the reference date
     - ``is_top_journal``    -> journal_title matches an entry in ``--top-journals-file``
5. Writes the result to Parquet, either locally (``--local``) or to GCS.

NOTE ON PROVENANCE
-------------------
The original ``upload_pm_abstract_ftp.py`` referenced by the README/docker-shell.sh
is not present anywhere in this repo's git history, so the exact original logic
for these three flags (in particular the journal allow-list behind
``is_top_journal`` and whatever reference date it anchored "last year"/"last 5
years" to) is unknown. This script is a reconstruction with explicit,
documented choices:

  - The reference date defaults to "today" (UTC) and is overridable via
    ``--reference-date`` so you can reproduce a specific historical cut.
  - ``is_top_journal`` requires you to supply ``--top-journals-file`` (one
    journal title per line, case-insensitive exact match against
    ``journal_title``). Without it, every row gets ``is_top_journal=False``
    and the script prints a loud warning -- it will NOT silently guess.

Example
-------
Save locally instead of uploading::

    uv run python upload_pm_abstract_ftp.py \\
        --data-dir outputs/pubmed_baseline_ftp_parsed \\
        --from 2020-01-01 --to 2025-12-31 \\
        --top-journals-file top_journals.txt \\
        --local outputs/final_dataset

Upload to GCS::

    uv run python upload_pm_abstract_ftp.py \\
        --data-dir outputs/pubmed_baseline_ftp_parsed \\
        --from 2020-01-01 --to 2025-12-31 \\
        --top-journals-file top_journals.txt
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

LOGGER = logging.getLogger("upload_pm_abstract_ftp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_DATA_DIR = Path("outputs/pubmed_baseline_ftp_parsed")
DEFAULT_ROWS_PER_FILE = 300_000
DEFAULT_BUCKET = os.environ.get("PROJECT_BUCKET_NAME", "ac215-project-data")
# PARQUET_SOURCE_PREFIX is also what parquet_to_chromadb.py reads *from* -- reusing
# it here as the upload destination keeps the two pipeline stages pointed at the
# same GCS folder without needing to pass --gcs-prefix by hand.
DEFAULT_GCS_PREFIX = os.environ.get("PARQUET_SOURCE_PREFIX")
DEFAULT_GCS_PREFIX_TEMPLATE = "pubmed/filtered_{from_date}_{to_date}"


def load_parsed_pickles(data_dir: Path) -> pd.DataFrame:
    """Load and concatenate every ``*.pkl`` DataFrame written by parse_pm_ftp.py."""
    pkl_files = sorted(data_dir.glob("*.pkl"))
    if not pkl_files:
        raise FileNotFoundError(f"No .pkl files found in {data_dir}")

    frames = []
    for pkl_file in pkl_files:
        df = pd.read_pickle(pkl_file)
        LOGGER.info("Loaded %s rows from %s", len(df), pkl_file.name)
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    LOGGER.info("Combined total: %s rows across %s files", len(combined), len(pkl_files))
    return combined


def dedupe_by_pmid(df: pd.DataFrame) -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset="pmid", keep="last")
    dropped = before - len(df)
    if dropped:
        LOGGER.info("Dropped %s duplicate pmid rows (kept last occurrence)", dropped)
    return df


def clean_abstract_and_date(df: pd.DataFrame) -> pd.DataFrame:
    """Remove rows without an abstract or without a parsable publication_date."""
    before = len(df)

    df = df.copy()
    df["abstract"] = df["abstract"].replace("", pd.NA)
    df = df[df["abstract"].notna()]
    LOGGER.info("Dropped %s rows with missing/empty abstract", before - len(df))

    before_date = len(df)
    df["publication_date"] = pd.to_datetime(df["publication_date"], errors="coerce")
    df = df[df["publication_date"].notna()]
    LOGGER.info("Dropped %s rows with unparsable/missing publication_date", before_date - len(df))

    return df


def filter_date_range(df: pd.DataFrame, from_date: pd.Timestamp, to_date: pd.Timestamp) -> pd.DataFrame:
    before = len(df)
    df = df[(df["publication_date"] >= from_date) & (df["publication_date"] <= to_date)]
    LOGGER.info(
        "Kept %s / %s rows within date range [%s, %s]",
        len(df),
        before,
        from_date.date(),
        to_date.date(),
    )
    return df


def add_recency_flags(df: pd.DataFrame, reference_date: pd.Timestamp) -> pd.DataFrame:
    df = df.copy()
    df["is_last_year"] = df["publication_date"] >= (reference_date - pd.DateOffset(years=1))
    df["is_last_5_years"] = df["publication_date"] >= (reference_date - pd.DateOffset(years=5))
    LOGGER.info(
        "is_last_year=True for %s rows; is_last_5_years=True for %s rows (reference date: %s)",
        int(df["is_last_year"].sum()),
        int(df["is_last_5_years"].sum()),
        reference_date.date(),
    )
    return df


def load_top_journals(top_journals_file: Path | None) -> set[str] | None:
    if top_journals_file is None:
        return None
    lines = top_journals_file.read_text(encoding="utf-8").splitlines()
    journals = {line.strip().lower() for line in lines if line.strip()}
    LOGGER.info("Loaded %s top-journal names from %s", len(journals), top_journals_file)
    return journals


def add_top_journal_flag(df: pd.DataFrame, top_journals: set[str] | None) -> pd.DataFrame:
    df = df.copy()
    if top_journals is None:
        LOGGER.warning(
            "No --top-journals-file supplied; setting is_top_journal=False for all rows. "
            "Pass --top-journals-file to compute this flag properly."
        )
        df["is_top_journal"] = False
        return df

    df["is_top_journal"] = df["journal_title"].fillna("").str.strip().str.lower().isin(top_journals)
    LOGGER.info("is_top_journal=True for %s / %s rows", int(df["is_top_journal"].sum()), len(df))
    return df


def add_pubmed_url(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pubmed_url"] = "https://pubmed.ncbi.nlm.nih.gov/" + df["pmid"].astype(str)
    return df


FINAL_COLUMN_ORDER = [
    "pmid",
    "title",
    "journal_title",
    "publication_date",
    "abstract",
    "author_list",
    "author_list_full",
    "coi_statement",
    "coi_flag",
    "pubmed_url",
    "is_last_year",
    "is_last_5_years",
    "is_top_journal",
]


def reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in FINAL_COLUMN_ORDER if c not in df.columns]
    if missing:
        raise ValueError(f"Output is missing expected columns: {missing}")
    return df[FINAL_COLUMN_ORDER]


def split_into_chunks(df: pd.DataFrame, rows_per_file: int | None) -> list[pd.DataFrame]:
    if not rows_per_file or len(df) <= rows_per_file:
        return [df]
    return [df.iloc[i : i + rows_per_file] for i in range(0, len(df), rows_per_file)]


def _next_start_index(existing_filenames: Iterable[str], stem: str) -> int:
    """Given existing `{stem}_NNNNN.parquet` basenames, return the next unused index (1 if none exist)."""
    pattern = re.compile(rf"^{re.escape(stem)}_(\d+)\.parquet$")
    max_index = 0
    for name in existing_filenames:
        match = pattern.match(name)
        if match:
            max_index = max(max_index, int(match.group(1)))
    return max_index + 1


def next_local_start_index(output_dir: Path, stem: str) -> int:
    if not output_dir.exists():
        return 1
    existing = [p.name for p in output_dir.glob(f"{stem}_*.parquet")]
    start_index = _next_start_index(existing, stem)
    if existing:
        LOGGER.info("Found %s existing local file(s); continuing numbering from index %s", len(existing), start_index)
    return start_index


def next_gcs_start_index(bucket_name: str, gcs_prefix: str, stem: str) -> int:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob_prefix = f"{gcs_prefix.rstrip('/')}/{stem}_"
    existing = [blob.name.rsplit("/", 1)[-1] for blob in bucket.list_blobs(prefix=blob_prefix)]
    start_index = _next_start_index(existing, stem)
    if existing:
        LOGGER.info(
            "Found %s existing file(s) under gs://%s/%s; continuing numbering from index %s",
            len(existing),
            bucket_name,
            gcs_prefix.rstrip("/"),
            start_index,
        )
    return start_index


def write_local(chunks: list[pd.DataFrame], output_dir: Path, stem: str, start_index: int = 1) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, chunk in enumerate(chunks, start=start_index):
        path = output_dir / f"{stem}_{idx:05d}.parquet"
        chunk.to_parquet(path, index=False)
        LOGGER.info("Wrote %s rows to %s", len(chunk), path)
        paths.append(path)
    return paths


def write_gcs(chunks: list[pd.DataFrame], bucket_name: str, gcs_prefix: str, stem: str, start_index: int = 1) -> list[str]:
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    uris = []
    for idx, chunk in enumerate(chunks, start=start_index):
        blob_name = f"{gcs_prefix.rstrip('/')}/{stem}_{idx:05d}.parquet"
        blob = bucket.blob(blob_name)
        blob.upload_from_string(chunk.to_parquet(index=False), content_type="application/octet-stream")
        uri = f"gs://{bucket_name}/{blob_name}"
        LOGGER.info("Uploaded %s rows to %s", len(chunk), uri)
        uris.append(uri)
    return uris


def parse_arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Directory of pickled DataFrames from parse_pm_ftp.py")
    parser.add_argument("--from", dest="from_date", required=True, help="Inclusive lower bound for publication_date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", required=True, help="Inclusive upper bound for publication_date (YYYY-MM-DD)")
    parser.add_argument(
        "--reference-date",
        default=None,
        help="Anchor date (YYYY-MM-DD) for is_last_year/is_last_5_years. Defaults to today (UTC).",
    )
    parser.add_argument(
        "--top-journals-file",
        type=Path,
        default=None,
        help="Text file, one journal title per line, used to compute is_top_journal. "
        "Without this, is_top_journal is False for every row.",
    )
    parser.add_argument("--rows-per-file", type=int, default=DEFAULT_ROWS_PER_FILE, help="Max rows per output Parquet file (0/negative = single file)")
    parser.add_argument("--output-stem", default="pubmed_data", help="Filename stem for output Parquet files")
    parser.add_argument("--local", type=Path, default=None, help="If set, write Parquet files here instead of uploading to GCS")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="GCS bucket name to upload to (ignored if --local is set)")
    parser.add_argument(
        "--gcs-prefix",
        default=None,
        help="GCS key prefix to upload under (ignored if --local is set). "
        "Defaults to the PARQUET_SOURCE_PREFIX env var, then pubmed/filtered_<from>_<to> "
        "if that isn't set either.",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:  # pragma: no cover - CLI wrapper
    args = parse_arguments(argv)

    if not args.data_dir.exists():
        raise FileNotFoundError(f"Data directory does not exist: {args.data_dir}")

    from_date = pd.Timestamp(args.from_date)
    to_date = pd.Timestamp(args.to_date)
    reference_date = pd.Timestamp(args.reference_date) if args.reference_date else pd.Timestamp.utcnow().normalize().tz_localize(None)

    df = load_parsed_pickles(args.data_dir)
    df = dedupe_by_pmid(df)
    df = clean_abstract_and_date(df)
    df = filter_date_range(df, from_date, to_date)
    df = add_recency_flags(df, reference_date)

    top_journals = load_top_journals(args.top_journals_file)
    df = add_top_journal_flag(df, top_journals)
    df = add_pubmed_url(df)
    df = reorder_columns(df)

    if df.empty:
        LOGGER.warning("No rows remain after filtering; nothing to write.")
        return

    chunks = split_into_chunks(df, args.rows_per_file)

    if args.local:
        start_index = next_local_start_index(args.local, args.output_stem)
        write_local(chunks, args.local, args.output_stem, start_index=start_index)
    else:
        gcs_prefix = (
            args.gcs_prefix
            or DEFAULT_GCS_PREFIX
            or DEFAULT_GCS_PREFIX_TEMPLATE.format(from_date=from_date.date(), to_date=to_date.date())
        )
        start_index = next_gcs_start_index(args.bucket, gcs_prefix, args.output_stem)
        write_gcs(chunks, args.bucket, gcs_prefix, args.output_stem, start_index=start_index)

    LOGGER.info("Done. Final row count: %s", len(df))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
