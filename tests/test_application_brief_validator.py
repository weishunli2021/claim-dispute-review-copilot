"""Tests for application/brief_validator.py -- the H2 deterministic
validation rules (A: evidence-reference existence, B: findings require
evidence, C: advisory action allowlist, D: narrow prohibited-authority
language). Fully offline, no LLM involved anywhere in this file.
"""

from __future__ import annotations

import pytest

from application.brief_validator import validate_investigation_brief
from application.models import (
    ActionCode,
    AssembledContext,
    ContextReference,
    Finding,
    InvestigationBrief,
    SuggestedNextStep,
    ValidationStatus,
)


def _context(*ref_ids: str) -> AssembledContext:
    return AssembledContext(
        claim_id="CLM-1001",
        original_query="Why was this claim denied?",
        enriched_query="Why was this claim denied? Service: MRI-KNEE.",
        evidence_sufficiency_status="EVIDENCE_SUFFICIENT",
        references=[
            ContextReference(ref_id=ref_id, kind="claim", label="x", detail="x")
            for ref_id in ref_ids
        ],
    )


def _brief(
    summary: str = "Summary.",
    findings: list[Finding] | None = None,
    missing: list[str] | None = None,
    action_code: ActionCode = ActionCode.EXPLAIN_RECORDED_STATUS,
    rationale: str = "Rationale.",
    next_step_refs: list[str] | None = None,
) -> InvestigationBrief:
    return InvestigationBrief(
        summary=summary,
        findings=findings if findings is not None else [Finding(statement="A fact.", evidence_refs=["claim:CLM-1001"])],
        missing_or_conflicting_evidence=missing or [],
        suggested_next_step=SuggestedNextStep(
            action_code=action_code, rationale=rationale, evidence_refs=next_step_refs or []
        ),
    )


# --- A. Valid Case-1-style brief -------------------------------------------------


def test_valid_brief_passes_every_rule():
    context = _context("claim:CLM-1001", "benefit:BEN-GOLD-MRI-KNEE", "policy:PA-4")
    brief = _brief(
        summary="The claim was denied with reason AUTH_REQUIRED.",
        findings=[
            Finding(
                statement="SOURCE FACT: the claim is recorded as DENIED for AUTH_REQUIRED.",
                evidence_refs=["claim:CLM-1001"],
            ),
            Finding(
                statement="SOURCE FACT: the benefit requires prior authorization.",
                evidence_refs=["benefit:BEN-GOLD-MRI-KNEE", "policy:PA-4"],
            ),
        ],
        missing=["No prior-authorization record is on file."],
        action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
        rationale="Review whether prior authorization information exists.",
        next_step_refs=["claim:CLM-1001"],
    )

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.PASSED
    assert result.issues == []


# --- B. Hallucinated/nonexistent evidence ref -------------------------------------


def test_nonexistent_evidence_ref_fails_and_is_reported():
    context = _context("claim:CLM-1001")
    brief = _brief(
        findings=[
            Finding(
                statement="A finding citing a reference that was never supplied.",
                evidence_refs=["claim:CLM-1001", "auth:PA-9999-DOES-NOT-EXIST"],
            )
        ]
    )

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    rules = {issue.rule for issue in result.issues}
    assert "evidence_reference_existence" in rules
    assert any("PA-9999-DOES-NOT-EXIST" in issue.detail for issue in result.issues)


def test_nonexistent_evidence_ref_on_suggested_next_step_also_fails():
    context = _context("claim:CLM-1001")
    brief = _brief(next_step_refs=["policy:DOES-NOT-EXIST"])

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(
        issue.rule == "evidence_reference_existence" and "DOES-NOT-EXIST" in issue.detail
        for issue in result.issues
    )


# --- C. Finding with zero evidence refs -------------------------------------------


def test_finding_with_zero_evidence_refs_fails():
    context = _context("claim:CLM-1001")
    brief = _brief(findings=[Finding(statement="An unsupported finding.", evidence_refs=[])])

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "finding_requires_evidence_ref" for issue in result.issues)


# --- D. Explicit prohibited action -------------------------------------------------


def test_prohibited_action_value_fails():
    # ActionCode is a closed enum, so this is exercised by monkeypatching
    # the validator's own allowlist check via a brief whose action_code is
    # temporarily forced to a value outside ALLOWED_ACTION_CODES, proving
    # Rule C is enforced explicitly and not merely by the schema.
    import application.brief_validator as validator_module

    context = _context("claim:CLM-1001")
    brief = _brief(action_code=ActionCode.HUMAN_REVIEW)

    original_allowlist = validator_module.ALLOWED_ACTION_CODES
    try:
        validator_module.ALLOWED_ACTION_CODES = frozenset(
            {ActionCode.EXPLAIN_RECORDED_STATUS}
        )  # HUMAN_REVIEW no longer allowed, for this test only
        result = validate_investigation_brief(brief, context)
    finally:
        validator_module.ALLOWED_ACTION_CODES = original_allowlist

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "advisory_action_allowlist" for issue in result.issues)


