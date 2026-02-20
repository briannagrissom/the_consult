# Data Pipeline

## Overview

This repo contains code for automatic data curation and preprocessing.

## How to run
```
uv venv
source .venv/bin/activate
uv sync
```

## Processes
1. Retrieve data from reliable sources (Currently aiming for PubmedAPI)
2. Extract metadata information (author, citation, ...)
3. Preprocess data with LLM (extracting information about topic, study design, COI, etc.)
4. Make all of this into RAG friendly data
5. enjoy

## Downloading PMC text dumps

Use the dedicated CLI to mirror the open-access PMC text archives into `outputs/pubmed`:

```bash
uv run python pubmed_fetch.py
```

## Scripts

- `get_pm_ftp.py` - Downloads PubMed baseline XML files from NCBI FTP
- `extract_pm_ftp.py` - Extracts downloaded .gz files to XML
- `parse_pm_ftp.py` - Parses XML files into pickled pandas DataFrames

## Individual Script Usage

Run individual scripts with `uv run <script_name>`. Use `--help` for detailed options.

```bash
# Download baseline files (limit for testing)
uv run get_pm_ftp.py --max-workers 4 --limit 10

# Extract downloaded archives
uv run extract_pm_ftp.py --input-dir outputs/pubmed_baseline_ftp --max-workers 4

# Parse XML to pickles (files with index > 400)
uv run parse_pm_ftp.py --min-index 400 --max-workers 4
```

## Docker Pipeline

The Docker container automatically runs the complete 4-step pipeline: download → extract → parse → upload.

### Build and Basic Run

```bash
docker build -t pubmed-pipeline .
docker run pubmed-pipeline
```

### Authentication Options

**Option 1: Service Account File**
```bash
docker run -v /path/to/service-account.json:/app/service-account.json pubmed-pipeline
```

**Option 2: Environment Variable**
```bash
docker run -e GOOGLE_APPLICATION_CREDENTIALS=/path/to/creds.json pubmed-pipeline
```

**Option 3: Default GCP Authentication** (when running on GCP)
```bash
docker run pubmed-pipeline  # Uses instance service account
```

### Configuration Options

**Save Locally Instead of GCS Upload**
```bash
docker run -e SAVE_LOCAL=true pubmed-pipeline
```

**Custom Date Range Filter**
```bash
docker run -e FROM_DATE=2023-01-01 -e TO_DATE=2024-12-31 pubmed-pipeline
```

**Keep Container Running for Debugging**
```bash
docker run -e KEEP_RUNNING=true pubmed-pipeline
```

**Combined Example**
```bash
docker run \
  -v ./service-account.json:/app/service-account.json \
  -e SAVE_LOCAL=true \
  -e FROM_DATE=2023-01-01 \
  -e TO_DATE=2024-06-30 \
  -e KEEP_RUNNING=true \
  pubmed-pipeline
```

### Pipeline Steps

1. **Download** - Fetches PubMed baseline files from NCBI FTP (limited to 10 files for testing)
2. **Extract** - Unzips downloaded .gz archives to XML files
3. **Parse** - Converts XML files to pickled DataFrames (processes files with index > 400)
4. **Filter & Upload** - Filters articles by date range, removes entries without abstracts/dates, and uploads to GCS or saves locally

### Output Directories

- `outputs/pubmed_baseline_ftp/` - Downloaded .gz files
- `outputs/pubmed_baseline_ftp_extract/` - Extracted XML files
- `outputs/pubmed_baseline_ftp_parsed/` - Pickled DataFrames
- `outputs/final_dataset/` - Final filtered Parquet files (when using `SAVE_LOCAL=true`)

Pass `--help` to list options such as timeouts, custom output directories, or limiting the mirrored subfolders.