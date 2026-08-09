# The Consult

Generative AI assistant that delivers referenced, clinically aware answers for clinicians
and researchers. It pairs OpenAI (via LangChain) with RAG over PubMed-derived content, so
answers carry citations, study details, and configurable evidence filters.

## Demo

<video src="https://github.com/user-attachments/assets/2ef237b4-b233-47af-8b5b-3c323535b66e" controls width="600"></video>

## What's inside

| Path | What it is |
|---|---|
| `src/llm-api` | FastAPI service: RAG over ChromaDB, OpenAI generation, streaming, multi-turn sessions |
| `src/frontend` | Vite/React client — renders answers, citations, and filters |
| `src/models` | RAG tooling: chunking, embeddings, ChromaDB ingestion, query CLI |
| `src/datapipeline` | PubMed ingestion: NCBI FTP download → parse → filter → Parquet |
| `src/deployment` | Pulumi programs + Dockerfiles for deploying to GCP (images + GKE) |
| `src/workflow` | ML workflow CLI driving Vertex AI pipelines |
| `tests/` | Integration and system tests (unit tests live in each module) |
| `.github/workflows` | CI/CD and ML pipelines |
| `docs`, `screenshots` | Design docs, workflow/coverage screenshots |

## Run locally

Requires **Python 3.12**, **Node 18+**, **Docker**, and an **OpenAI API key**.

Three processes make up the running app: ChromaDB (vector store, port 8000), the
API (port 8081), and the frontend (port 8080). Run steps 4–6 in separate terminals.

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install uv && uv sync

cd src/frontend && npm install && cd ../..
```

### 2. Create a `.env` in the repo root

`.env` is gitignored, so a fresh clone won't have one:

```bash
cat > .env <<'EOF'
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.4-nano
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536
CHROMADB_HOST=localhost
CHROMADB_PORT=8000
API_ALLOW_ORIGINS=http://localhost:8080
EOF
```

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | **Required.** LLM calls and embeddings |
| `OPENAI_MODEL` | Chat model (default `gpt-5.4-nano`) |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | Default `text-embedding-3-small` / `1536` |
| `CHROMADB_HOST` / `CHROMADB_PORT` / `CHROMADB_TOP_K` | Vector store connection |
| `API_ALLOW_ORIGINS` | Comma-separated CORS origins |
| `GOOGLE_APPLICATION_CREDENTIALS` | Only needed to ingest Parquet from GCS (steps in `src/models`) |
| `GCP_PROJECT` | Same — unrelated to the LLM path |

> The API loads this file automatically on startup. Variables already set in the
> environment take precedence, so Docker/Kubernetes config always wins over `.env`.

### 3. Start ChromaDB

The compose file attaches to an external Docker network, so create it first:

```bash
docker network create llm-rag-network

cd src/models
docker compose up -d chromadb
cd ../..
```

Data persists in `src/models/docker-volumes/chromadb`, so it survives restarts.

### 4. Load data into ChromaDB

A fresh ChromaDB is empty — the app will run but return no citations. To populate it,
follow [`src/models/README.md`](src/models/README.md). The Parquet files it ingests are
produced by the pipeline in [`src/datapipeline/README.md`](src/datapipeline/README.md).

### 5. Start the API

```bash
uvicorn api.server:app --app-dir src/llm-api --host 0.0.0.0 --port 8081 --reload
```

Check it: `curl http://localhost:8081/healthz`

### 6. Start the frontend

```bash
cd src/frontend && npm run dev -- --host --port 8080
```

Open **`http://localhost:8080`**. The UI reads its API URL from
`src/frontend/.env` (`VITE_API_BASE_URL=http://localhost:8081`), which is
committed, so no extra config is needed.

### Alternative: containerized

Each module ships a `./docker-shell.sh` that builds and drops you into a container
with its dependencies — available for `src/llm-api`, `src/frontend`, `src/models`,
and `src/datapipeline`.

## Testing

```bash
pytest src/llm-api/tests        # unit
pytest src/models/tests         # unit
pytest tests/integration        # integration
pytest tests/system             # system (needs the API running)

cd src/datapipeline && uv run pytest tests/   # unit, run from its own venv
```

## Deploy to GCP

📖 **See [`src/deployment/README.md`](src/deployment/README.md)** for the complete
walkthrough: creating the GCP project, enabling APIs, service-account roles, running
both Pulumi stacks, cost estimates, and teardown.

⚠️ Deploying provisions a GKE cluster that bills hourly (roughly $150–220/month if
left running). The guide covers costs and how to tear everything down.

## CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| `ci-cd-main.yml` | Push/PR to `main` or `develop` | Lint (Black + Flake8), unit, integration, and system tests |
| `app-ci-cd-gcp.yml` | `/deploy-app` in commit message | Builds images, runs Pulumi, deploys to GKE |
| `ml-ci-cd-gcp.yml` | `/run-*` in commit message | Submits Vertex AI pipeline jobs |

`ml-ci-cd-gcp.yml` sub-triggers: `/run-data-collector`, `/run-data-processor`,
`/run-ml-pipeline`.

The deployment and ML workflows need these configured as GitHub secrets or shell env:
`GCP_PROJECT`, `GCS_SERVICE_ACCOUNT`, `PULUMI_BUCKET`, and `GOOGLE_APPLICATION_CREDENTIALS`
(path to that service account's JSON key).

## Issues and limitations

- Study-type and evidence-quality filters were scoped but not built.
- Fine-tuning for clinical/research tone remains a work in progress.
- User modes are limited to research vs. clinical; more specific personas are future work.

### Testing gaps

- `src/datapipeline` unit tests cover only the filter/flag transforms in
  `upload_pm_abstract_ftp.py` — the FTP download and XML parse steps are untested, and no
  CI workflow runs this suite yet.
- Coverage reports (see `screenshots/`) show weak spots: `get_chromadb_collection()`,
  `query_documents()`, `get_index()`, `health_check()`, and `build_context_and_citations()`
  in `src/llm-api`; `query_rag_model.py` and `src/gcs.py` in `src/models`.
- GitHub artifact storage limits have kept the HTML coverage reports from being regenerated
  recently.
