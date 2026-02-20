import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Import the FastAPI app
import sys, os
sys.path.append(os.path.abspath("src/llm-api"))

from llm_api.api.server import app

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_rag_components(): # can add test_ to name
    """
    Patch the entire RAG pipeline:
    - generate_query_embedding
    - get_chromadb_collection
    - llm_client inside rag_module
    - llm_client inside server
    """

    with patch("llm_api.api.rag_module.generate_query_embedding") as mock_embed, \
         patch("llm_api.api.rag_module.get_chromadb_collection") as mock_chroma, \
         patch("llm_api.api.rag_module.llm_client") as mock_llm_rag, \
         patch("llm_api.api.server.llm_client") as mock_llm_server:

        # Mock embedding
        mock_embed.return_value = [0.1, 0.2, 0.3]

        # Mock Chroma collection & results
        collection = MagicMock()
        collection.query.return_value = {
            "documents": [["Some snippet"]],
            "metadatas": [[{
                "title": "Mock Study",
                "journal_title": "Mock Journal",
                "publication_date": "2020-01-01",
                "pubmed_url": "http://example.com",
                "pmid": "12345",
                "coi_flag": "0",
                "is_last_year": "False",
                "is_last_5_years": "True",
                "is_top_journal": "False",
            }]],
            "ids": [["doc1"]],
            "distances": [[0.5]],
        }
        mock_chroma.return_value = collection

        # Mock LLM for non-streaming endpoint
        response_mock = MagicMock()
        response_mock.text = "Mock Gemini Answer"
        mock_llm_server.models.generate_content.return_value = response_mock

        # Mock LLM streaming generator
        mock_delta1 = MagicMock()
        mock_delta1.text = "Hello "
        mock_delta2 = MagicMock()
        mock_delta2.text = "world!"

        mock_llm_server.models.generate_content_stream.return_value = iter([mock_delta1, mock_delta2])

        yield {
            "embed": mock_embed,
            "chroma": mock_chroma,
            "llm_rag": mock_llm_rag,
            "llm_server": mock_llm_server,
        }


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers(client):
    response = client.options("/api/ask")
    assert "access-control-allow-origin" in response.headers


def test_api_ask_end_to_end(client, mock_rag_components):
    payload = {
        "question": "What is the effect of X?",
        "mode": "clinical",
        "patient_context": "Patient has Y.",
        "filters": {
            "articleTypes": [],
            "articleImpact": ["Top Journal"],
            "publicationDate": "Within last 5 years",
            "coiDisclosure": "Without Disclosures"
        }
    }

    response = client.post("/api/ask", json=payload)
    assert response.status_code == 200

    body = response.json()

    # Validate answer
    assert "answer" in body
    assert body["answer"] == "Mock Gemini Answer"

    # Validate citations
    assert "citations" in body
    assert len(body["citations"]) == 1
    first = body["citations"][0]
    assert first["title"] == "Mock Study"
    assert first["journal"] == "Mock Journal"
    assert first["pmid"] == "12345"


def test_api_stream_sse(client, mock_rag_components):
    payload = {"question": "Test streaming?"}

    response = client.post("/api/ask/stream", json=payload)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"

    chunks = response.text.split("\n\n")

    # First event = citations SSE
    assert "event: citations" in chunks[0]
    assert "Mock Study" in chunks[0]

    # Delata events
    delta_events = [c for c in chunks if "delta" in c]
    assert any("Hello" in c for c in delta_events)
    assert any("world!" in c for c in delta_events)

    # Last event = end
    assert any("event: end" in c for c in chunks)
