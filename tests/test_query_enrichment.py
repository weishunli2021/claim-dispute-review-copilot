"""Tests for deterministic query enrichment."""

from __future__ import annotations

from context.models import StructuredEvidence
from context.query_enrichment import enrich_query
from tools.case_context import get_case_context


def test_enrichment_uses_only_real_structured_facts_case1():
    structured_facts = StructuredEvidence(**get_case_context("CLM-1001").model_dump())
    enriched = enrich_query("My claim was denied. What's wrong with it?", structured_facts)

    assert "My claim was denied. What's wrong with it?" in enriched
    assert "Service: MRI-KNEE." in enriched
    assert "Denial reason: AUTH_REQUIRED." in enriched


def test_enrichment_omits_denial_reason_when_none_present_case2():
    # CLM-1002 is PAID -- there is no denial_reason_code to append, and
    # none must be invented.
    structured_facts = StructuredEvidence(**get_case_context("CLM-1002").model_dump())
    enriched = enrich_query("Was this claim paid correctly?", structured_facts)

    assert "Service: MRI-KNEE." in enriched
    assert "Denial reason:" not in enriched


def test_enrichment_omits_denial_reason_when_recorded_as_none_case5():
    # CLM-1005 is DENIED but its denial_reason_code is itself None in the
    # source data -- enrichment must not fabricate one.
    structured_facts = StructuredEvidence(**get_case_context("CLM-1005").model_dump())
    enriched = enrich_query("Why was my specialist visit claim denied?", structured_facts)

    assert "Service: SPECIALIST-VISIT." in enriched
    assert "Denial reason:" not in enriched


def test_enrichment_returns_query_unchanged_for_unknown_claim():
    structured_facts = StructuredEvidence(**get_case_context("CLM-9999").model_dump())
    original = "Anything at all?"
    assert enrich_query(original, structured_facts) == original


def test_original_query_text_is_never_mutated():
    structured_facts = StructuredEvidence(**get_case_context("CLM-1001").model_dump())
    original = "My claim was denied. What's wrong with it?"
    enriched = enrich_query(original, structured_facts)
    assert enriched.startswith(original)
    assert enriched != original
