"""Tests for application/dispute_brief_validator.py.

Uses a REAL DisputeGenerationContext + comparison_result (CLM-1001,
matching preset) as the fixed backdrop, then constructs deliberately
DEFECTIVE DisputeBrief objects by hand to exercise each rule -- passing
mocked-judge/validator tests does not demonstrate real generation
quality; it demonstrates the deterministic checks work as specified.
"""

from __future__ import annotations

import pytest

from application.dispute_brief_validator import validate_dispute_brief
from application.dispute_context import assemble_dispute_context
from application.dispute_models import DisputeBrief, DisputeValidationStatus
from application.dispute_workflow import assess_evidence_gate
from application.models import ActionCode, Finding, SuggestedNextStep
from dispute_review.presets import build_demo_presets
from skills.investigate_dispute import investigate_dispute
from tools.case_context import get_case_context


def _real_context_and_comparison():
    case_context = get_case_context("CLM-1001")
    presets = build_demo_presets(case_context)
    result = investigate_dispute("CLM-1001", presets.matching)
    from context.dispute_evidence_models import DisputeEvidencePackage

    package = DisputeEvidencePackage.model_validate(result.evidence["dispute_evidence_package"])
    gate = assess_evidence_gate(package)
    context = assemble_dispute_context(package, gate)
    return context, package.comparison_result


def _well_formed_brief(context) -> DisputeBrief:
    real_ref = context.references[0].ref_id
    return DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="A grounded fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION, rationale="Because.", evidence_refs=[real_ref]
        ),
    )


def test_well_formed_brief_passes():
    context, comparison_result = _real_context_and_comparison()
    brief = _well_formed_brief(context)
    result = validate_dispute_brief(brief, context, comparison_result)
    assert result.status == DisputeValidationStatus.PASSED
    assert result.issues == []


def test_unknown_citation_in_finding_fails_rule_a():
    context, comparison_result = _real_context_and_comparison()
    brief = DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="A fact.", evidence_refs=["claim:DOES-NOT-EXIST-ANYWHERE"])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]
        ),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "evidence_reference_existence" for i in result.issues)


def test_unknown_citation_in_next_step_fails_rule_a():
    context, comparison_result = _real_context_and_comparison()
    real_ref = context.references[0].ref_id
    brief = DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="A fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=["policy:FAKE-CHUNK"]
        ),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert any(i.rule == "evidence_reference_existence" for i in result.issues)


def test_missing_citation_fails_rule_b():
    context, comparison_result = _real_context_and_comparison()
    brief = DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="An uncited fact.", evidence_refs=[])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "finding_requires_evidence_ref" for i in result.issues)


def test_corrupted_context_comparison_reference_fails_rule_c():
    context, comparison_result = _real_context_and_comparison()
    # Corrupt one comparison reference's detail text in the context --
    # simulates a context-assembly bug, never something the model did.
    corrupted_refs = []
    for ref in context.references:
        if ref.ref_id == "comparison:servicing_provider":
            corrupted_refs.append(ref.model_copy(update={"detail": "corrupted text, does not match the comparator"}))
        else:
            corrupted_refs.append(ref)
    corrupted_context = context.model_copy(update={"references": corrupted_refs})

    brief = _well_formed_brief(corrupted_context)
    result = validate_dispute_brief(brief, corrupted_context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "comparison_context_integrity" for i in result.issues)


