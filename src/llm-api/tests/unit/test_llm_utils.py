import importlib
import json as _json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import braintrust
import braintrust.integrations.langchain as braintrust_langchain
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def _stub_langchain_openai():
    langchain_openai_module = ModuleType("langchain_openai")
    langchain_openai_module.ChatOpenAI = lambda *_a, **_k: SimpleNamespace(
        invoke=lambda *_a, **_k: SimpleNamespace(content="stub", tool_calls=[]),
        stream=lambda *_a, **_k: iter([]),
        bind_tools=lambda *_a, **_k: None,
    )
    langchain_openai_module.OpenAIEmbeddings = lambda *_a, **_k: SimpleNamespace(
        embed_query=lambda *_a, **_k: [0.1, 0.2]
    )
    sys.modules["langchain_openai"] = langchain_openai_module


def _tool_call_response(query: str, call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "search_pubmed", "args": {"query": query}, "id": call_id, "type": "tool_call"}],
    )


def _final_response(text: str) -> AIMessage:
    return AIMessage(content=text)


@pytest.fixture()
def server_module(monkeypatch):
    # Stub langchain_openai so the API can import without a real OPENAI_API_KEY.
    class FakeChatOpenAI:
        def __init__(self, *_args, **_kwargs):
            self.generate_calls: list[dict] = []
            self.stream_calls: list[dict] = []
            self.responses: list[SimpleNamespace] = []  # queued responses, consumed in order

        def bind_tools(self, tools):
            self.bound_tools = tools
            return self

        def _next_response(self) -> SimpleNamespace:
            if self.responses:
                return self.responses.pop(0)
            return _final_response("stub response")

        def invoke(self, messages):
            self.generate_calls.append({"messages": messages})
            return self._next_response()

        def stream(self, messages):
            self.stream_calls.append({"messages": messages})
            yield self._next_response()

    class FakeOpenAIEmbeddings:
        def __init__(self, *_args, **_kwargs):
            self.embed_calls: list[dict] = []

        def embed_query(self, text):
            self.embed_calls.append({"contents": text})
            return [0.1, 0.2]

    langchain_openai_module = ModuleType("langchain_openai")
    langchain_openai_module.ChatOpenAI = FakeChatOpenAI
    langchain_openai_module.OpenAIEmbeddings = FakeOpenAIEmbeddings
    sys.modules["langchain_openai"] = langchain_openai_module

    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("API_ALLOW_ORIGINS", "http://localhost:8080,http://0.0.0.0:8080")

    package_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(package_root))

    server = importlib.import_module("api.server")
    server = importlib.reload(server)

    rag_calls: list[dict] = []

    def fake_build_context_and_citations(question, filters=None):
        rag_calls.append({"question": question, "filters": filters})
        idx = len(rag_calls)
        citation = {
            "id": str(idx),
            "pmid": f"pmid-{idx}",
            "title": f"trial-{idx}",
            "journal": "JAMA",
            "publication_date": "2024",
            "pubmed_url": "http://example.com",
            "snippet": "evidence",
            "coi_flag": "0",
            "is_last_year": "True",
            "is_last_5_years": "True",
            "is_top_journal": "True",
        }
        return f"[1] Title: trial-{idx}", [citation]

    monkeypatch.setattr(server, "build_context_and_citations", fake_build_context_and_citations)
    server.rag_calls = rag_calls  # expose the counter to tests

    return server


@pytest.fixture()
def client(server_module):
    return TestClient(server_module.app)


@pytest.fixture()
def rag_module(server_module, monkeypatch):
    # Ensure chromadb is stubbed before import.
    chromadb_stub = ModuleType("chromadb")
    chromadb_stub.query_calls: list[dict] = []
    chromadb_stub.query_payload = {
        "documents": [["doc1"]],
        "metadatas": [[{"is_top_journal": "True", "coi_flag": "1", "is_last_year": "True"}]],
        "ids": [["id1"]],
        "distances": [[0.1]],
    }

    class DummyCollection:
        def query(self, **kwargs):
            chromadb_stub.query_calls.append(kwargs)
            return chromadb_stub.query_payload

    class DummyClient:
        def __init__(self, *_, **__):
            self.collections = []

        def get_collection(self, name):
            self.collections.append(name)
            return DummyCollection()

    chromadb_stub.HttpClient = DummyClient
    sys.modules["chromadb"] = chromadb_stub

    module = importlib.import_module("api.rag_module")
    module = importlib.reload(module)
    return module


