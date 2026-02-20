"""
Unit tests for Utilities functions
"""

import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from models.src.chunker import chunk_abstracts
from models.src.embedder import _get_client, embed_texts, embed_chunk_lists
from models.src.gcs import read_parquet_from_gcs


# ----------------------------------------------------------------------
# GCS tests
# ----------------------------------------------------------------------


@patch("models.src.gcs.storage.Client")  # Correct patch target
def test_get_parquet(MockClient):
    mock_instance = MockClient.return_value
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = b"PAR1..."  # minimal parquet header
    mock_instance.bucket.return_value.blob.return_value = mock_blob

    # Should not raise
    try:
        df = read_parquet_from_gcs("bucket", "file.parquet")
        assert isinstance(df, pd.DataFrame)
    except Exception:
        pytest.fail("read_parquet_from_gcs unexpectedly raised error.")


# ----------------------------------------------------------------------
# Chunker tests
# ----------------------------------------------------------------------


class TestUtils:

    def test_chunk_abstracts(self):
        df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "abstract": [
                    "Short.",
                    "Long. " * 50,
                    "",
                ],
            }
        )

        out = chunk_abstracts(df, chunk_size=50, chunk_overlap=5, parallel=False)
        assert "abstract_chunks" in out.columns
        assert len(out) == 3
        assert all(isinstance(x, list) for x in out["abstract_chunks"])

    def test_chunk_abstracts_parallel(self):
        df = pd.DataFrame(
            {
                "id": [1, 2],
                "abstract": [
                    "Long. " * 50,
                    "More text. " * 60,
                ],
            }
        )

        out = chunk_abstracts(df, chunk_size=50, chunk_overlap=5, parallel=True)
        assert "abstract_chunks" in out.columns
        assert len(out) == 2

    def test_chunk_abstracts_empty(self):
        df = pd.DataFrame({"id": [1], "abstract": [""]})
        out = chunk_abstracts(df, chunk_size=50, chunk_overlap=5, parallel=False)
        assert out["abstract_chunks"].iloc[0] == []

    def test_chunk_abstracts_no_abstract_column(self):
        df = pd.DataFrame({"id": [1, 2], "title": ["t1", "t2"]})
        with pytest.raises(KeyError):
            chunk_abstracts(df, chunk_size=50, chunk_overlap=5, parallel=False)


# ----------------------------------------------------------------------
# Embedder tests
# ----------------------------------------------------------------------


@patch("models.src.embedder.genai.Client")  # avoid real GCP call
def test_embedder_get_client(MockGenAI):
    mock_client = MockGenAI.return_value
    client = _get_client()
    assert client is mock_client


@patch("models.src.embedder._get_client")
def test_embed_texts(MockGetClient):
    class FakeEmbedding:
        def __init__(self, values):
            self.values = values

    # Create fake client
    fake_client = MagicMock()
    fake_client.models.embed_content.return_value.embeddings = [
        FakeEmbedding([0.1] * 256),
        FakeEmbedding([0.2] * 256),
        FakeEmbedding([0.3] * 256),
    ]

    MockGetClient.return_value = fake_client

    texts = ["A", "B", "", "  ", "C"]
    embs = embed_texts(texts, batch_size=2)

    assert len(embs) == 3


def test_embed_texts_empty():
    assert embed_texts(["", " ", None]) == []


@patch("models.src.embedder._get_client")
def test_embed_chunk_lists(MockGetClient):
    class FakeEmbedding:
        def __init__(self, values):
            self.values = values

    fake_client = MagicMock()
    fake_client.models.embed_content.return_value.embeddings = [
        FakeEmbedding([0.5] * 256),
        FakeEmbedding([0.6] * 256),
        FakeEmbedding([0.7] * 256),
        FakeEmbedding([0.8] * 256),
        FakeEmbedding([0.9] * 256),
        FakeEmbedding([1.0] * 256),
    ]
    MockGetClient.return_value = fake_client
    chunk_lists = [
        ["a1", "a2"],
        ["b1"],
        [],
        ["c1", "c2", "c3"],
    ]
    result = embed_chunk_lists(chunk_lists, batch_size=3)
    assert len(result) == 4
