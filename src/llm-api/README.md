# LLM API Service

Thin FastAPI proxy that fronts Vertex AI Gemini for _The Consult_ UI. It exposes the
same `/api/ask` and `/api/ask/stream` endpoints used in development but is packaged
as an independent service for Cloud Run deployments.

## Local development

```bash
cd src/llm-api
uv sync
uv run uvicorn api.server:app --reload --host 0.0.0.0 --port 8081
```

Set the required environment variables before starting:

- `GCP_PROJECT` – Google Cloud project that has Vertex AI + Gemini access.
- `GCP_LOCATION` – Vertex AI region (defaults to `us-central1`).
- `GEMINI_MODEL` – Gemini model name (defaults to `gemini-2.5-flash`).
- `API_ALLOW_ORIGINS` – Comma-separated list of allowed CORS origins.

## Container build

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/$PROJECT_ID/consult/llm-api:latest \
  src/llm-api
```

Deploy the resulting image to Cloud Run while wiring the same environment variables.

