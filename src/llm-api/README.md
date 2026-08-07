# LLM API Service

FastAPI service backing _The Consult_: RAG retrieval over ChromaDB plus OpenAI generation
through LangChain, packaged as an independent deployment unit.

**Endpoints:** `GET /`, `GET /healthz`, `POST /api/ask`, `POST /api/ask/stream`

## Local development

```bash
cd src/llm-api
uv sync
uv run uvicorn api.server:app --reload --host 0.0.0.0 --port 8081
```

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | **Required.** Chat completions and embeddings |
| `OPENAI_MODEL` | Chat model (default `gpt-5.4-nano`) |
| `EMBEDDING_MODEL` | Embedding model (default `text-embedding-3-small`) |
| `API_ALLOW_ORIGINS` | Comma-separated CORS origins |

These are read from a `.env` at the repo root, loaded automatically by
`api/__init__.py` on import. Variables already present in the environment take
precedence, so Docker and Kubernetes config always wins over the file.

## Tracing

Set `BRAINTRUST_API_KEY` (optionally `BRAINTRUST_PROJECT`, default `The Consult`) to send
traces to Braintrust. Each `/api/ask` call becomes one trace with nested spans for
retrieval (`build_context_and_citations`) and generation. Without the key, tracing is a
no-op — no crash, no retry noise.

## Evals

`evals/eval_rag.py` runs the real retrieve-then-generate pipeline against a question set
and scores it with `autoevals`' RAGAS scorers.

```bash
cd src/llm-api
uv run --env-file ../../.env braintrust eval evals/eval_rag.py
```

Requires both `OPENAI_API_KEY` and `BRAINTRUST_API_KEY` — the scorers themselves call an
LLM through Braintrust's proxy, so the key is needed for scoring to run at all, not just
for uploading results. Each run costs real OpenAI calls: one generation per question, plus
one grading call per question per scorer.

> `ContextPrecision`, `ContextRecall`, and `AnswerCorrectness` need a ground-truth
> `expected` answer per question. `DATASET` currently sets these to `None`, which those
> scorers skip cleanly. Fill them in with clinician-reviewed answers before trusting the
> scores — don't treat AI-generated "expected" answers as ground truth for a medical eval.

## Container build

```bash
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/$PROJECT_ID/consult/llm-api:latest \
  src/llm-api
```

Deploy to Cloud Run with the same environment variables wired in.
