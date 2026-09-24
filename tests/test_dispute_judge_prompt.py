"""Tests for prompts/dispute_judge_prompt.py.

Fixes the judge-side instance of the same root cause already fixed for
brief generation (see application/dispute_brief_validator.py's live-check
history and prompts/dispute_brief_prompt.py): a missing-evidence CATEGORY
LABEL (e.g. "prior_authorization") is not a citable reference id, but
without an explicit instruction the judge can mistake it for one and
invent a fabricated evidence_refs entry -- caught downstream by
application/dispute_judge.py's UNKNOWN_REFERENCE check (never surfaced as
a fabricated score), but the goal here is to reduce how often that
happens in the first place, exactly mirroring the brief-side fix.

Uses a REAL DisputeGenerationContext (CLM-1001, matching preset) so the
"prior_authorization" missing-evidence label actually appears in the
rendered prompt, the same way it did in the live smoke check.
"""

from __future__ import annotations

from application.dispute_context import assemble_dispute_context
from application.dispute_models import DisputeBrief
from application.dispute_workflow import assess_evidence_gate
from application.models import ActionCode, Finding, SuggestedNextStep
from context.dispute_evidence_models import DisputeEvidencePackage
from dispute_review.presets import build_demo_presets
from prompts.dispute_judge_prompt import (
    JUDGE_PROMPT_VERSION,
    JUDGE_SYSTEM_PROMPT,
    render_judge_user_prompt,
)
from skills.investigate_dispute import investigate_dispute
from tools.case_context import get_case_context


def _real_context():
    case_context = get_case_context("CLM-1001")
    presets = build_demo_presets(case_context)
    result = investigate_dispute("CLM-1001", presets.matching)
    package = DisputeEvidencePackage.model_validate(result.evidence["dispute_evidence_package"])
    gate = assess_evidence_gate(package)
    return assemble_dispute_context(package, gate)


def _well_formed_brief(context) -> DisputeBrief:
    real_ref = context.references[0].ref_id
    return DisputeBrief(
        summary="Summary.",
        findings=[Finding(statement="A grounded fact.", evidence_refs=[real_ref])],
        missing_or_conflicting_evidence=["prior_authorization: No matching prior authorization record was retrieved."],
        verification_questions=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION, rationale="Because.", evidence_refs=[real_ref]
        ),
    )


def test_prompt_version_bumped_for_the_citation_instruction_change():
    assert JUDGE_PROMPT_VERSION == "dispute_judge.v2"


def test_system_prompt_states_the_evidence_refs_rule():
    assert "EVIDENCE_REFS RULE" in JUDGE_SYSTEM_PROMPT
    assert "NOT reference ids" in JUDGE_SYSTEM_PROMPT
    assert "never invent a reference id" in JUDGE_SYSTEM_PROMPT.lower()
    assert "prior_authorization" in JUDGE_SYSTEM_PROMPT  # the exact observed live example, named explicitly


def test_system_prompt_permits_empty_evidence_refs_for_absence_based_critique():
    assert "leave that dimension's evidence_refs" in JUDGE_SYSTEM_PROMPT
    assert "EMPTY" in JUDGE_SYSTEM_PROMPT


def test_rendered_prompt_contains_the_real_prior_authorization_gap_unbracketed():
    context = _real_context()
    brief = _well_formed_brief(context)
    rendered = render_judge_user_prompt(context, brief)

    assert "prior_authorization" in rendered  # the real, live-observed missing-evidence category
    assert "[prior_authorization]" not in rendered  # never presented in the bracketed-citation shape


def test_rendered_prompt_labels_missing_evidence_conflicts_limitations_as_not_reference_ids():
    context = _real_context()
    brief = _well_formed_brief(context)
    rendered = render_judge_user_prompt(context, brief)

    assert "MISSING EVIDENCE (gap descriptions in prose, NOT reference ids" in rendered
    assert "CONFLICTS (prose, not reference ids)" in rendered
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