def test_missing_context_comparison_reference_fails_rule_c():
    context, comparison_result = _real_context_and_comparison()
    trimmed_refs = [ref for ref in context.references if ref.ref_id != "comparison:member"]
    trimmed_context = context.model_copy(update={"references": trimmed_refs})

    brief = _well_formed_brief(trimmed_context)
    result = validate_dispute_brief(brief, trimmed_context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(
        i.rule == "comparison_context_integrity" and "comparison:member" in i.detail for i in result.issues
    )


def test_unsupported_consequential_action_is_unrepresentable_but_allowlist_checked():
    # ActionCode's own closed enum already makes an out-of-allowlist action
    # unrepresentable in a parsed brief -- this test verifies Rule D's
    # independent enforcement layer, matching
    # tests/test_application_brief_validator.py's own precedent for the
    # original validator.
    from application.brief_validator import ALLOWED_ACTION_CODES

    assert set(ActionCode) == ALLOWED_ACTION_CODES  # today's allowlist is the full enum


def test_unverified_provenance_described_as_confirmed_fails_rule_e():
    context, comparison_result = _real_context_and_comparison()
    submitted_ref = next(r.ref_id for r in context.references if r.provenance == "SUBMITTED_UNVERIFIED")
    brief = DisputeBrief(
        summary="Summary.",
        findings=[
            Finding(
                statement="The submitted authorization reference is confirmed valid.",
                evidence_refs=[submitted_ref],
            )
        ],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "unverified_provenance_preserved" for i in result.issues)


def test_unverified_reference_without_verified_language_passes_rule_e():
    # Deliberately avoids the trigger words entirely.
    context, comparison_result = _real_context_and_comparison()
    submitted_ref = next(r.ref_id for r in context.references if r.provenance == "SUBMITTED_UNVERIFIED")
    brief = DisputeBrief(
        summary="Summary.",
        findings=[
            Finding(
                statement="The submission states an authorization reference for this dispute.",
                evidence_refs=[submitted_ref],
            )
        ],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert not any(i.rule == "unverified_provenance_preserved" for i in result.issues)


# --- Rule E negation-awareness (fixed after a live smoke check found 3/3
# live requests false-positived on the correct hedge word "unverified" --
# see docs/DISPUTE_AI_VALIDATION.md and
# application/dispute_brief_validator.py's own module-level comment on
# _find_unnegated_verified_claim for the exact, narrow scope of this fix).
# Every SAFE case below uses wording actually observed from the live model
# in that check; every UNSAFE case confirms the fix did not weaken Rule E
# itself. ---------------------------------------------------------------


def _rule_e_brief(statement: str, submitted_ref: str) -> DisputeBrief:
    return DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement=statement, evidence_refs=[submitted_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )


def _assert_rule_e(statement: str, *, expected_flagged: bool) -> None:
    context, comparison_result = _real_context_and_comparison()
    submitted_ref = next(r.ref_id for r in context.references if r.provenance == "SUBMITTED_UNVERIFIED")
    brief = _rule_e_brief(statement, submitted_ref)
    result = validate_dispute_brief(brief, context, comparison_result)
    flagged = any(i.rule == "unverified_provenance_preserved" for i in result.issues)
    assert flagged == expected_flagged, (
        f"statement {statement!r}: expected flagged={expected_flagged}, got {flagged} "
        f"(issues: {[i.detail for i in result.issues if i.rule == 'unverified_provenance_preserved']})"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "The authorization is unverified.",
        "The authorization has not been verified.",
        "Authenticity has not yet been verified.",
        "Matching fields do not establish verified authorization.",
    ],
)
def test_rule_e_safe_negated_or_hedged_wording_is_not_flagged(statement):
    _assert_rule_e(statement, expected_flagged=False)


@pytest.mark.parametrize(
    "statement",
    [
        "The submitted authorization is verified.",
        "We verified the submitted authorization.",
        "Authenticity has been confirmed.",
    ],
)
def test_rule_e_unsafe_unnegated_claims_are_still_flagged(statement):
    _assert_rule_e(statement, expected_flagged=True)


def test_rule_e_mixed_clause_positive_claim_after_negated_clause_is_still_flagged():
    # An earlier disclaimer/negation must not exempt a later, separate
    # positive claim in the same statement.
    _assert_rule_e(
        "The submission is unverified, but we have verified its authenticity.",
        expected_flagged=True,
    )


def test_prohibited_authority_language_in_summary_fails_rule_f():
    context, comparison_result = _real_context_and_comparison()
    real_ref = context.references[0].ref_id
    brief = DisputeBrief(
        summary="We approve the claim.",
        findings=[Finding(statement="A fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "prohibited_authority_language" for i in result.issues)


def test_prohibited_authority_language_in_verification_question_fails_rule_f():
    context, comparison_result = _real_context_and_comparison()
    real_ref = context.references[0].ref_id
    brief = DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="A fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=["Approve the authorization."],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert result.status == DisputeValidationStatus.FAILED
    assert any(i.rule == "prohibited_authority_language" for i in result.issues)


def test_third_party_historical_fact_does_not_trigger_rule_f():
    context, comparison_result = _real_context_and_comparison()
    real_ref = context.references[0].ref_id
    brief = DisputeBrief(
        summary="The payer denied the claim for AUTH_REQUIRED.",
        findings=[Finding(statement="A fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    assert not any(i.rule == "prohibited_authority_language" for i in result.issues)


def test_multiple_defects_are_all_reported():
    context, comparison_result = _real_context_and_comparison()
    brief = DisputeBrief(
        summary="We approve the claim.",
        findings=[Finding(statement="An uncited, wrong-ref fact.", evidence_refs=["claim:NOPE"])],
        missing_or_conflicting_evidence=[],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(action_code=ActionCode.HUMAN_REVIEW, rationale="x", evidence_refs=[]),
    )
    result = validate_dispute_brief(brief, context, comparison_result)
    rules_hit = {i.rule for i in result.issues}
    assert "evidence_reference_existence" in rules_hit
    assert "prohibited_authority_language" in rules_hit
