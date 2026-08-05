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

from braintrust import Span, init_logger, start_span
from braintrust.integrations.langchain import BraintrustCallbackHandler, set_global_handler
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
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

BRAINTRUST_API_KEY = os.environ.get("BRAINTRUST_API_KEY")
BRAINTRUST_PROJECT = os.environ.get("BRAINTRUST_PROJECT", "The Consult")

# Safety cap on the tool-calling loop, in case the model keeps requesting searches forever.
TOOL_MAX_ITERATIONS = int(os.environ.get("TOOL_MAX_ITERATIONS", "4"))

llm_client = None
session_store = SessionStore(ttl_seconds=SESSION_TTL_SECONDS)


def get_llm_client() -> ChatOpenAI:
    global llm_client
    if llm_client is None:
        llm_client = ChatOpenAI(model=OPENAI_MODEL, temperature=0.4)
    return llm_client


def _init_tracing() -> None:
    # start_span()/@traced are safe no-ops when init_logger() was never called, so tracing
    # is only wired up at all when a key is actually present -- avoids noisy background
    # flush/login retries in local dev where BRAINTRUST_API_KEY is unset.
    if not BRAINTRUST_API_KEY:
        return
    init_logger(project=BRAINTRUST_PROJECT)
    set_global_handler(BraintrustCallbackHandler())


_init_tracing()


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


@tool
def search_pubmed(query: str) -> str:
    """Search the PubMed abstract database for evidence relevant to a medical or clinical
    question. Returns numbered results (title, journal, date, URL, and a relevant snippet)
    to cite in your answer using their bracketed number, e.g. [1]."""
    context_block, _citations = build_context_and_citations(query, frontend_filters=None)
    return context_block or "No relevant PubMed results were found for this query."


def _format_citations_block(citations: list[dict], start: int = 1) -> str:
    """Render citation dicts as the same numbered '[n] Title: ...' text build_context_and_citations
    produces, but starting from an arbitrary index -- used so a turn's tool results stay numbered
    contiguously even across multiple search_pubmed calls in the same turn."""
    lines = []
    for offset, c in enumerate(citations):
        lines.append(
            f"[{start + offset}] Title: {c.get('title')}\n"
            f"Journal: {c.get('journal')}\n"
            f"Date: {c.get('publication_date')}\n"
            f"URL: {c.get('pubmed_url')}\n"
            f"Snippet: {c.get('snippet')}"
        )
    return "\n\n".join(lines)


def _execute_search(query: str, filters: dict | None, turn_citations: list[Citation]) -> str:
    """Runs one search_pubmed call for real, numbering its results to continue on from
    whatever this turn has already retrieved, and appends them to turn_citations in place.
    Skips papers already retrieved earlier in this same turn (the model may search more than
    once), since query_documents only dedupes within a single search, not across several."""
    _unused_context_block, citations_raw = build_context_and_citations(query, filters)

    seen_pmids = {c.pmid for c in turn_citations if c.pmid}
    new_citations = [c for c in citations_raw if c.get("pmid") not in seen_pmids]

    if not new_citations:
        message = "No relevant PubMed results were found for this query."
        if citations_raw:
            message = "No new results for this query -- all matches were already retrieved earlier this turn."
        return message

    start = len(turn_citations) + 1
    text = _format_citations_block(new_citations, start=start)
    turn_citations.extend(Citation(**c) for c in new_citations)
    return text


def _run_tool_calls(tool_calls: list[dict], filters: dict | None, turn_citations: list[Citation]) -> list[ToolMessage]:
    results = []
    for call in tool_calls:
        if call["name"] == "search_pubmed":
            content = _execute_search(call["args"].get("query", ""), filters, turn_citations)
        else:  # pragma: no cover - only one tool is bound today
            content = f"Unknown tool: {call['name']}"
        results.append(ToolMessage(content=content, tool_call_id=call["id"]))
    return results


def _build_first_turn_message(payload: AskRequest) -> str:
    """Construct the first human message of a new conversation: mode, patient context, and
    filters. No evidence is pre-fetched here anymore -- the model retrieves it itself via the
    search_pubmed tool, whenever it decides a question (first turn or follow-up) needs it."""
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
            filter_block = "\nUser-selected filters (apply these when you search): " + "; ".join(filter_parts)

    return (
        f"Mode: {payload.mode.capitalize()}\n"
        f"User question: {payload.question.strip()}\n"
        f"{patient_block}"
        f"{filter_block}\n"
        "Respond in the requested tone."
    )


def _resolve_messages(
    payload: AskRequest,
) -> tuple[list[BaseMessage], list[BaseMessage], list[Citation], str | None]:
    """Returns (baseline_messages, new_messages, fallback_citations, conversation_id).

    baseline_messages is what's already stored for this conversation (empty if new).
    new_messages is what this turn contributes so far (system+first message if new, else
    just the question) -- the tool-calling loop appends its own rounds onto this list as it
    runs, and the full thing gets persisted afterward. fallback_citations is what to report
    if the model doesn't call search_pubmed this turn. conversation_id is None if new."""
    session = session_store.get(payload.conversation_id) if payload.conversation_id else None

    if session is None:
        first_message = HumanMessage(_build_first_turn_message(payload))
        return [], [SystemMessage(SYSTEM_PROMPT), first_message], [], None

    new_message = HumanMessage(payload.question.strip())
    citations = [Citation(**c) for c in session.citations]
    return session.messages, [new_message], citations, payload.conversation_id


