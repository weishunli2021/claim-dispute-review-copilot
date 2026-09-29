"""Tests for application/dispute_brief_validator.py, using a REAL
DisputeGenerationContext + comparison_result built from the billing-
correction scenario's own default example (never hand-constructed fakes
for the context shape itself)."""

from __future__ import annotations

from application.brief_validator import ALLOWED_ACTION_CODES
from application.dispute_brief_validator import validate_dispute_brief
from application.dispute_context import assemble_dispute_context
from application.dispute_models import DisputeBrief, DisputeValidationStatus, EvidenceGateResult, EvidenceGateStatus
from application.models import ActionCode, Finding, SuggestedNextStep
from context.dispute_evidence_retriever import build_dispute_evidence_package
from dispute_review.billing_fixtures import BILLING_CLAIM_ID
from dispute_review.presets import billing_correction_example_preset


def _context():
    package = build_dispute_evidence_package(BILLING_CLAIM_ID, billing_correction_example_preset())
    gate = EvidenceGateResult(status=EvidenceGateStatus.READY_FOR_SCOPED_GENERATION, reasons=[])
    return package, assemble_dispute_context(package, gate)


def _brief(**overrides) -> DisputeBrief:
    real_ref = overrides.pop("ref", "comparison:service_code")
    defaults = dict(
        summary="Summary.",
        findings=[Finding(statement="Grounded.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="Route it.", evidence_refs=[real_ref]),
    )
    defaults.update(overrides)
    return DisputeBrief(**defaults)


def test_well_formed_brief_passes():
    package, context = _context()
    result = validate_dispute_brief(_brief(), context, package.comparison_result)
    assert result.status == DisputeValidationStatus.PASSED


def test_rule_a_rejects_unknown_reference():
    package, context = _context()
    result = validate_dispute_brief(_brief(ref="comparison:not_a_real_field"), context, package.comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "evidence_reference_existence" for i in result.issues)


def test_rule_b_rejects_finding_with_no_refs():
    package, context = _context()
    brief = _brief()
    brief = brief.model_copy(update={"findings": [Finding(statement="x", evidence_refs=[])]})
    result = validate_dispute_brief(brief, context, package.comparison_result)
    assert any(i.rule == "finding_requires_evidence_ref" for i in result.issues)


def test_rule_c_comparison_context_integrity_holds_for_real_context():
    package, context = _context()
    result = validate_dispute_brief(_brief(), context, package.comparison_result)
    assert not any(i.rule == "comparison_context_integrity" for i in result.issues)


def test_rule_c_skipped_when_comparison_result_is_none():
    package, context = _context()
    result = validate_dispute_brief(_brief(), context, None)
    assert not any(i.rule == "comparison_context_integrity" for i in result.issues)


def test_rule_d_rejects_unapproved_action_code():
    package, context = _context()
    brief = _brief()
    step = brief.suggested_next_step.model_copy(update={"action_code": "NOT_A_REAL_CODE"})
    brief = brief.model_copy(update={"suggested_next_step": step})
    result = validate_dispute_brief(brief, context, package.comparison_result)
    assert any(i.rule == "advisory_action_allowlist" for i in result.issues)


def test_all_allowed_action_codes_pass_rule_d():
    package, context = _context()
    for code in ALLOWED_ACTION_CODES:
        step = SuggestedNextStep(action_code=code, rationale="r", evidence_refs=["comparison:service_code"])
        brief = _brief().model_copy(update={"suggested_next_step": step})
        result = validate_dispute_brief(brief, context, package.comparison_result)
        assert not any(i.rule == "advisory_action_allowlist" for i in result.issues)


def test_rule_e_flags_unnegated_verified_claim_about_submitted_field():
    package, context = _context()
    submitted_ref = next(r.ref_id for r in context.references if r.source_type == "submitted_field")
    brief = _brief().model_copy(
        update={"findings": [Finding(statement="The submitted value is verified.", evidence_refs=[submitted_ref])]}
    )
    result = validate_dispute_brief(brief, context, package.comparison_result)
    assert any(i.rule == "unverified_provenance_preserved" for i in result.issues)


def test_rule_e_allows_negated_verified_language():
    package, context = _context()
    submitted_ref = next(r.ref_id for r in context.references if r.source_type == "submitted_field")
    brief = _brief().model_copy(
        update={"findings": [Finding(statement="The submitted value has not been verified.", evidence_refs=[submitted_ref])]}
    )
    result = validate_dispute_brief(brief, context, package.comparison_result)
    assert not any(i.rule == "unverified_provenance_preserved" for i in result.issues)


def test_rule_e_never_applies_to_independent_supporting_record_references():
    """INDEPENDENT_SUPPORTING_RECORD is trusted ground truth for this
    scenario -- unlike SUBMITTED_UNVERIFIED, describing it plainly is fine."""
    package, context = _context()
    support_ref = next(r.ref_id for r in context.references if r.source_type == "independent_support_record")
    brief = _brief().model_copy(
        update={"findings": [Finding(statement="The operative note confirms this.", evidence_refs=[support_ref])]}
    )
    result = validate_dispute_brief(brief, context, package.comparison_result)
    assert not any(i.rule == "unverified_provenance_preserved" for i in result.issues)


def test_rule_f_rejects_prohibited_authority_language():
    package, context = _context()
    brief = _brief(summary="Approve the claim for payment.")
    result = validate_dispute_brief(brief, context, package.comparison_result)
    assert any(i.rule == "prohibited_authority_language" for i in result.issues)
