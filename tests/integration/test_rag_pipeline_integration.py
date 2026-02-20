import pytest
import chromadb
from unittest.mock import patch, MagicMock

# Import the target function
import src.models.query_rag_model as rag


@pytest.fixture
def temp_chroma(tmp_path):
    """Create a temporary local ChromaDB instance."""
    # Use persistent client to store test data
    client = chromadb.PersistentClient(path=str(tmp_path))

    # Create test collection
    collection = client.create_collection(name="test_collection")

    # Insert fake documents
    collection.add(
        ids=["doc1", "doc2"],
        documents=[
            "Cancer is caused by genetic mutations.",
            "Smoking increases cancer risk significantly.",
        ],
        metadatas=[
            {
                "title": "Cancer Research A",
                "journal_title": "Medical Journal",
                "publication_date": "2020-01-01",
                "author_list_full": "Alice, Bob",     # FIXED
                "pubmed_url": "http://example.com/1",
            },
            {
                "title": "Cancer Research B",
                "journal_title": "Health Science",
                "publication_date": "2021-06-15",
                "author_list_full": "Carol",          # FIXED
                "pubmed_url": "http://example.com/2",
            },
        ],
        embeddings=[[0.1] * 256, [0.2] * 256],  # dummy embeddings
    )

    return client


@pytest.fixture
def mock_chroma_httpclient(temp_chroma):
    """
    Patch chromadb.HttpClient so the RAG pipeline connects to our local test instance
    instead of the real chromadb server.
    """
    with patch("src.models.query_rag_model.chromadb.HttpClient") as mock_client:
        mock_client.return_value = temp_chroma
        yield


@pytest.fixture
def mock_vertex_embedding():
    """Mock the embedding function used in the RAG pipeline."""
    with patch("src.models.query_rag_model.generate_query_embedding") as mock_embed:
        mock_embed.return_value = [0.1] * 256
        yield


@pytest.fixture
def mock_llm():
    """Mock the LLM response."""
    fake_response = MagicMock()
    fake_response.text = "MOCK_LLM_RESPONSE"

    with patch("src.models.query_rag_model.llm_client") as mock_client:
        mock_client.models.generate_content.return_value = fake_response
        yield


@pytest.fixture
def mock_input():
    """Mock input() so chat() doesn't block."""
    with patch("builtins.input", return_value="What causes cancer?"):
        yield


def test_rag_pipeline_end_to_end(
    mock_chroma_httpclient,
    mock_vertex_embedding,
    mock_llm,
    mock_input,
):
    """
    End-to-end integration test for query-rag-model.chat().
    Verifies:
      - embedding is generated
      - documents are retrieved from chroma
      - LLM is called with constructed prompt
      - RAG pipeline runs without error
    """

    # Run the chat function with our test collection
    rag.chat(collection_name="test_collection", filter_dict={})

    # If no exceptions occur, we consider this integration test successful.
    # You can assert deeper details if desired:
    rag.llm_client.models.generate_content.assert_called_once()

    args, kwargs = rag.llm_client.models.generate_content.call_args
    prompt = kwargs["contents"]

    assert "What causes cancer?" in prompt
    assert "Cancer is caused by genetic mutations." in prompt
    assert "Smoking increases cancer risk significantly." in prompt

    rag.generate_query_embedding.assert_called_once()

    #collection = rag.chromadb.HttpClient.return_value.get_collection.return_value
    #collection.query.assert_called_once()
    # Because our patched HttpClient returns the real temp_chroma instance
    client = rag.chromadb.HttpClient.return_value
    collection = client.get_collection("test_collection")
    assert collection.count() == 2  # Ensures collection contains documents
