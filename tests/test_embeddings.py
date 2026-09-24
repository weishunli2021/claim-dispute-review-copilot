"""Tests for the embedding-provider abstraction: the TF-IDF lexical baseline
and the local SentenceTransformer semantic provider."""

from __future__ import annotations

import numpy as np
import pytest

from rag.embeddings import (
    SemanticEmbeddingProvider,
    TfidfLexicalEmbeddingProvider,
    get_embedding_provider,
)


def _cosine(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


def test_get_embedding_provider_returns_correct_types():
    assert isinstance(get_embedding_provider("tfidf"), TfidfLexicalEmbeddingProvider)
    assert isinstance(get_embedding_provider("semantic"), SemanticEmbeddingProvider)


def test_get_embedding_provider_rejects_unknown_name():
    with pytest.raises(ValueError):
        get_embedding_provider("not-a-real-provider")


def test_tfidf_requires_fit_before_embedding():
    provider = TfidfLexicalEmbeddingProvider()
    with pytest.raises(RuntimeError):
        provider.embed_query("anything")


def test_tfidf_produces_consistent_dimensions_after_fit():
    corpus = [
        "outpatient MRI requires prior authorization",
        "basic lab panel not covered under the plan",
    ]
    provider = TfidfLexicalEmbeddingProvider()
    provider.fit(corpus)
    doc_vectors = provider.embed_documents(corpus)
    query_vector = provider.embed_query("does this need prior authorization")

    dims = {len(v) for v in doc_vectors} | {len(query_vector)}
    assert len(dims) == 1


def test_semantic_embedding_creation_and_dimensions():
    provider = SemanticEmbeddingProvider()
    doc_vectors = provider.embed_documents(
        [
            "Outpatient MRI requires prior authorization.",
            "A basic lab panel is not a covered benefit.",
        ]
    )
    query_vector = provider.embed_query("Do I need permission before this scan?")

    assert len(doc_vectors) == 2
    dims = {len(v) for v in doc_vectors} | {len(query_vector)}
    assert len(dims) == 1
    assert dims.pop() == 384  # sentence-transformers/all-MiniLM-L6-v2's known output size


def test_semantic_and_tfidf_do_not_share_an_embedding_space():
    text = "outpatient MRI requires prior authorization"

    tfidf = TfidfLexicalEmbeddingProvider()
    tfidf.fit([text])
    tfidf_vector = tfidf.embed_query(text)

    semantic = SemanticEmbeddingProvider()
    semantic_vector = semantic.embed_query(text)

    assert len(tfidf_vector) != len(semantic_vector)


def test_semantic_provider_captures_paraphrase_similarity():
    # Sanity check the model captures more than literal overlap: a
    # paraphrase of a sentence should score higher cosine similarity than
    # an unrelated sentence, even with almost no shared vocabulary.
    provider = SemanticEmbeddingProvider()
    base = provider.embed_query("Does outpatient MRI require prior authorization?")
    paraphrase = provider.embed_query("Do I need permission from the plan before getting this scan?")
    unrelated = provider.embed_query("The weather today is sunny with a light breeze.")

    assert _cosine(base, paraphrase) > _cosine(base, unrelated)


def test_tfidf_fit_rejects_empty_corpus():
    with pytest.raises(ValueError):
        TfidfLexicalEmbeddingProvider().fit([])


def test_semantic_fit_rejects_empty_corpus():
    with pytest.raises(ValueError):
        SemanticEmbeddingProvider().fit([])
