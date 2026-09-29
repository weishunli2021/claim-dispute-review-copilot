"""Tests for the shared Skill contract types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from skills.base import RiskLevel, SkillInput, SkillMetadata, SkillResult, SkillStatus


def test_skill_status_is_a_controlled_enum():
    values = {member.value for member in SkillStatus}
    assert values == {"COMPLETED", "INSUFFICIENT_EVIDENCE", "NOT_FOUND", "ERROR"}


def test_skill_metadata_has_required_fields():
    metadata = SkillMetadata(
        name="example",
        version="1.0.0",
        description="An example skill.",
        capabilities=["example_capability"],
        allowed_tools=["tools.example_tool"],
        risk_level=RiskLevel.LOW,
    )
    assert metadata.name == "example"
    assert metadata.version == "1.0.0"
    assert metadata.risk_level == RiskLevel.LOW
    assert metadata.capabilities == ["example_capability"]


def test_skill_result_has_no_final_answer_field():
    field_names = set(SkillResult.model_fields)
    for forbidden in ("answer", "final_answer", "response", "explanation", "resolution"):
        assert forbidden not in field_names
    assert field_names == {
        "skill_name",
        "skill_version",
        "status",
        "evidence",
        "missing_information",
        "next_capability",
        "error",
    }


def test_skill_result_constructs_with_minimal_fields():
    result = SkillResult(skill_name="x", skill_version="1.0.0", status=SkillStatus.COMPLETED)
    assert result.evidence == {}
    assert result.missing_information == []
    assert result.next_capability is None
    assert result.error is None


def test_skill_input_rejects_unknown_fields():
    class ExampleInput(SkillInput):
        claim_id: str

    with pytest.raises(ValidationError):
        ExampleInput(claim_id="CLM-1001", unexpected_field="oops")
