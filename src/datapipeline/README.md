# Data Pipeline

Downloads the PubMed baseline XML dump from NCBI, parses it into structured
records, filters/flags them, and publishes Parquet (locally or to GCS) for
downstream RAG ingestion (`src/models/parquet_to_chromadb.py`).

## Setup

```bash
uv venv && source .venv/bin/activate && uv sync
```

- **Step 1** (FTP download) needs no credentials.
- **Step 4** (GCS upload) needs `GOOGLE_APPLICATION_CREDENTIALS` exported to
  a service-account key (`.env` already points it at
  `secrets/consult-app-local.json`) — `gcloud auth list` alone isn't enough,
  since the Python client reads the env var, not your CLI session.

## Run the whole pipeline

```bash
cd src/datapipeline
uv venv && source .venv/bin/activate && uv sync

# 1. Download baseline files (add --start-index/--limit to bound a test run, e.g. --start-index 1270 --limit 3)
uv run get_pm_ftp.py

# 2. Extract
uv run extract_pm_ftp.py --xml-dir outputs/pubmed_baseline_ftp

# 3. Parse
uv run parse_pm_ftp.py --xml-dir outputs/pubmed_baseline_ftp_extract --min-index 400

# 4. Filter, flag, and publish
export GOOGLE_APPLICATION_CREDENTIALS=../../secrets/consult-app-local.json
uv run upload_pm_abstract_ftp.py \
    --data-dir outputs/pubmed_baseline_ftp_parsed \
    --from 2020-01-01 --to 2025-12-31 \
    --top-journals-file data/top_journals.txt \
    --local outputs/final_dataset   # omit --local to upload to GCS instead
```

## Pipeline stages

Run in order; each reads the previous stage's output. `--help` on any
script lists its full flags.

| # | Script | Reads | Writes |
|---|--------|-------|--------|
| 1 | [`get_pm_ftp.py`](get_pm_ftp.py) | NCBI FTP | `outputs/pubmed_baseline_ftp/*.xml.gz` |
| 2 | [`extract_pm_ftp.py`](extract_pm_ftp.py) | `*.xml.gz` | `outputs/pubmed_baseline_ftp_extract/*.xml` |
| 3 | [`parse_pm_ftp.py`](parse_pm_ftp.py) | `*.xml` | `outputs/pubmed_baseline_ftp_parsed/*.pkl` |
| 4 | [`upload_pm_abstract_ftp.py`](upload_pm_abstract_ftp.py) | `*.pkl` | `outputs/final_dataset/*.parquet` or GCS |

**1. `get_pm_ftp.py`** — NCBI regenerates the whole baseline dump under a new
year prefix every December (`pubmed25n1274.xml.gz` → `pubmed26n0001.xml.gz`),
so this script discovers real filenames from the server rather than guessing
them. `--start-index N` filters by index, `--limit N` caps how many files.
MD5-verifies each download; skips files already present with a matching
checksum.

**2. `extract_pm_ftp.py --xml-dir <dir>`** — gunzips to `<dir>_extract`.

**3. `parse_pm_ftp.py --xml-dir <dir> --min-index 400`** — parses XML into
one pickled DataFrame per file (`pmid, title, journal_title,
publication_date, abstract, author_list, author_list_full, coi_statement,
coi_flag, pubmed_url`). `--min-index` skips already-processed index ranges;
`--force` reprocesses; `--max-workers` sets parallelism.

**4. `upload_pm_abstract_ftp.py`**:
```bash
uv run upload_pm_abstract_ftp.py \
    --data-dir outputs/pubmed_baseline_ftp_parsed \
    --from 2020-01-01 --to 2025-12-31 \
    --top-journals-file data/top_journals.txt \
    --local outputs/final_dataset   # omit to upload to GCS
```
Drops rows with no abstract or unparsable date, filters to `[--from, --to]`,
adds `is_last_year`/`is_last_5_years` (relative to `--reference-date`,
default today) and `is_top_journal` (exact match against
`--top-journals-file`, one journal per line — **omit it and every row gets
`is_top_journal=False`**), then writes Parquet chunked at
`--rows-per-file` rows (default 300K).

GCS destination = `--gcs-prefix` → `PARQUET_SOURCE_PREFIX` env var →
`pubmed/filtered_<from>_<to>`. **It continues numbering from whatever
already exists at that destination** rather than overwriting — a rerun
against a live prefix lands new files there (e.g. `_00003.parquet`), it
won't clobber `_00001`. Still: point test runs at `--local` or a scratch
`--gcs-prefix`, not a real ingestion folder.

## Docker

```bash
docker build -t pubmed-pipeline .
docker run -v /path/to/service-account.json:/app/service-account.json pubmed-pipeline
```
Runs all 4 steps via [`docker-shell.sh`](docker-shell.sh). Default run
downloads the **entire** current baseline (1,300+ files) — for a bounded
run, invoke the scripts individually instead. Env vars: `SAVE_LOCAL`,
`FROM_DATE` (default `2020-01-01`), `TO_DATE` (default `2025-12-31`),
`REFERENCE_DATE`, `TOP_JOURNALS_FILE` (must also be volume-mounted in),
`KEEP_RUNNING`.

## Data files

- `data/top_journals.txt` — journal allow-list for step 4 (see caveat above).

## Tests

```bash
uv run pytest tests/
```
Covers the pure functions in `upload_pm_abstract_ftp.py`. Run from inside
`src/datapipeline/` — not picked up by the repo's root-level `pytest`,
which is scoped to the top-level `tests/` dir.
