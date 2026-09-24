"""Tests for the deterministic SkillRegistry."""

from __future__ import annotations

import pytest

from skills.base import RiskLevel, SkillMetadata, SkillResult, SkillStatus
from skills.registry import DuplicateSkillError, SkillNotFoundError, SkillRegistry


def _metadata(name: str = "example", version: str = "1.0.0") -> SkillMetadata:
    return SkillMetadata(
        name=name,
        version=version,
        description="An example skill.",
        capabilities=[],
        allowed_tools=[],
        risk_level=RiskLevel.LOW,
    )


def _func(*args, **kwargs) -> SkillResult:
    return SkillResult(skill_name="example", skill_version="1.0.0", status=SkillStatus.COMPLETED)


def test_register_and_retrieve_skill():
    registry = SkillRegistry()
    metadata = _metadata()
    registry.register(metadata, _func)

    retrieved_metadata, retrieved_func = registry.get("example")
    assert retrieved_metadata == metadata
    assert retrieved_func is _func


def test_retrieve_unknown_skill_raises():
    registry = SkillRegistry()
    with pytest.raises(SkillNotFoundError):
        registry.get("does-not-exist")


def test_retrieve_unknown_version_raises():
    registry = SkillRegistry()
    registry.register(_metadata(version="1.0.0"), _func)
    with pytest.raises(SkillNotFoundError):
        registry.get("example", version="2.0.0")


def test_duplicate_name_and_version_rejected():
    registry = SkillRegistry()
    registry.register(_metadata(), _func)
    with pytest.raises(DuplicateSkillError):
        registry.register(_metadata(), _func)


def test_same_name_different_version_is_allowed():
    registry = SkillRegistry()
    registry.register(_metadata(version="1.0.0"), _func)
    registry.register(_metadata(version="1.1.0"), _func)  # must not raise

    metadata, _ = registry.get("example")  # defaults to the highest version
    assert metadata.version == "1.1.0"

    metadata_v1, _ = registry.get("example", version="1.0.0")
    assert metadata_v1.version == "1.0.0"


def test_list_skills_returns_stable_sorted_order():
    registry = SkillRegistry()
    registry.register(_metadata(name="zeta"), _func)
    registry.register(_metadata(name="alpha"), _func)
    registry.register(_metadata(name="alpha", version="1.1.0"), _func)

    listed = registry.list_skills()
    assert [(m.name, m.version) for m in listed] == [
        ("alpha", "1.0.0"),
        ("alpha", "1.1.0"),
        ("zeta", "1.0.0"),
    ]


def test_build_default_registry_has_all_four_skills():
    from skills import build_default_registry

    registry = build_default_registry()
    names = {m.name for m in registry.list_skills()}
    assert names == {
        "investigate_claim",
        "explain_benefit",
        "check_prior_authorization",
        "escalate_case",
    }
    for metadata in registry.list_skills():
        assert metadata.version == "1.0.0"
