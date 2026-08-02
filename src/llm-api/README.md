# LLM API Service

Thin FastAPI proxy that fronts OpenAI (via LangChain) for _The Consult_ UI. It exposes the
same `/api/ask` and `/api/ask/stream` endpoints used in development but is packaged
as an independent service for Cloud Run deployments.

## Local development

```bash
cd src/llm-api
uv sync
uv run uvicorn api.server:app --reload --host 0.0.0.0 --port 8081
```

Set the required environment variables before starting:

- `OPENAI_API_KEY` – OpenAI API key used for both chat completions and embeddings.
- `OPENAI_MODEL` – Chat model name (defaults to `gpt-5.4-nano`).
- `EMBEDDING_MODEL` – Embedding model name (defaults to `text-embedding-3-small`).
- `API_ALLOW_ORIGINS` – Comma-separated list of allowed CORS origins.

## Container build

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/$PROJECT_ID/consult/llm-api:latest \
  src/llm-api
```

Deploy the resulting image to Cloud Run while wiring the same environment variables.

