import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


@pytest.fixture()
def server_module(monkeypatch):
    # Stub langchain_openai so the API can import without a real OPENAI_API_KEY.
    class FakeChatOpenAI:
        def __init__(self, *_args, **_kwargs):
            self.generate_calls: list[dict] = []
            self.stream_calls: list[dict] = []

        def invoke(self, messages):
            self.generate_calls.append({"messages": messages})
            return SimpleNamespace(content="stub response")

        def stream(self, messages):
            self.stream_calls.append({"messages": messages})
            for piece in ["alpha", "beta"]:
                yield SimpleNamespace(content=piece)

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

    dummy_citations = [
        {
            "id": "1",
            "pmid": "123",
            "title": "trial",
            "journal": "JAMA",
            "publication_date": "2024",
            "pubmed_url": "http://example.com",
            "snippet": "evidence",
            "coi_flag": "0",
            "is_last_year": "True",
            "is_last_5_years": "True",
            "is_top_journal": "True",
        }
    ]
    rag_calls: list[dict] = []

    def fake_build_context_and_citations(question, filters=None):
        rag_calls.append({"question": question, "filters": filters})
        return "[1] Title: trial", dummy_citations

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


def test_ask_returns_answer_citations_and_conversation_id(client, server_module):
    payload = {"question": "What is hypertension?", "mode": "clinical"}

    response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "stub response"
    assert body["citations"][0]["title"] == "trial"
    assert body["conversation_id"]

    call = server_module.llm_client.generate_calls[0]
    messages = call["messages"]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert "hypertension" in messages[1].content
    assert len(server_module.rag_calls) == 1


def test_ask_follow_up_reuses_conversation_without_rerunning_rag(client, server_module):
    first = client.post("/api/ask", json={"question": "What is hypertension?", "mode": "clinical"})
    conversation_id = first.json()["conversation_id"]
    assert len(server_module.rag_calls) == 1

    second = client.post(
        "/api/ask",
        json={"question": "What about in pregnancy?", "mode": "clinical", "conversation_id": conversation_id},
    )

    assert second.status_code == 200
    body = second.json()
    assert body["conversation_id"] == conversation_id
    # RAG must not run again on the follow-up.
    assert len(server_module.rag_calls) == 1
    # Citations carry over unchanged from the first turn.
    assert body["citations"] == first.json()["citations"]

    second_call_messages = server_module.llm_client.generate_calls[-1]["messages"]
    # system + first human + first AI + new human
    assert len(second_call_messages) == 4
    assert isinstance(second_call_messages[0], SystemMessage)
    assert isinstance(second_call_messages[2], AIMessage)
    assert second_call_messages[2].content == "stub response"
    assert second_call_messages[-1].content == "What about in pregnancy?"


def test_ask_unknown_conversation_id_starts_a_new_conversation(client, server_module):
    response = client.post(
        "/api/ask", json={"question": "Fresh question", "mode": "clinical", "conversation_id": "does-not-exist"}
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] != "does-not-exist"
    assert len(server_module.rag_calls) == 1


def test_stream_endpoint_emits_meta_and_deltas(client, server_module):
    payload = {"question": "Use streaming", "mode": "research"}

    with client.stream("POST", "/api/ask/stream", json=payload) as response:
        lines = [line if isinstance(line, str) else line.decode() for line in response.iter_lines() if line]

    body = "\n".join(lines)
    assert response.status_code == 200
    assert "event: meta" in body
    assert '"delta": "alpha"' in body
    assert '"delta": "beta"' in body
    assert '"status": "completed"' in body
    assert server_module.llm_client.stream_calls, "Streaming model should be invoked once"


def test_stream_follow_up_reuses_conversation(client, server_module):
    with client.stream("POST", "/api/ask/stream", json={"question": "First", "mode": "clinical"}) as response:
        lines = [line if isinstance(line, str) else line.decode() for line in response.iter_lines() if line]
    import json as _json

    meta_line = next(line for line in lines if line.startswith("data:") and "conversation_id" in line)
    conversation_id = _json.loads(meta_line[len("data:") :].strip())["conversation_id"]
    assert len(server_module.rag_calls) == 1

    with client.stream(
        "POST",
        "/api/ask/stream",
        json={"question": "Follow up", "mode": "clinical", "conversation_id": conversation_id},
    ) as response2:
        lines2 = [line if isinstance(line, str) else line.decode() for line in response2.iter_lines() if line]

    meta_line2 = next(line for line in lines2 if line.startswith("data:") and "conversation_id" in line)
    body2 = _json.loads(meta_line2[len("data:") :].strip())
    assert body2["conversation_id"] == conversation_id
    assert len(server_module.rag_calls) == 1  # still just the one RAG call from the first turn


def test_build_first_turn_message_includes_context_and_filters_but_not_system_prompt(server_module):
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

    message = server_module._build_first_turn_message(payload, context_block="[1] title\nSnippet")

    assert "Explain findings" in message
    assert "65yo with HTN" in message
    assert "Article types: Review" in message
    assert "Impact filters: Top Journal" in message
    assert "Publication date: Within last year" in message
    assert "COI: With Disclosures" in message
    assert "Keyword: ventilation" in message
    assert "Use the retrieved studies below as evidence" in message
    assert "[1] title" in message
    assert "Respond in the requested tone." in message
    # The system prompt now travels as its own SystemMessage, not inline here.
    assert "You are a medical and biomedical Q&A assistant" not in message


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
