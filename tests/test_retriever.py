"""Tests for the semantic retriever: evidence-only search over the real policy corpus."""

from __future__ import annotations

import pytest

from rag.models import RetrievedChunk
from rag.retriever import search_policy


def test_search_policy_returns_relevant_evidence_for_mri_prior_auth_question():
    results = search_policy("Does outpatient knee MRI require prior authorization?", top_k=3)
    assert results
    section_ids = {r.section_id for r in results}
    assert section_ids & {"IMG-2", "PA-2", "BEN-2"}
    for result in results:
        assert result.text.strip()
        assert result.chunk_id
        assert result.score is not None


def test_search_policy_irrelevant_query_fails_gracefully():
    # No vocabulary overlap with the corpus -- must degrade to low/zero
    # relevance scores rather than raising or returning malformed results.
    results = search_policy("zzz qwerty asdf unrelated gibberish 12345", top_k=3)
    assert isinstance(results, list)
    assert len(results) <= 3
    for result in results:
        assert result.score is not None


def test_search_policy_blank_query_raises():
    with pytest.raises(ValueError):
        search_policy("")


def test_search_policy_returns_evidence_only_no_answer_fields():
    results = search_policy("appeal a denied claim", top_k=1)
    assert set(RetrievedChunk.model_fields) == {
        "chunk_id",
        "document_id",
        "document_title",
        "section_id",
        "section_title",
        "text",
        "score",
    }


def test_search_policy_rejects_unknown_provider_name():
    with pytest.raises(ValueError):
        search_policy("anything", provider_name="bogus")


def test_semantic_provider_beats_tfidf_on_appeals_paraphrase():
    # A paraphrase that deliberately avoids the word "appeal" (see
    # evals/retrieval_golden_set.json: paraphrase_appeals). This is a
    # documented, expected result at this stage, not an incidental one --
    # TF-IDF's lexical matching has no shared vocabulary to work with here,
    # while the semantic provider recognizes the underlying concept.
    query = "If I don't agree with how my claim was handled, how do I ask the plan to take another look at it?"

    semantic_sections = {r.section_id for r in search_policy(query, top_k=5, provider_name="semantic")}
    assert semantic_sections & {"APL-1", "APL-2"}

    tfidf_sections = {r.section_id for r in search_policy(query, top_k=5, provider_name="tfidf")}
    assert not (tfidf_sections & {"APL-1", "APL-2"})