def _persist_turn(
    conversation_id: str | None,
    baseline_messages: list[BaseMessage],
    new_messages: list[BaseMessage],
    fallback_citations: list[Citation],
    turn_citations: list[Citation],
) -> tuple[str, list[Citation]]:
    """Stores this turn's message trajectory, and returns (conversation_id, citations to report)
    -- turn_citations if the model searched this turn, otherwise whatever was already known."""
    citations = turn_citations if turn_citations else fallback_citations
    stored_citations = [c.model_dump() for c in citations]

    if conversation_id is None:
        conversation_id = session_store.create(messages=baseline_messages + new_messages, citations=stored_citations)
    else:
        session_store.append_messages(conversation_id, new_messages, citations=stored_citations)

    return conversation_id, citations


# Routes
@app.get("/")
async def get_index():  # optional async, at start of line
    return {"message": "Welcome to the Consult Medical App!"}


@app.get("/healthz")
def health_check():
    return {"status": "ok"}


@app.post("/api/ask", response_model=AskResponse)
def ask_llm(payload: AskRequest) -> AskResponse:
    """Answer a question. The model decides, on any turn, whether it needs to call the
    search_pubmed tool -- possibly more than once -- before giving a final answer."""
    with start_span(name="ask", type="task", input=payload.question) as span:
        baseline_messages, new_messages, fallback_citations, conversation_id = _resolve_messages(payload)
        messages = baseline_messages + new_messages
        filters = payload.filters.model_dump(by_alias=True) if payload.filters else None
        turn_citations: list[Citation] = []
        llm = get_llm_client().bind_tools([search_pubmed])

        try:
            for _ in range(TOOL_MAX_ITERATIONS):
                response = llm.invoke(messages)
                messages = messages + [response]
                new_messages.append(response)
                if not response.tool_calls:
                    break
                tool_messages = _run_tool_calls(response.tool_calls, filters, turn_citations)
                messages = messages + tool_messages
                new_messages.extend(tool_messages)
            else:  # loop exhausted TOOL_MAX_ITERATIONS without a final answer
                response = get_llm_client().invoke(messages)
                new_messages.append(response)
        except Exception as exc:  # pragma: no cover - surfaced via HTTP error for debugging
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        answer = response.content or "The LLM returned an empty response."

        conversation_id, citations = _persist_turn(
            conversation_id, baseline_messages, new_messages, fallback_citations, turn_citations
        )
        span.log(output=answer, metadata={"conversation_id": conversation_id, "num_citations": len(citations)})

    return AskResponse(answer=answer, citations=citations, conversation_id=conversation_id)


def _stream_llm(
    messages: list[BaseMessage],
    baseline_messages: list[BaseMessage],
    new_messages: list[BaseMessage],
    filters: dict | None,
    fallback_citations: list[Citation],
    conversation_id: str | None,
    span: Span,
) -> Generator[str, None, None]:
    """Yield Server-Sent Events with LLM deltas, running the same tool-calling loop as
    ask_llm. Tool-call rounds produce no visible deltas -- only the final text round streams
    to the client. Owns closing `span`, left open by ask_llm_stream for this purpose."""
    span.set_current()
    llm = get_llm_client().bind_tools([search_pubmed])
    turn_citations: list[Citation] = []
    answer = ""

    try:
        for _ in range(TOOL_MAX_ITERATIONS):
            full = None
            for chunk in llm.stream(messages):
                if chunk.content:
                    answer += chunk.content
                    payload = json.dumps({"delta": chunk.content})
                    yield f"data: {payload}\n\n"
                full = chunk if full is None else full + chunk

            messages = messages + [full]
            new_messages.append(full)
            if not full.tool_calls:
                break
            tool_messages = _run_tool_calls(full.tool_calls, filters, turn_citations)
            messages = messages + tool_messages
            new_messages.extend(tool_messages)
        else:  # loop exhausted TOOL_MAX_ITERATIONS without a final answer
            response = get_llm_client().invoke(messages)
            new_messages.append(response)
            answer = response.content or answer
            if answer:
                yield f"data: {json.dumps({'delta': answer})}\n\n"
    except Exception as exc:
        span.log(metadata={"error": str(exc)})
        span.end()
        error_payload = json.dumps({"error": str(exc)})
        yield f"event: error\ndata: {error_payload}\n\n"
        return

    conversation_id, citations = _persist_turn(
        conversation_id, baseline_messages, new_messages, fallback_citations, turn_citations
    )

    span.log(output=answer, metadata={"conversation_id": conversation_id, "num_citations": len(citations)})
    span.end()

    meta_payload = json.dumps({"conversation_id": conversation_id, "citations": [c.model_dump() for c in citations]})
    yield f"event: meta\ndata: {meta_payload}\n\n"
    yield 'event: end\ndata: {"status": "completed"}\n\n'


@app.post("/api/ask/stream")
def ask_llm_stream(payload: AskRequest):
    """Stream LLM deltas via Server-Sent Events. Same tool-calling loop as /api/ask.

    The span for this request is created here (not as a `with` block) because it has to
    stay open across the route handler's return -- the actual LLM calls happen later,
    inside _stream_llm's generator body, once FastAPI starts iterating the response.
    """
    span = start_span(name="ask_stream", type="task", input=payload.question)
    baseline_messages, new_messages, fallback_citations, conversation_id = _resolve_messages(payload)
    messages = baseline_messages + new_messages
    filters = payload.filters.model_dump(by_alias=True) if payload.filters else None

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(
        _stream_llm(messages, baseline_messages, new_messages, filters, fallback_citations, conversation_id, span),
        media_type="text/event-stream",
        headers=headers,
    )
