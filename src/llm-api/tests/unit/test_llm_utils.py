import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def server_module(monkeypatch):
    # Stub google auth/genai dependencies so the API can import without cloud credentials.
    auth_exceptions = ModuleType("google.auth.exceptions")

    class DummyCredError(Exception):
        pass

    auth_exceptions.DefaultCredentialsError = DummyCredError

    # Reuse existing google package (protobuf relies on it) and attach stubs without clobbering.
    try:
        google_pkg = importlib.import_module("google")
    except ModuleNotFoundError:
        google_pkg = ModuleType("google")
        google_pkg.__path__ = []  # mark as package for submodules
        sys.modules["google"] = google_pkg

    auth_module = sys.modules.get("google.auth") or ModuleType("google.auth")
    auth_module.exceptions = auth_exceptions
    google_pkg.auth = auth_module
    sys.modules["google.auth"] = auth_module
    sys.modules["google.auth.exceptions"] = auth_exceptions

    genai_types = ModuleType("google.genai.types")

    class DummyEmbedConfig:
        def __init__(self, *_, **__):
            pass

    genai_types.EmbedContentConfig = DummyEmbedConfig

    class FakeModels:
        def __init__(self, parent):
            self.parent = parent

        def generate_content(self, model, contents, config):
            self.parent.generate_calls.append({"model": model, "contents": contents, "config": config})
            return SimpleNamespace(text="stub response")

        def generate_content_stream(self, model, contents, config):
            self.parent.stream_calls.append({"model": model, "contents": contents, "config": config})
            for piece in ["alpha", "beta"]:
                yield SimpleNamespace(text=piece)

        def embed_content(self, model, contents, config):
            self.parent.embed_calls.append({"model": model, "contents": contents, "config": config})
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.models = FakeModels(self)
            self.generate_calls: list[dict] = []
            self.stream_calls: list[dict] = []
            self.embed_calls: list[dict] = []

    genai_module = ModuleType("google.genai")
    genai_module.Client = FakeClient
    genai_module.types = genai_types
    google_pkg.genai = genai_module
    sys.modules["google.genai"] = genai_module
    sys.modules["google.genai.types"] = genai_types

    monkeypatch.setenv("GCP_PROJECT", "test-project")
    monkeypatch.setenv("GCP_LOCATION", "test-location")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
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
    monkeypatch.setattr(
        server, "build_context_and_citations", lambda _q, _f=None: ("[1] Title: trial", dummy_citations)
    )

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


def test_ask_returns_answer_and_citations(client, server_module):
    payload = {"question": "What is hypertension?", "mode": "clinical"}

    response = client.post("/api/ask", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "stub response"
    assert body["citations"][0]["title"] == "trial"

    call = server_module.llm_client.generate_calls[0]
    # assert call["model"] == "test-model"
    assert "hypertension" in call["contents"]


def test_stream_endpoint_emits_citations_and_deltas(client, server_module):
    payload = {"question": "Use streaming", "mode": "research"}

    with client.stream("POST", "/api/ask/stream", json=payload) as response:
        lines = [line if isinstance(line, str) else line.decode() for line in response.iter_lines() if line]

    body = "\n".join(lines)
    assert response.status_code == 200
    assert "event: citations" in body
    assert '"delta": "alpha"' in body
    assert '"delta": "beta"' in body
    assert '"status": "completed"' in body
    assert server_module.llm_client.stream_calls, "Streaming model should be invoked once"


def test_build_prompt_includes_context_and_filters(server_module):
    filters = server_module.EvidenceFilters(
        articleTypes=["Review"],
        articleImpact=["Top Journal"],
        publicationDate="Within last year",
        coiDisclosure="With Disclosures",
    )
    payload = server_module.AskRequest(
        question="Explain findings",
        mode="research",
        patient_context="65yo with HTN",
        filters=filters,
    )

    prompt = server_module._build_prompt(payload, context_block="[1] title\nSnippet")

    assert "Explain findings" in prompt
    assert "65yo with HTN" in prompt
    assert "Article types: Review" in prompt
    assert "Impact filters: Top Journal" in prompt
    assert "Publication date: Within last year" in prompt
    assert "COI: With Disclosures" in prompt
    assert "Use the retrieved studies below as evidence" in prompt
    assert "[1] title" in prompt
    assert "Respond in the requested tone." in prompt


def test_generate_query_embedding(rag_module):
    query = "test query"
    embedding = rag_module.generate_query_embedding(query)

    assert embedding == [0.1, 0.2]
    call = rag_module.llm_client.embed_calls[0]
    assert call["model"] == rag_module.EMBEDDING_MODEL
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
