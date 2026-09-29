"""Tests for the explain_benefit skill."""

from __future__ import annotations

from skills.base import SkillStatus
from skills.explain_benefit import explain_benefit


def test_covered_benefit_completed():
    result = explain_benefit("M-1001", "MRI-KNEE")
    assert result.status == SkillStatus.COMPLETED
    assert result.evidence["benefit"]["covered"] is True
    assert result.missing_information == []
    assert result.next_capability is None


def test_not_covered_benefit_still_completed():
    # A present-but-not-covered benefit record is NOT missing evidence.
    result = explain_benefit("M-1003", "LAB-BASIC")
    assert result.status == SkillStatus.COMPLETED
    assert result.evidence["benefit"]["covered"] is False
    assert result.missing_information == []


def test_missing_benefit_record_reported_not_inferred():
    result = explain_benefit("M-1005", "SPECIALIST-VISIT")
    assert result.status == SkillStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence["benefit"] is None
    assert "benefit" in result.missing_information
    assert result.next_capability == "escalate_case"


def test_unknown_member_not_found():
    result = explain_benefit("M-9999", "MRI-KNEE")
    assert result.status == SkillStatus.NOT_FOUND


def test_invalid_member_id_error():
    result = explain_benefit("bad member id", "MRI-KNEE")
    assert result.status == SkillStatus.ERROR
