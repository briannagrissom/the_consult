"""
Unit test for parquet_to_chromadb.py pipeline
"""

import pytest
import pandas as pd
from models.parquet_to_chromadb import _build_chunk_records

# from models.parquet_to_chromadb import connect_to_chromadb, _build_chunk_records, _upload_records


class TestParquetToChromaDB:

    def test_build_chunk_records(self):

        data = {
            "pmid": [123, 456],
            "title": ["Title 1", "Title 2"],
            "authors": ["Author A; Author B", "Author C; Author D"],
        }
        df = pd.DataFrame(data)

        chunk_map = [(0, 0), (1, 0)]
        chunk_texts = ["This is a short abstract.", "This abstract is a bit longer. " * 10]
        embeddings = [[0.1] * 768, [0.2] * 768]

        records = _build_chunk_records(df, chunk_map, chunk_texts, embeddings)
        assert len(records) == 2
        assert records[0]["id"] == "123-0"
        assert records[1]["id"] == "456-0"
        assert records[0]["metadata"]["pmid"] == "123"
        assert records[1]["metadata"]["pmid"] == "456"

    def test_build_chunk_records_mismatched_lengths(self):
        data = {
            "pmid": [123],
            "title": ["Title 1"],
        }
        df = pd.DataFrame(data)

        chunk_map = [(0, 0)]
        chunk_texts = ["This is a short abstract.", "Extra chunk text."]
        embeddings = [[0.1] * 768]

        with pytest.raises(ValueError):
            _build_chunk_records(df, chunk_map, chunk_texts, embeddings)
