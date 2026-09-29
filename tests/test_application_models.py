"""Contract tests for application/models.py: no case-identity or agent-
status field leaks onto InvestigationBrief, and the action vocabulary
stays fixed to the four approved, non-executable codes."""

from __future__ import annotations

from application.models import (
    ActionCode,
    GenerationFailureCategory,
    GenerationStatus,
    InvestigationBrief,
    ValidationStatus,
)


def test_investigation_brief_has_no_case_identity_or_status_field():
    fields = set(InvestigationBrief.model_fields)
    forbidden = {"claim_id", "case_id", "status", "agent_status", "confidence", "confidence_score"}
    assert not (fields & forbidden)


def test_action_vocabulary_is_exactly_the_four_approved_codes():
    assert {c.value for c in ActionCode} == {
        "EXPLAIN_RECORDED_STATUS",
        "VERIFY_AUTHORIZATION_INFORMATION",
        "REQUEST_INFORMATION",
        "HUMAN_REVIEW",
    }


def test_investigation_brief_rejects_unknown_fields():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        InvestigationBrief(
            summary="x",
            findings=[],
            missing_or_conflicting_evidence=[],
            suggested_next_step={
                "action_code": "EXPLAIN_RECORDED_STATUS",
                "rationale": "x",
                "evidence_refs": [],
            },
            confidence=0.9,  # not part of the contract
        )


def test_generation_status_enum_is_the_h2_consolidated_three_values():
    # H2 consolidated the H1 six-value enum into three; which AgentStatus
    # gated a NOT_ATTEMPTED, and which failure kind produced a FAILED, now
    # live on separate fields (agent_result.status,
    # ApplicationResult.generation_failure_category) instead of being
    # baked into this enum's own values -- see GenerationStatus's docstring.
    assert {s.value for s in GenerationStatus} == {"NOT_ATTEMPTED", "DRAFTED", "FAILED"}


def test_generation_failure_category_covers_every_distinguished_failure_kind():
    assert {c.value for c in GenerationFailureCategory} == {
        "CONFIGURATION",
        "PROVIDER",
        "TIMEOUT",
        "STRUCTURED_PARSING",
    }


def test_validation_status_enum_is_not_run_passed_failed():
    assert {s.value for s in ValidationStatus} == {"NOT_RUN", "PASSED", "FAILED"}