def test_ask_calls_search_tool_and_returns_answer_citations_and_conversation_id(client, server_module):
    llm = server_module.get_llm_client()
    llm.responses = [_tool_call_response("hypertension"), _final_response("Here is the answer [1].")]

    response = client.post("/api/ask", json={"question": "What is hypertension?", "mode": "clinical"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Here is the answer [1]."
    assert body["citations"][0]["title"] == "trial-1"
    assert body["conversation_id"]
    assert len(server_module.rag_calls) == 1

    first_call_messages = llm.generate_calls[0]["messages"]
    assert isinstance(first_call_messages[0], SystemMessage)
    assert isinstance(first_call_messages[1], HumanMessage)
    assert "hypertension" in first_call_messages[1].content
    assert len(first_call_messages) == 2  # no pre-fetched context stuffed in anymore


def test_ask_follow_up_without_new_search_reuses_previous_citations(client, server_module):
    llm = server_module.get_llm_client()
    llm.responses = [_tool_call_response("hypertension"), _final_response("First answer [1].")]
    first = client.post("/api/ask", json={"question": "What is hypertension?", "mode": "clinical"})
    conversation_id = first.json()["conversation_id"]
    assert len(server_module.rag_calls) == 1

    llm.responses = [_final_response("Second answer, no new search needed.")]
    second = client.post(
        "/api/ask",
        json={"question": "Can you clarify that?", "mode": "clinical", "conversation_id": conversation_id},
    )

    assert second.status_code == 200
    body = second.json()
    assert body["conversation_id"] == conversation_id
    assert len(server_module.rag_calls) == 1  # no new search happened
    assert body["citations"] == first.json()["citations"]

    second_call_messages = llm.generate_calls[-1]["messages"]
    # system, human(q1), ai(tool_call), tool(result), ai(first answer), human(q2)
    assert len(second_call_messages) == 6
    assert isinstance(second_call_messages[0], SystemMessage)
    assert isinstance(second_call_messages[2], AIMessage)
    assert isinstance(second_call_messages[3], ToolMessage)
    assert second_call_messages[4].content == "First answer [1]."
    assert second_call_messages[-1].content == "Can you clarify that?"


def test_ask_follow_up_with_new_search_gets_fresh_citations(client, server_module):
    llm = server_module.get_llm_client()
    llm.responses = [_tool_call_response("hypertension"), _final_response("First answer [1].")]
    first = client.post("/api/ask", json={"question": "What is hypertension?", "mode": "clinical"})
    conversation_id = first.json()["conversation_id"]

    llm.responses = [_tool_call_response("hypertension in pregnancy"), _final_response("Second answer [1].")]
    second = client.post(
        "/api/ask",
        json={"question": "What about in pregnancy?", "mode": "clinical", "conversation_id": conversation_id},
    )

    assert second.status_code == 200
    body = second.json()
    assert len(server_module.rag_calls) == 2  # a fresh search happened this turn
    assert body["citations"] != first.json()["citations"]
    assert body["citations"][0]["title"] == "trial-2"


def test_ask_unknown_conversation_id_starts_a_new_conversation(client, server_module):
    llm = server_module.get_llm_client()
    llm.responses = [_tool_call_response("fresh question"), _final_response("An answer.")]

    response = client.post(
        "/api/ask", json={"question": "Fresh question", "mode": "clinical", "conversation_id": "does-not-exist"}
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] != "does-not-exist"
    assert len(server_module.rag_calls) == 1


def test_ask_tool_loop_respects_max_iterations_safety_cap(client, server_module):
    server_module.TOOL_MAX_ITERATIONS = 2
    llm = server_module.get_llm_client()
    llm.responses = [
        _tool_call_response("q1", call_id="call_1"),
        _tool_call_response("q2", call_id="call_2"),
        _final_response("Forced final answer."),
    ]

    response = client.post("/api/ask", json={"question": "Keep searching forever?", "mode": "clinical"})

    assert response.status_code == 200
    assert response.json()["answer"] == "Forced final answer."
    assert len(server_module.rag_calls) == 2  # both tool-call rounds ran their search
    assert len(llm.generate_calls) == 3  # 2 loop iterations + 1 forced fallback call


def test_stream_calls_search_tool_and_emits_meta_and_deltas(client, server_module):
    llm = server_module.get_llm_client()
    llm.responses = [_tool_call_response("research streaming"), _final_response("Full streamed answer.")]

    with client.stream("POST", "/api/ask/stream", json={"question": "Use streaming", "mode": "research"}) as response:
        lines = [line if isinstance(line, str) else line.decode() for line in response.iter_lines() if line]

    body = "\n".join(lines)
    assert response.status_code == 200
    assert "event: meta" in body
    assert '"delta": "Full streamed answer."' in body
    assert '"status": "completed"' in body
    assert len(server_module.rag_calls) == 1
    assert len(llm.stream_calls) == 2  # tool-call round + final content round


def test_stream_follow_up_reuses_conversation_and_citations(client, server_module):
    llm = server_module.get_llm_client()
    llm.responses = [_tool_call_response("first"), _final_response("First streamed answer.")]
    with client.stream("POST", "/api/ask/stream", json={"question": "First", "mode": "clinical"}) as response:
        lines = [line if isinstance(line, str) else line.decode() for line in response.iter_lines() if line]

    meta_line = next(line for line in lines if line.startswith("data:") and "conversation_id" in line)
    first_meta = _json.loads(meta_line[len("data:") :].strip())
    conversation_id = first_meta["conversation_id"]
    assert len(server_module.rag_calls) == 1

    llm.responses = [_final_response("Follow-up answer, no new search.")]
    with client.stream(
        "POST",
        "/api/ask/stream",
        json={"question": "Follow up", "mode": "clinical", "conversation_id": conversation_id},
    ) as response2:
        lines2 = [line if isinstance(line, str) else line.decode() for line in response2.iter_lines() if line]

    meta_line2 = next(line for line in lines2 if line.startswith("data:") and "conversation_id" in line)
    second_meta = _json.loads(meta_line2[len("data:") :].strip())
    assert second_meta["conversation_id"] == conversation_id
    assert second_meta["citations"] == first_meta["citations"]
    assert len(server_module.rag_calls) == 1  # no new search on the follow-up


def test_execute_search_numbers_citations_starting_from_turn_offset(server_module):
    turn_citations: list = []
    text_first = server_module._execute_search("query one", None, turn_citations)
    assert text_first.startswith("[1] Title: trial-1")
    assert len(turn_citations) == 1

    text_second = server_module._execute_search("query two", None, turn_citations)
    assert text_second.startswith("[2] Title: trial-2")
    assert len(turn_citations) == 2


def test_execute_search_skips_papers_already_retrieved_earlier_this_turn(server_module, monkeypatch):
    # Two different queries that happen to surface an overlapping paper (same pmid), plus one
    # genuinely new one on the second call -- the overlap shouldn't be cited twice.
    overlapping_citation = {
        "id": "1",
        "pmid": "dup-1",
        "title": "Shared Paper",
        "journal": "JAMA",
        "publication_date": "2024",
        "pubmed_url": "http://example.com/1",
        "snippet": "evidence",
    }
    new_citation = {
        "id": "2",
        "pmid": "dup-2",
        "title": "New Paper",
        "journal": "JAMA",
        "publication_date": "2024",
        "pubmed_url": "http://example.com/2",
        "snippet": "more evidence",
    }
    responses = [
        ("[1] Title: Shared Paper", [overlapping_citation]),
        ("[1] Title: Shared Paper\n\n[2] Title: New Paper", [overlapping_citation, new_citation]),
    ]
    monkeypatch.setattr(server_module, "build_context_and_citations", lambda *_a, **_k: responses.pop(0))

    turn_citations: list = []
    server_module._execute_search("query one", None, turn_citations)
    assert len(turn_citations) == 1

    text_second = server_module._execute_search("query two", None, turn_citations)
    assert "New Paper" in text_second
    assert "Shared Paper" not in text_second  # already retrieved this turn -- not re-cited
    assert text_second.startswith("[2]")  # continues numbering, doesn't restart
    assert len(turn_citations) == 2
    assert [c.title for c in turn_citations] == ["Shared Paper", "New Paper"]


def test_build_first_turn_message_includes_mode_and_filters_but_not_system_prompt_or_evidence(server_module):
    filters = server_module.EvidenceFilters(
        articleTypes=["Review"],
        articleImpact=["Top Journal"],
        publicationDate="Within last year",
        coiDisclosure="With Disclosures",
        keyword="ventilation",
    )
    payload = server_module.AskRequest(
        question="Explain findings",
        mode="research",
        patient_context="65yo with HTN",
        filters=filters,
    )

    message = server_module._build_first_turn_message(payload)

    assert "Explain findings" in message
    assert "65yo with HTN" in message
    assert "Article types: Review" in message
    assert "Impact filters: Top Journal" in message
    assert "Publication date: Within last year" in message
    assert "COI: With Disclosures" in message
    assert "Keyword: ventilation" in message
    assert "Respond in the requested tone." in message
    # The system prompt travels as its own SystemMessage, not inline here.
    assert "You are a medical and biomedical Q&A assistant" not in message
    # No evidence is pre-fetched anymore -- the model retrieves it itself via the tool.
    assert "retrieved studies" not in message


def test_generate_query_embedding(rag_module):
    query = "test query"
    embedding = rag_module.generate_query_embedding(query)

    assert embedding == [0.1, 0.2]
    call = rag_module.embeddings_client.embed_calls[0]
    assert call["contents"] == query


def test_build_metadata_filter(rag_module):
    frontend_filters = {
        "articleImpact": ["Top Journal"],
        "publicationDate": "Within last year",
        "coiDisclosure": "With Disclosures",
    }
    metadata_filter = rag_module._build_metadata_filter(frontend_filters)
    assert metadata_filter == {"top_journal": True, "last_year": True, "coi_required": True}

    # Test with no filters
    assert rag_module._build_metadata_filter(None) == {}
    assert rag_module._build_metadata_filter({}) == {}


def test_query_documents_filters_locally(rag_module):
    rag_module.chromadb.query_payload = {
        "documents": [["doc_a", "doc_b", "doc_c", "doc_d"]],
        "metadatas": [
            [
                {"is_top_journal": "True", "coi_flag": "1", "is_last_year": "True"},
                {"is_top_journal": "False", "coi_flag": "0", "is_last_5_years": "True"},
                {"is_top_journal": "True", "coi_flag": "0", "is_last_5_years": "True"},
                {"coi_flag": "1", "is_last_5_years": "True"},
            ]
        ],
        "ids": [["a", "b", "c", "d"]],
        "distances": [[0.01, 0.02, 0.03, 0.04]],
    }

    results = rag_module.query_documents(
        embedded_query=[0.3, 0.4],
        frontend_filters={"articleImpact": ["Top Journal"], "coiDisclosure": "With Disclosures"},
        n_results=2,
    )

    assert [item["id"] for item in results] == ["a"]
    query_call = rag_module.chromadb.query_calls[-1]
    assert "where" not in query_call
    assert query_call["n_results"] == max(2, rag_module.CHROMADB_CANDIDATE_K)


def test_query_documents_caps_filtered_results(rag_module):
    rag_module.chromadb.query_payload = {
        "documents": [["d1", "d2", "d3", "d4", "d5", "d6"]],
        "metadatas": [[{"pmid": "1"}, {"pmid": "2"}, {"pmid": "3"}, {"pmid": "4"}, {"pmid": "5"}, {"pmid": "6"}]],
        "ids": [["id-1", "id-2", "id-3", "id-4", "id-5", "id-6"]],
        "distances": [[0.01, 0.02, 0.03, 0.04, 0.05, 0.06]],
    }

    results = rag_module.query_documents(embedded_query=[0.7], frontend_filters={}, n_results=10)

    assert len(results) == rag_module.CHROMADB_FILTERED_TOP_K == 5
    assert [item["id"] for item in results] == ["id-1", "id-2", "id-3", "id-4", "id-5"]
    query_call = rag_module.chromadb.query_calls[-1]
    assert query_call["n_results"] == max(rag_module.CHROMADB_FILTERED_TOP_K, rag_module.CHROMADB_CANDIDATE_K)


def test_query_documents_dedupes_by_pmid(rag_module):
    rag_module.chromadb.query_payload = {
        "documents": [["dup1", "dup2", "unique"]],
        "metadatas": [[{"pmid": "123"}, {"pmid": "123"}, {"pmid": "456"}]],
        "ids": [["a", "b", "c"]],
        "distances": [[0.01, 0.02, 0.03]],
    }

    results = rag_module.query_documents(embedded_query=[0.1], frontend_filters={}, n_results=5)

    assert [item["id"] for item in results] == ["a", "c"]
    assert len(results) == 2


def test_query_documents_applies_case_insensitive_keyword_filter(rag_module):
    rag_module.query_documents(embedded_query=[0.1], frontend_filters={"keyword": "Ventilation"}, n_results=5)

    query_call = rag_module.chromadb.query_calls[-1]
    assert query_call["where_document"] == {"$regex": "(?i)Ventilation"}


def test_query_documents_escapes_regex_special_characters_in_keyword(rag_module):
    rag_module.query_documents(embedded_query=[0.1], frontend_filters={"keyword": "COVID-19 (severe)"}, n_results=5)

    query_call = rag_module.chromadb.query_calls[-1]
    assert query_call["where_document"] == {"$regex": "(?i)COVID\\-19\\ \\(severe\\)"}


def test_query_documents_omits_where_document_without_keyword(rag_module):
    rag_module.query_documents(embedded_query=[0.1], frontend_filters={}, n_results=5)
    rag_module.query_documents(embedded_query=[0.1], frontend_filters={"keyword": "   "}, n_results=5)
    rag_module.query_documents(embedded_query=[0.1], frontend_filters=None, n_results=5)

    for query_call in rag_module.chromadb.query_calls:
        assert "where_document" not in query_call


def test_tracing_initialized_when_braintrust_api_key_set(monkeypatch):
    _stub_langchain_openai()
    init_logger_mock = MagicMock()
    set_global_handler_mock = MagicMock()
    monkeypatch.setattr(braintrust, "init_logger", init_logger_mock)
    monkeypatch.setattr(braintrust_langchain, "set_global_handler", set_global_handler_mock)
    monkeypatch.setenv("BRAINTRUST_API_KEY", "test-key")
    monkeypatch.setenv("BRAINTRUST_PROJECT", "Test Project")

    package_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(package_root))
    server = importlib.import_module("api.server")
    importlib.reload(server)

    init_logger_mock.assert_called_once_with(project="Test Project")
    set_global_handler_mock.assert_called_once()


def test_tracing_skipped_when_braintrust_api_key_unset(monkeypatch):
    _stub_langchain_openai()
    init_logger_mock = MagicMock()
    set_global_handler_mock = MagicMock()
    monkeypatch.setattr(braintrust, "init_logger", init_logger_mock)
    monkeypatch.setattr(braintrust_langchain, "set_global_handler", set_global_handler_mock)
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)

    package_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(package_root))
    server = importlib.import_module("api.server")
    importlib.reload(server)

    init_logger_mock.assert_not_called()
    set_global_handler_mock.assert_not_called()
