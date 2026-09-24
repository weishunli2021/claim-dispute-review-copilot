"""Tests for the vector store: building, querying, and rebuilding without duplication.

Uses a small synthetic chunk set and a tmp_path index root -- isolated from
the real policy corpus and the real rag/index/ directory used in development.
"""

from __future__ import annotations

import pytest

from rag.chunking import SMALL
from rag.models import Chunk
from rag.vector_store import IndexNotBuiltError, VectorStore


def _sample_chunks() -> list[Chunk]:
    return [
        Chunk(
            chunk_id="doc_a::SEC-1::small::00",
            document_id="doc_a",
            document_title="Doc A",
            section_id="SEC-1",
            section_title="Section One",
            text="Outpatient MRI requires prior authorization before it is performed.",
        ),
        Chunk(
            chunk_id="doc_a::SEC-2::small::00",
            document_id="doc_a",
            document_title="Doc A",
            section_id="SEC-2",
            section_title="Section Two",
            text="Basic lab panels are not covered under the Silver plan.",
        ),
    ]


def test_query_before_build_raises_clear_error(tmp_path):
    store = VectorStore(config=SMALL, index_root=tmp_path)
    with pytest.raises(IndexNotBuiltError):
        store.query("anything")


def test_build_index_then_query_returns_results_with_metadata(tmp_path):
    store = VectorStore(config=SMALL, index_root=tmp_path)
    store.build_index(_sample_chunks())

    results = store.query("Does an MRI need prior authorization?", top_k=2)
    assert results
    top = results[0]
    assert top.section_id == "SEC-1"
    assert top.document_id == "doc_a"
    assert top.document_title == "Doc A"
    assert top.chunk_id == "doc_a::SEC-1::small::00"
    assert top.score is not None


def test_rebuilding_index_does_not_duplicate_records(tmp_path):
    store = VectorStore(config=SMALL, index_root=tmp_path)
    chunks = _sample_chunks()

    store.build_index(chunks)
    assert store.count() == len(chunks)

    store.build_index(chunks)
    assert store.count() == len(chunks)


def test_build_index_rejects_empty_chunk_list(tmp_path):
    store = VectorStore(config=SMALL, index_root=tmp_path)
    with pytest.raises(ValueError):
        store.build_index([])


def test_index_isolated_by_provider_and_config(tmp_path):
    tfidf_small = VectorStore(config=SMALL, provider_name="tfidf", index_root=tmp_path)
    tfidf_small.build_index(_sample_chunks())

    # A different provider at the same chunking config must be a completely
    # separate, unbuilt index -- never sharing the tfidf collection.
    semantic_small = VectorStore(config=SMALL, provider_name="semantic", index_root=tmp_path)
    assert not semantic_small.exists()
    assert tfidf_small.exists()

    assert tfidf_small.chroma_path != semantic_small.chroma_path
    assert tfidf_small.index_root == tmp_path / "tfidf" / "small"
    assert semantic_small.index_root == tmp_path / "semantic" / "small"


def test_vector_store_rejects_unknown_provider_name(tmp_path):
    with pytest.raises(ValueError):
        VectorStore(config=SMALL, provider_name="bogus", index_root=tmp_path)
