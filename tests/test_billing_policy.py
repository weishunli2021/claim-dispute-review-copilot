"""Tests for dispute_review.billing_policy: the deterministic full-document
read and the REAL semantic vector search over the billing-correction
scenario's own isolated index. Uses the LOCAL "semantic" embedding
provider (sentence-transformers) -- no API key, no network call to any LLM
provider (the model itself may be downloaded from Hugging Face Hub on a
cold cache, same as the original investigation pipeline's own default
policy provider)."""

from __future__ import annotations

import pytest

from dispute_review.billing_policy import (
    BillingPolicyLoadError,
    get_billing_policy_sections,
    search_billing_policy,
)


def test_get_billing_policy_sections_returns_full_fixed_set():
    sections = get_billing_policy_sections()
    assert [s.section_id for s in sections] == ["BILL-1", "BILL-2", "BILL-3", "BILL-4", "BILL-5"]


def test_missing_document_raises_billing_policy_load_error(tmp_path):
    with pytest.raises(BillingPolicyLoadError):
        get_billing_policy_sections(tmp_path / "does_not_exist.md")


def test_search_rejects_blank_query():
    with pytest.raises(ValueError):
        search_billing_policy("   ")


def test_search_returns_ranked_top_k_not_the_full_document():
    results = search_billing_policy("servicing provider network participation", top_k=3)
    assert 0 < len(results) <= 3
    scores = [r.score for r in results]
    assert all(s is not None for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_search_result_sections_come_from_the_billing_document_only():
    results = search_billing_policy("corrected claim review analyst checklist", top_k=5)
    assert all(r.document_id == "billing_correction_policy" for r in results)
    assert all(r.section_id.startswith("BILL-") for r in results)


def test_search_index_is_isolated_from_the_shared_rag_index(tmp_path):
    """Building/querying this scenario's index must never touch or require
    the shared rag/index/ directory the golden-dataset pipeline uses."""
    from dispute_review.billing_policy import DEFAULT_BILLING_POLICY_INDEX_ROOT
    from rag.vector_store import DEFAULT_INDEX_ROOT

    assert DEFAULT_BILLING_POLICY_INDEX_ROOT != DEFAULT_INDEX_ROOT
    assert "index_billing" in str(DEFAULT_BILLING_POLICY_INDEX_ROOT)
