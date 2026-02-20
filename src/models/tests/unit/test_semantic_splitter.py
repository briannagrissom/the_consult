"""
Unit tests for semantic_splitter helpers.
"""

from __future__ import annotations

import pytest

from models.semantic_splitter import (
    SemanticChunker,
    calculate_cosine_distances,
    combine_sentences,
)


def test_combine_sentences_applies_buffer():
    sentences = [
        {"sentence": "alpha"},
        {"sentence": "beta"},
        {"sentence": "gamma"},
    ]

    combined = combine_sentences(sentences, buffer_size=1)

    assert combined[0]["combined_sentence"] == "alpha beta"
    assert combined[1]["combined_sentence"] == "alpha beta gamma"
    assert combined[2]["combined_sentence"] == "beta gamma"


def test_calculate_cosine_distances_tracks_distance_to_next():
    sentences = [
        {"combined_sentence_embedding": [1.0, 0.0]},
        {"combined_sentence_embedding": [0.0, 1.0]},
        {"combined_sentence_embedding": [1.0, 0.0]},
    ]

    distances, enriched = calculate_cosine_distances(sentences)

    assert len(distances) == 2
    assert distances[0] == pytest.approx(1.0)
    assert "distance_to_next" in enriched[0]
    assert enriched[0]["distance_to_next"] == pytest.approx(1.0)


def test_split_text_chunks_on_large_distance():
    mapping = {
        "A. B.": [1.0, 0.0],
        "A. B. C.": [1.0, 0.0],
        "B. C. D.": [-1.0, 0.0],
        "C. D.": [-1.0, 0.0],
    }

    def fake_embed(texts, batch_size=50):
        return [mapping[text] for text in texts]

    chunker = SemanticChunker(
        embedding_function=fake_embed,
        buffer_size=1,
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=80,
    )

    chunks = chunker.split_text("A. B. C. D.")

    assert chunks == ["A. B.", "C. D."]
