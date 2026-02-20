"""
Unit tests for jsonl_to_chromadb helpers.
"""

from __future__ import annotations

import importlib
from unittest import mock

import pytest

from models import jsonl_to_chromadb as jsonl_module


class TestJsonlToChromaDB:
    @staticmethod
    def _reload_module(monkeypatch: pytest.MonkeyPatch, **env_overrides):
        """Reload the module so that environment variables are re-evaluated."""
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(jsonl_module)

    def test_connect_to_chromadb_uses_env(self, monkeypatch: pytest.MonkeyPatch):
        module = self._reload_module(
            monkeypatch,
            CHROMADB_HOST="fake-host",
            CHROMADB_PORT="1337",
        )
        fake_client = mock.Mock()
        http_client = mock.Mock(return_value=fake_client)
        monkeypatch.setattr(module.chromadb, "HttpClient", http_client)

        result = module.connect_to_chromadb()

        http_client.assert_called_once_with(host="fake-host", port=1337)
        assert result is fake_client

    def test_load_backups_to_chromadb_batches_semantic(self, monkeypatch: pytest.MonkeyPatch):
        module = self._reload_module(monkeypatch)
        module.BACKUP_ENABLED = True
        module.BACKUP_BUCKET = "bucket"
        module.BACKUP_PREFIX = "prefix"
        module.CHROMADB_BATCH_SIZE = 2

        backups = [
            {
                "id": "1",
                "embeddings_semantic": [0.1],
                "embedding": [0.01],
                "metadata": {"pmid": "1"},
                "document": "doc1",
            },
            {
                "id": "2",
                "embeddings_semantic": [0.2],
                "embedding": [0.02],
                "metadata": {"pmid": "2"},
                "document": "doc2",
            },
            {
                "id": "3",
                "embeddings_semantic": [0.3],
                "embedding": [0.03],
                "metadata": {"pmid": "3"},
                "document": "doc3",
            },
        ]

        read_mock = mock.Mock(return_value=backups)
        monkeypatch.setattr(module, "stream_backup_from_gcs", read_mock)
        fake_collection = mock.Mock()
        fake_client = mock.Mock(get_or_create_collection=mock.Mock(return_value=fake_collection))

        module.load_backups_to_chromadb(fake_client, semantic=True)

        read_mock.assert_called_once_with("bucket", "prefix")
        fake_client.get_or_create_collection.assert_called_once_with(name=module.CHROMADB_COLLECTION)
        assert fake_collection.add.call_count == 2
        first_call = fake_collection.add.call_args_list[0].kwargs
        assert first_call["ids"] == ["1", "2"]
        assert first_call["embeddings"] == [[0.1], [0.2]]
        assert first_call["metadatas"] == [{"pmid": "1"}, {"pmid": "2"}]
        assert first_call["documents"] == ["doc1", "doc2"]
        second_call = fake_collection.add.call_args_list[1].kwargs
        assert second_call["ids"] == ["3"]

    def test_load_backups_to_chromadb_uses_non_semantic_embeddings(self, monkeypatch: pytest.MonkeyPatch):
        module = self._reload_module(monkeypatch)
        module.BACKUP_ENABLED = True
        module.BACKUP_BUCKET = "bucket"
        module.BACKUP_PREFIX = "prefix"
        module.CHROMADB_BATCH_SIZE = 10

        backups = [
            {
                "id": "1",
                "embeddings_semantic": [0.1],
                "embedding": [9.1],
                "metadata": {"pmid": "1"},
                "document": "doc1",
            },
            {
                "id": "2",
                "embeddings_semantic": [0.2],
                "embedding": [9.2],
                "metadata": {"pmid": "2"},
                "document": "doc2",
            },
        ]

        monkeypatch.setattr(module, "stream_backup_from_gcs", mock.Mock(return_value=backups))
        fake_collection = mock.Mock()
        fake_client = mock.Mock(get_or_create_collection=mock.Mock(return_value=fake_collection))

        module.load_backups_to_chromadb(fake_client, semantic=False)

        fake_collection.add.assert_called_once()
        call_kwargs = fake_collection.add.call_args.kwargs
        assert call_kwargs["embeddings"] == [[9.1], [9.2]]
