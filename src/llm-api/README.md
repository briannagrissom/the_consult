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

## Tracing

Set `BRAINTRUST_API_KEY` (and optionally `BRAINTRUST_PROJECT`, default `The Consult`) to
send request traces to Braintrust. Every `/api/ask` and `/api/ask/stream` call becomes one
trace with nested spans for retrieval (`build_context_and_citations`) and generation (the
LangChain LLM call, via `BraintrustCallbackHandler`). Without the key, tracing is a no-op —
no crash, no background retry noise.

## Evals

`evals/eval_rag.py` runs the real retrieve-then-generate pipeline against a small question
set and scores it with `autoevals`' RAGAS scorers (`Faithfulness`, `ContextRelevancy`,
`AnswerRelevancy`, `ContextPrecision`, `ContextRecall`, `AnswerCorrectness`). Run it with:

```bash
cd src/llm-api
uv run --env-file ../../.env braintrust eval evals/eval_rag.py
```

Requires `OPENAI_API_KEY` and `BRAINTRUST_API_KEY` — the scorers themselves call an LLM
through Braintrust's proxy to grade each answer, so the key is required for scoring to work
at all, not just for uploading results. Each run costs real OpenAI calls: one generation
call per question, plus one grading call per question per scorer.

`ContextPrecision`, `ContextRecall`, and `AnswerCorrectness` need a ground-truth `expected`
answer per question (`DATASET` in the eval file currently has all of them set to `None`,
which those three scorers skip cleanly). Fill those in with real, clinician-reviewed answers
before trusting their scores — don't treat AI-generated "expected" answers as ground truth
for a medical eval.

## Container build

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/$PROJECT_ID/consult/llm-api:latest \
  src/llm-api
```

Deploy the resulting image to Cloud Run while wiring the same environment variables.

