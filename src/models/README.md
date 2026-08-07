# Models

RAG tooling: chunking corpora, generating embeddings, loading them into ChromaDB, and
running ad-hoc queries. The FastAPI LLM service lives in `src/llm-api` — this module is
data prep only.

| Script | Purpose |
|---|---|
| `parquet_to_chromadb.py` | Parquet (GCS) → chunk → embed → ChromaDB. Checkpointed and resumable. |
| `jsonl_to_chromadb.py` | Load a previously embedded `.jsonl` backup into ChromaDB |
| `query_rag_model.py` | Ad-hoc RAG query CLI with metadata filters |
| `semantic_splitter.py` | Experimental similarity-based text splitter |

## Setup

```bash
cd src/models
uv sync
```

Configuration is read from the repo-root `.env`, loaded automatically by
`models/__init__.py` — including `GOOGLE_APPLICATION_CREDENTIALS`, so no manual
`export` is needed. Variables already set in the environment take precedence, so
Docker and Kubernetes config always wins over the file.

These scripts use relative imports, so run them as modules with `src/` on the path — not
as bare scripts:

```bash
PYTHONPATH=.. uv run python -m models.parquet_to_chromadb
```

## Ingesting Parquet into ChromaDB

`PARQUET_SOURCE_PREFIX` (the GCS folder) comes from `.env`. To ingest a single file
instead of the whole folder, set `PARQUET_FILENAME` — either in `.env` or inline:

```bash
PARQUET_FILENAME=pubmed_data_00003.parquet \
  PYTHONPATH=.. uv run python -m models.parquet_to_chromadb
```

Without `PARQUET_FILENAME`, every Parquet file under the prefix is read and concatenated.
Uploads use `upsert`, so reruns are idempotent, and embedding/upload progress is
checkpointed — a crash mid-run resumes instead of restarting.

| Variable | Default |
|---|---|
| `PROJECT_BUCKET_NAME` | `ac215-project-data` |
| `PARQUET_SOURCE_PREFIX` | `pubmed/filtered_oct23/2020-01-01_2025-12-31` |
| `PARQUET_FILENAME` | unset (whole folder) |
| `CHROMADB_HOST` / `CHROMADB_PORT` | `35.193.38.202` / `8000` |
| `CHROMADB_BATCH_SIZE` | `50` |
| `ENABLE_GCS_BACKUP` | `true` — writes a `.jsonl` backup of all records to GCS |

## Querying

```bash
PYTHONPATH=.. uv run python -m models.query_rag_model --help
```

Supports metadata filters: `--journal_title`, `--coi_flag`, `--is_last_year`,
`--is_last_5_years`, `--is_top_journal`.

## Provisioning a ChromaDB VM (Terraform)

```bash
cd trf
# set your variables in chroma.tfvars first
terraform init
terraform plan -var-file chroma.tfvars
terraform apply -var-file chroma.tfvars
```

## Tests

```bash
uv run pytest tests/unit
```
