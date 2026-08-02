import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Generator, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .prompt import SYSTEM_PROMPT
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .rag_module import build_context_and_citations
from .session_store import SessionStore

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-nano")

ROOT_PATH = os.environ.get("ROOT_PATH", "")

API_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "API_ALLOW_ORIGINS", "http://localhost:8080,http://0.0.0.0:8080,http://127.0.0.1:8080"
    ).split(",")
    if origin.strip()
]

SESSION_TTL_SECONDS = float(os.environ.get("SESSION_TTL_SECONDS", "1800"))
SESSION_CLEANUP_INTERVAL_SECONDS = float(os.environ.get("SESSION_CLEANUP_INTERVAL_SECONDS", "300"))

llm_client = None
session_store = SessionStore(ttl_seconds=SESSION_TTL_SECONDS)


def get_llm_client() -> ChatOpenAI:
    global llm_client
    if llm_client is None:
        llm_client = ChatOpenAI(model=OPENAI_MODEL, temperature=0.4)
    return llm_client


async def _session_cleanup_loop():
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
        session_store.sweep_expired()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    yield
    cleanup_task.cancel()


app = FastAPI(
    title="The Consult · LLM Proxy",
    description="Thin API proxy that calls OpenAI via LangChain, with RAG-backed conversations.",
    root_path=ROOT_PATH.rstrip("/"),
    lifespan=_lifespan,
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
    keyword: str | None = Field(default=None, alias="keyword")

    model_config = {"populate_by_name": True}


class AskRequest(BaseModel):
    question: str
    mode: Literal["clinical", "research"] = "clinical"
    patient_context: str | None = None
    filters: EvidenceFilters | None = None
    conversation_id: str | None = None


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
    conversation_id: str


def _build_first_turn_message(payload: AskRequest, context_block: str = "") -> str:
    """Construct the first human message of a new conversation: mode, patient context,
    filters, and RAG results. Follow-up turns skip all of this and reuse it from history."""
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
        if filters.keyword:
            filter_parts.append(f"Keyword: {filters.keyword}")

        if filter_parts:
            filter_block = "\nUser-selected filters: " + "; ".join(filter_parts)

    retrieved_block = ""
    if context_block:
        retrieved_block = (
            "\n\nUse the retrieved studies below as evidence. Cite using [number] and note uncertainty if weak.\n"
            f"{context_block}"
        )

    return (
        f"Mode: {payload.mode.capitalize()}\n"
        f"User question: {payload.question.strip()}\n"
        f"{patient_block}"
        f"{filter_block}\n"
        f"{retrieved_block}\n"
        "Respond in the requested tone. Cite real guidelines or trials only if you are sure."
    )


def _start_conversation(payload: AskRequest) -> tuple[list[BaseMessage], HumanMessage, list[Citation]]:
    """Run RAG for a brand-new conversation and build the opening message pair."""
    filters = payload.filters.model_dump(by_alias=True) if payload.filters else None
    context_block, citations_raw = build_context_and_citations(payload.question, filters)
    human_message = HumanMessage(_build_first_turn_message(payload, context_block=context_block))
    messages = [SystemMessage(SYSTEM_PROMPT), human_message]
    citations = [Citation(**c) for c in citations_raw]
    return messages, human_message, citations


def _resolve_turn(payload: AskRequest) -> tuple[list[BaseMessage], HumanMessage, list[Citation], str | None]:
    """Resolve a request into the message list to send the LLM, the new human message,
    the current citation list, and the existing conversation_id (None if this is new).
    A new/unknown/expired conversation_id runs RAG once; a live one skips RAG entirely
    and continues the stored message history."""
    session = session_store.get(payload.conversation_id) if payload.conversation_id else None

    if session is None:
        try:
            messages_for_call, human_message, citations = _start_conversation(payload)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="OpenAI credentials not found. Ensure OPENAI_API_KEY is set.",
            ) from exc
        return messages_for_call, human_message, citations, None

    human_message = HumanMessage(payload.question.strip())
    messages_for_call = session.messages + [human_message]
    citations = [Citation(**c) for c in session.citations]
    return messages_for_call, human_message, citations, payload.conversation_id


def _stream_llm(
    messages_for_call: list[BaseMessage],
    human_message: HumanMessage,
    conversation_id: str | None,
    citations: list[Citation],
) -> Generator[str, None, None]:
    """Yield Server-Sent Events with LLM deltas, then persist the turn and report the
    conversation_id once the full answer is known."""
    answer = ""
    try:
        for chunk in get_llm_client().stream(messages_for_call):
            text = chunk.content
            if not text:
                continue
            answer += text
            payload = json.dumps({"delta": text})
            yield f"data: {payload}\n\n"
    except Exception as exc:
        error_payload = json.dumps({"error": str(exc)})
        yield f"event: error\ndata: {error_payload}\n\n"
        return

    ai_message = AIMessage(answer)
    if conversation_id is None:
        conversation_id = session_store.create(
            messages=messages_for_call + [ai_message],
            citations=[c.model_dump() for c in citations],
        )
    else:
        session_store.append_turn(conversation_id, human_message, ai_message)

    meta_payload = json.dumps(
        {"conversation_id": conversation_id, "citations": [c.model_dump() for c in citations]}
    )
    yield f"event: meta\ndata: {meta_payload}\n\n"
    yield 'event: end\ndata: {"status": "completed"}\n\n'


# Routes
@app.get("/")
async def get_index():  # optional async, at start of line
    return {"message": "Welcome to the Consult Medical App!"}


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


@app.post("/api/ask", response_model=AskResponse)
def ask_llm(payload: AskRequest) -> AskResponse:
    """Answer a question. A new conversation runs RAG once; follow-ups (via conversation_id)
    reuse that retrieved context and simply continue the message history."""
    messages_for_call, human_message, citations, conversation_id = _resolve_turn(payload)

    try:
        response = get_llm_client().invoke(messages_for_call)
    except Exception as exc:  # pragma: no cover - surfaced via HTTP error for debugging
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    answer = response.content or "The LLM returned an empty response."
    ai_message = AIMessage(answer)

    if conversation_id is None:
        conversation_id = session_store.create(
            messages=messages_for_call + [ai_message],
            citations=[c.model_dump() for c in citations],
        )
    else:
        session_store.append_turn(conversation_id, human_message, ai_message)

    return AskResponse(answer=answer, citations=citations, conversation_id=conversation_id)


@app.post("/api/ask/stream")
def ask_llm_stream(payload: AskRequest):
    """Stream LLM deltas via Server-Sent Events. Same new-vs-continuing logic as /api/ask."""
    messages_for_call, human_message, citations, conversation_id = _resolve_turn(payload)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(
        _stream_llm(messages_for_call, human_message, conversation_id, citations),
        media_type="text/event-stream",
        headers=headers,
    )
