"""Tests for prompts/dispute_judge_prompt.py -- the billing-correction
scenario's judge prompt. Uses a REAL DisputeGenerationContext (the
scenario's own fixed claim + default example) so the rendered prompt
reflects actual evidence, never a hand-typed fake context shape.
"""

from __future__ import annotations

from application.dispute_context import assemble_dispute_context
from application.dispute_models import DisputeBrief
from application.dispute_workflow import assess_evidence_gate
from application.models import ActionCode, Finding, SuggestedNextStep
from context.dispute_evidence_models import DisputeEvidencePackage
from dispute_review.billing_fixtures import BILLING_CLAIM_ID
from dispute_review.presets import billing_correction_example_preset
from prompts.dispute_judge_prompt import (
    JUDGE_PROMPT_VERSION,
    JUDGE_SYSTEM_PROMPT,
    render_judge_user_prompt,
)
from skills.investigate_dispute import investigate_dispute


def _real_context():
    result = investigate_dispute(BILLING_CLAIM_ID, billing_correction_example_preset())
    package = DisputeEvidencePackage.model_validate(result.evidence["dispute_evidence_package"])
    gate = assess_evidence_gate(package)
    return assemble_dispute_context(package, gate)


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


def test_prompt_version_bumped_for_the_billing_scenario_rewrite():
    assert JUDGE_PROMPT_VERSION == "dispute_judge.v6"


def test_system_prompt_states_the_evidence_refs_rule():
    assert "EVIDENCE_REFS RULE" in JUDGE_SYSTEM_PROMPT
    assert "NOT reference ids" in JUDGE_SYSTEM_PROMPT
    assert "never invent a reference id" in JUDGE_SYSTEM_PROMPT.lower()


def test_system_prompt_permits_empty_evidence_refs_for_absence_based_critique():
    assert "leave that dimension's evidence_refs" in JUDGE_SYSTEM_PROMPT
    assert "EMPTY" in JUDGE_SYSTEM_PROMPT


def test_system_prompt_covers_billing_dimensions_not_authorization_ones():
    assert "AUTHORIZATION_SOURCE" not in JUDGE_SYSTEM_PROMPT
    assert "authorization-source" not in JUDGE_SYSTEM_PROMPT.lower()
    assert "ORIGINAL-VS-PROPOSED DISTINCTION" in JUDGE_SYSTEM_PROMPT
    assert "submitting/editing a claim" in JUDGE_SYSTEM_PROMPT.lower() or "submitting" in JUDGE_SYSTEM_PROMPT.lower()


def test_rendered_prompt_labels_missing_evidence_conflicts_limitations_as_not_reference_ids():
    context = _real_context()
    brief = _well_formed_brief(context)
    rendered = render_judge_user_prompt(context, brief)

    assert "MISSING EVIDENCE (gap descriptions in prose, NOT reference ids" in rendered
    assert "CONFLICTS (none identified across the billing-correction comparison; prose, not reference ids):" in rendered
    assert "LIMITATIONS (prose, not reference ids)" in rendered
    assert "MISSING/CONFLICTING EVIDENCE LISTED BY THE BRIEF (prose from the brief being reviewed" in rendered


def test_rendered_prompt_marks_evidence_references_as_the_only_valid_ids():
    context = _real_context()
    brief = _well_formed_brief(context)
    rendered = render_judge_user_prompt(context, brief)

    assert "EVIDENCE REFERENCES (the ONLY valid evidence_refs ids" in rendered
    real_ref = context.references[0].ref_id
    assert f"[{real_ref}]" in rendered  # real ids are still shown in the bracketed citation shape


def test_rendered_prompt_warns_the_brief_text_is_content_not_an_id_source():
    context = _real_context()
    brief = _well_formed_brief(context)
    rendered = render_judge_user_prompt(context, brief)

    assert "CONTENT UNDER REVIEW, not an authoritative source of reference" in rendered


def test_rendered_prompt_includes_independent_support_record_references():
    context = _real_context()
    brief = _well_formed_brief(context)
    rendered = render_judge_user_prompt(context, brief)

    assert "support:BILLREC-001" in rendered
    assert "INDEPENDENT_SUPPORTING_RECORD" in rendered