def test_action_code_enum_itself_excludes_every_prohibited_action_name():
    from application.brief_validator import PROHIBITED_ACTION_NAMES

    allowed_values = {c.value for c in ActionCode}
    assert not (allowed_values & PROHIBITED_ACTION_NAMES)


# --- E. Explicit generated authority statement ------------------------------------


def test_explicit_authority_statement_in_summary_fails():
    context = _context("claim:CLM-1001")
    brief = _brief(summary="Approve the claim.")

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "prohibited_authority_language" for issue in result.issues)


def test_explicit_authority_statement_in_finding_fails():
    context = _context("claim:CLM-1001")
    brief = _brief(
        findings=[Finding(statement="We deny this claim.", evidence_refs=["claim:CLM-1001"])]
    )

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "prohibited_authority_language" for issue in result.issues)


def test_explicit_authority_statement_in_rationale_fails():
    context = _context("claim:CLM-1001")
    brief = _brief(rationale="Reverse the claim.")

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "prohibited_authority_language" for issue in result.issues)


def test_medical_necessity_authority_claim_fails():
    context = _context("claim:CLM-1001")
    brief = _brief(summary="This system determines medical necessity.")

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "prohibited_authority_language" for issue in result.issues)


@pytest.mark.parametrize(
    "prohibited_text",
    [
        "We approve this claim.",
        "Approve the claim.",
        "We deny this claim.",
        "Deny the claim.",
        "Reverse the claim.",
        "Pay the claim.",
        "Approve the prior authorization.",
        "Deny the prior authorization.",
        "This system determines medical necessity.",
    ],
)
def test_every_documented_prohibited_phrase_fails(prohibited_text):
    context = _context("claim:CLM-1001")
    brief = _brief(summary=prohibited_text)

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "prohibited_authority_language" for issue in result.issues)


# --- F/G. Historical/source and advisory statements must NOT fail ----------------


@pytest.mark.parametrize(
    "allowed_text",
    [
        "The claim was denied.",
        "The claim was denied with reason AUTH_REQUIRED.",
        "The recorded claim status is DENIED.",
        "The denial reason is AUTH_REQUIRED.",
        "The available evidence is consistent with the recorded denial reason.",
        "Review whether prior authorization existed.",
        "Verify whether prior authorization information exists.",
        "Verify authorization information.",
    ],
)
def test_historical_and_advisory_statements_do_not_trigger_rule_d(allowed_text):
    context = _context("claim:CLM-1001")
    brief = _brief(summary=allowed_text)

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.PASSED
    assert not any(issue.rule == "prohibited_authority_language" for issue in result.issues)


# --- H. Post-audit remediation: Rule D precision (false positives / false negatives) -----
#
# An independent source-code audit found the pre-remediation Rule D regex
# had both false positives (flagging safe historical/negated statements)
# and false negatives (missing some genuine authority claims). Each
# `unsafe_text` case below would have been MISSED (no match) and each
# `safe_text` case would have been WRONGLY FLAGGED by
# claims-copilot-final-v1's implementation -- see
# application/brief_validator.py's Rule D docstring for the design that
# fixes both without becoming a general subject/negation grammar.


@pytest.mark.parametrize(
    "safe_text",
    [
        "The claim was denied.",
        "The payer denied the claim.",
        "The insurer denied the claim.",
        "The recorded system denied the claim.",
        "Do not approve the claim.",
        "This system does not approve the claim.",
        "The copilot cannot approve the claim.",
    ],
)
def test_h_safe_historical_and_negated_statements_do_not_trigger_rule_d(safe_text):
    context = _context("claim:CLM-1001")
    brief = _brief(summary=safe_text)

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.PASSED
    assert not any(issue.rule == "prohibited_authority_language" for issue in result.issues)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Approve the claim.",
        "We approve the claim.",
        "This system approves the claim.",
        "The claim should be approved.",
        "We recommend approving the claim.",
        "Pay the claim.",
        "Deny this authorization.",
        "Approve claim CLM-1001.",
        "This system determines medical necessity.",
    ],
)
def test_h_unsafe_authority_statements_trigger_rule_d(unsafe_text):
    context = _context("claim:CLM-1001")
    brief = _brief(summary=unsafe_text)

    result = validate_investigation_brief(brief, context)

    assert result.status == ValidationStatus.FAILED
    assert any(issue.rule == "prohibited_authority_language" for issue in result.issues)


def test_h_known_remaining_limitation_intervening_modal_not_caught():
    """Documented, deliberate gap: Rule D requires the authority-claiming
    subject to DIRECTLY govern the verb (no intervening word) -- an
    intervening modal like "must"/"will"/"should" (attached to a
    first-person subject, not the passive "should be X" shape Rule D does
    catch) breaks that adjacency and is not flagged. This is not a defect
    to silently work around; it is the documented boundary of a
    deliberately narrow, deterministic pattern -- see
    application/brief_validator.py's module docstring "THIS IS NOT" list."""
    context = _context("claim:CLM-1001")
    brief = _brief(summary="We must approve the claim.")

    result = validate_investigation_brief(brief, context)

    assert not any(issue.rule == "prohibited_authority_language" for issue in result.issues)
