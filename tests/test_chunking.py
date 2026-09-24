"""Tests for the chunking layer."""

from __future__ import annotations

from rag.chunking import LARGE, SMALL, chunk_documents
from rag.document_loader import load_all_policy_documents


def test_chunking_preserves_metadata():
    documents = load_all_policy_documents()
    chunks = chunk_documents(documents, SMALL)
    assert chunks
    for chunk in chunks:
        assert chunk.chunk_id
        assert chunk.document_id
        assert chunk.document_title
        assert chunk.section_id
        assert chunk.section_title
        assert chunk.text.strip()


def test_chunk_ids_are_deterministic():
    documents = load_all_policy_documents()
    ids_first_run = [c.chunk_id for c in chunk_documents(documents, SMALL)]
    ids_second_run = [c.chunk_id for c in chunk_documents(documents, SMALL)]
    assert ids_first_run == ids_second_run
    assert len(ids_first_run) == len(set(ids_first_run))


def test_small_and_large_configs_produce_different_chunk_counts():
    documents = load_all_policy_documents()
    small_chunks = chunk_documents(documents, SMALL)
    large_chunks = chunk_documents(documents, LARGE)
    assert len(small_chunks) >= len(large_chunks)


def test_small_and_large_chunk_ids_do_not_collide():
    documents = load_all_policy_documents()
    small_ids = {c.chunk_id for c in chunk_documents(documents, SMALL)}
    large_ids = {c.chunk_id for c in chunk_documents(documents, LARGE)}
    assert small_ids.isdisjoint(large_ids)
