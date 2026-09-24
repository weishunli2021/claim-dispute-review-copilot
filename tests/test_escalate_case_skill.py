"""Tests for the escalate_case skill (synthetic only -- no external system)."""

from __future__ import annotations

from skills.base import SkillStatus
from skills.escalate_case import escalate_case


def test_case5_escalation():
    result = escalate_case(
        "CLM-1005",
        "Critical structured evidence is unresolved.",
        missing_information=["benefit", "servicing_provider", "prior_authorization"],
        evidence_summary={"claim_id": "CLM-1005"},
    )
    assert result.status == SkillStatus.COMPLETED
    assert result.evidence["synthetic"] is True
    assert result.evidence["external_system_contacted"] is False
    assert result.missing_information == ["benefit", "servicing_provider", "prior_authorization"]


def test_blank_case_id_is_error():
    result = escalate_case("", "some reason")
    assert result.status == SkillStatus.ERROR


def test_blank_reason_is_error():
    result = escalate_case("CLM-1005", "   ")
    assert result.status == SkillStatus.ERROR


def test_never_connects_to_external_system():
    result = escalate_case("CLM-1001", "test reason")
    assert result.evidence["external_system_contacted"] is False


def test_defaults_are_empty_not_none():
    result = escalate_case("CLM-1001", "test reason")
    assert result.missing_information == []
    assert result.evidence["evidence_summary"] == {}
