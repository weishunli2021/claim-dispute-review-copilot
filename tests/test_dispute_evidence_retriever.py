"""Tests for context.dispute_evidence_retriever: the four billing-scenario
evidence adapters (recorded claim/decision, independent supporting
records, synthetic policy via vector search, and the isolated provider-
network graph) and the full assembly function, including the claim/member
linkage safety check."""

from __future__ import annotations

import pytest

from context.dispute_evidence_models import EvidenceProvenanceCategory as Provenance
from context.dispute_evidence_retriever import (
    build_dispute_evidence_package,
    gather_network_evidence,
    gather_policy_evidence,
    gather_recorded_evidence,
    gather_support_record_evidence,
)
from dispute_review.billing_fixtures import BILLING_CLAIM_ID, get_billing_claim_record, get_billing_support_records
from dispute_review.comparison import build_billing_claim_snapshot
from dispute_review.models import BillingCorrectionSubmission


def test_build_package_unknown_claim_id_raises_value_error():
    with pytest.raises(ValueError):
        build_dispute_evidence_package("CLM-DOES-NOT-EXIST", BillingCorrectionSubmission())


def test_gather_recorded_evidence_covers_claim_and_decision():
    claim = build_billing_claim_snapshot(get_billing_claim_record())
    result = gather_recorded_evidence(claim)
    ref_ids = {ref.ref_id for ref in result.references}
    assert f"claim:{BILLING_CLAIM_ID}" in ref_ids
    assert f"decision:{BILLING_CLAIM_ID}" in ref_ids
    assert all(ref.provenance == Provenance.RECORDED for ref in result.references)


def test_gather_support_record_evidence_uses_independent_provenance():
    result = gather_support_record_evidence(get_billing_support_records())
    assert len(result.references) == 3
    assert all(ref.provenance == Provenance.INDEPENDENT_SUPPORTING_RECORD for ref in result.references)


def test_support_records_are_never_mutated_by_gathering_evidence():
    before = [r.model_dump() for r in get_billing_support_records()]
    gather_support_record_evidence(get_billing_support_records())
    after = [r.model_dump() for r in get_billing_support_records()]
    assert before == after


def test_gather_policy_evidence_uses_real_semantic_vector_search():
    result = gather_policy_evidence()
    assert result.references
    assert len(result.references) <= 3  # top_k, never the full fixed section set
    assert all(ref.provenance == Provenance.SYNTHETIC_BILLING_POLICY for ref in result.references)
    assert all(ref.score is not None for ref in result.references)  # a real similarity score
    scores = [ref.score for ref in result.references]
    assert scores == sorted(scores, reverse=True)  # ranked, most-similar first


def test_gather_policy_evidence_query_reflects_conflicting_fields():
    from dispute_review.comparison import build_billing_claim_snapshot, compare_billing_correction
    from dispute_review.billing_fixtures import get_billing_support_records

    claim = build_billing_claim_snapshot(get_billing_claim_record())
    conflicting_submission = BillingCorrectionSubmission(service_code="SURG-BOGUS")
    comparison = compare_billing_correction(claim, conflicting_submission, get_billing_support_records())
    result = gather_policy_evidence(comparison)
    assert result.references  # still resolves without error against a CONFLICTS-shaped query


def test_gather_network_evidence_checks_original_and_proposed_providers():
    result = gather_network_evidence("PRV-BILL-SUNRISEHMO200", "PRV-BILL-SYNTHETICCHOICEPPO500")
    ref_ids = {ref.ref_id for ref in result.references}
    assert ref_ids == {"network:PRV-BILL-SUNRISEHMO200", "network:PRV-BILL-SYNTHETICCHOICEPPO500"}
    assert all(ref.provenance == Provenance.INDEPENDENT_PROVIDER_NETWORK_RELATIONSHIP for ref in result.references)
    detail_by_id = {ref.ref_id: ref.detail for ref in result.references}
    assert "True" in detail_by_id["network:PRV-BILL-SYNTHETICCHOICEPPO500"]
    assert "False" in detail_by_id["network:PRV-BILL-SUNRISEHMO200"]


def test_gather_network_evidence_deduplicates_identical_provider_ids():
    result = gather_network_evidence("PRV-BILL-SYNTHETICCHOICEPPO500", "PRV-BILL-SYNTHETICCHOICEPPO500")
    assert len(result.references) == 1


def test_gather_network_evidence_unknown_provider_is_no_results_not_failure():
    from context.dispute_evidence_models import EvidenceSourceStatus

    result = gather_network_evidence("PRV-DOES-NOT-EXIST", None)
    assert result.references == []
    assert any(o.status == EvidenceSourceStatus.SUCCESS_NO_RESULTS for o in result.outcomes)
    assert not any(o.status == EvidenceSourceStatus.FAILURE for o in result.outcomes)


def test_full_package_default_example_is_fully_supported_and_resolvable():
    from dispute_review.presets import billing_correction_example_preset

    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    assert package.linkage_issue is None
    assert package.comparison_result is not None
    assert package.missing_evidence == []
    assert package.conflicts == []

    assert package.policy_passages  # real vector search returned ranked results
    assert package.network_relationships  # isolated network graph returned results

    all_ref_ids = {
        ref.ref_id
        for refs in (
            package.recorded_facts,
            package.submitted_fields,
            package.comparison_findings,
            package.support_records,
            package.policy_passages,
            package.network_relationships,
        )
        for ref in refs
    }
    for row in package.comparison_result.rows:
        for evidence_ref in row.evidence_refs:
            assert evidence_ref in all_ref_ids


def test_package_blocks_on_claim_reference_mismatch():
    submission = BillingCorrectionSubmission(original_claim_reference="CLM-OTHER")
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, submission)
    assert package.linkage_issue is not None
    assert package.comparison_result is None
    assert package.support_records == []
    assert package.policy_passages == []
    assert package.network_relationships == []
    assert package.conflicts == [package.linkage_issue]


def test_package_blocks_on_member_mismatch_never_retrieves_for_another_member():
    submission = BillingCorrectionSubmission(member_id="MEM-SOMEONE-ELSE")
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, submission)
    assert package.linkage_issue is not None
    assert package.comparison_result is None


def test_missing_evidence_reported_when_independent_records_are_ambiguous(monkeypatch):
    from dispute_review import billing_fixtures

    def _fake_records(*args, **kwargs):
        real = get_billing_support_records()
        conflicting = real[0].model_copy(update={"record_id": "BILLREC-CONFLICT", "supports_units": 99})
        return real + [conflicting]

    monkeypatch.setattr(billing_fixtures, "get_billing_support_records", _fake_records)
    monkeypatch.setattr("context.dispute_evidence_retriever.get_billing_support_records", _fake_records)

    from dispute_review.presets import billing_correction_example_preset

    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    assert package.comparison_result is not None
    assert any("Units" in item for item in package.missing_evidence)
