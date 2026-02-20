import json
import os
from typing import Generator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .prompt import SYSTEM_PROMPT
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from google import genai
from google.auth import exceptions as google_auth_exceptions

from .rag_module import build_context_and_citations

# GCP_PROJECT = os.environ["GCP_PROJECT"]
GCP_PROJECT = os.environ.get("GCP_PROJECT", "local-test-project")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    "projects/650165561090/locations/us-central1/endpoints/6751272973916700672",
)
GEMINI_MODEL = "gemini-2.5-flash"  # for testing

ROOT_PATH = os.environ.get("ROOT_PATH", "")

API_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "API_ALLOW_ORIGINS", "http://localhost:8080,http://0.0.0.0:8080,http://127.0.0.1:8080"
    ).split(",")
    if origin.strip()
]

llm_client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)

app = FastAPI(
    title="The Consult · Gemini Proxy",
    description="Thin API proxy that calls Vertex AI Gemini directly (no RAG yet).",
    root_path=ROOT_PATH.rstrip("/"),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=API_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EvidenceFilters(BaseModel):
    article_types: list[str] = Field(default_factory=list, alias="articleTypes")
    article_impact: list[str] = Field(default_factory=list, alias="articleImpact")
    publication_date: str | None = Field(default=None, alias="publicationDate")
    coi_disclosure: str | None = Field(default=None, alias="coiDisclosure")

    model_config = {"populate_by_name": True}


class AskRequest(BaseModel):
    question: str
    mode: Literal["clinical", "research"] = "clinical"
    patient_context: str | None = None
    filters: EvidenceFilters | None = None


class Citation(BaseModel):
    id: str | None = None
    pmid: str | None = None
    title: str | None = None
    authors: list[str] | str | None = None
    journal: str | None = None
    publication_date: str | None = None
    pubmed_url: str | None = None
    snippet: str | None = None
    coi_flag: str | None = None
    is_last_year: str | None = None
    is_last_5_years: str | None = None
    is_top_journal: str | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation] = []


def _build_prompt(payload: AskRequest, context_block: str = "") -> str:
    """Construct the Gemini prompt from the incoming request."""
    patient_block = ""
    if payload.patient_context:
        patient_block = f"\nAdditional context: {payload.patient_context}"

    filter_block = ""
    if payload.filters:
        filters = payload.filters
        filter_parts: list[str] = []
        if filters.article_types:
            filter_parts.append(f"Article types: {', '.join(filters.article_types)}")
        if filters.article_impact:
            filter_parts.append(f"Impact filters: {', '.join(filters.article_impact)}")
        if filters.publication_date:
            filter_parts.append(f"Publication date: {filters.publication_date}")
        if filters.coi_disclosure:
            filter_parts.append(f"COI: {filters.coi_disclosure}")

        if filter_parts:
            filter_block = "\nUser-selected filters: " + "; ".join(filter_parts)

    retrieved_block = ""
    if context_block:
        retrieved_block = (
            "\n\nUse the retrieved studies below as evidence. Cite using [number] and note uncertainty if weak.\n"
            f"{context_block}"
        )

    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"Mode: {payload.mode.capitalize()}\n"
        f"User question: {payload.question.strip()}\n"
        f"{patient_block}"
        f"{filter_block}\n"
        f"{retrieved_block}\n"
        "Respond in the requested tone. Cite real guidelines or trials only if you are sure."
    )


def _stream_gemini(prompt: str, citations: list[Citation] | None = None) -> Generator[str, None, None]:
    """Yield Server-Sent Events with Gemini deltas."""
    if citations:
        payload = json.dumps({"citations": [c.model_dump() for c in citations]})
        yield f"event: citations\ndata: {payload}\n\n"

    try:
        stream = llm_client.models.generate_content_stream(
            model=GEMINI_MODEL,
            contents=prompt,
            config={"temperature": 0.4},
        )
    except AttributeError as exc:
        raise HTTPException(
            status_code=501, detail="Gemini streaming is not supported with the current client."
        ) from exc

    try:
        for chunk in stream:
            text = getattr(chunk, "text", None)
            if not text:
                continue
            payload = json.dumps({"delta": text})
            yield f"data: {payload}\n\n"
    except Exception as exc:
        error_payload = json.dumps({"error": str(exc)})
        yield f"event: error\ndata: {error_payload}\n\n"
        return

    yield 'event: end\ndata: {"status": "completed"}\n\n'


# Routes
@app.get("/")
async def get_index():  # optional async, at start of line
    return {"message": "Welcome to the Consult Medical App!"}


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


@app.post("/api/ask", response_model=AskResponse)
def ask_vertex(payload: AskRequest) -> AskResponse:
    """Return a plain Gemini answer (no RAG yet)."""
    filters = payload.filters.model_dump(by_alias=True) if payload.filters else None
    try:
        context_block, citations_raw = build_context_and_citations(payload.question, filters)
    except google_auth_exceptions.DefaultCredentialsError as exc:
        raise HTTPException(
            status_code=500,
            detail="GC cred not found. Ensure ADC configured (GA_CREDENTIALS or workload identity).",
        ) from exc
    citations = [Citation(**c) for c in citations_raw]
    prompt = _build_prompt(payload, context_block=context_block)

    try:
        response = llm_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={"temperature": 0.4},
        )
    except Exception as exc:  # pragma: no cover - surfaced via HTTP error for debugging
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    answer = response.text or "Gemini returned an empty response."
    return AskResponse(answer=answer, citations=citations)


@app.post("/api/ask/stream")
def ask_vertex_stream(payload: AskRequest):
    """Stream Gemini deltas via Server-Sent Events."""
    filters = payload.filters.model_dump(by_alias=True) if payload.filters else None
    context_block, citations_raw = build_context_and_citations(payload.question, filters)

    # try:
    #     context_block, citations_raw = build_context_and_citations(payload.question, filters)
    # except google_auth_exceptions.DefaultCredentialsError as exc:
    #     raise HTTPException(
    #         status_code=500, detail="Google Cloud credentials not found.
    #         Ensure ADC is configured (GA_CREDENTIALS, or workload identity).",
    #     ) from exc
    citations = [Citation(**c) for c in citations_raw]
    prompt = _build_prompt(payload, context_block=context_block)
    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(
        _stream_gemini(prompt, citations=citations),
        media_type="text/event-stream",
        headers=headers,
    )
