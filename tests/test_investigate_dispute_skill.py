"""Tests for skills.investigate_dispute: the SkillResult framing around
context.dispute_evidence_retriever.build_dispute_evidence_package, plus
fail-fast proof that this skill never reaches the OpenAI client or the
existing LLM investigation pipeline.
"""

from __future__ import annotations

import pytest

from dispute_review.models import ComparisonStatus, DisputeSubmission
from dispute_review.presets import build_demo_presets
from skills.base import SkillStatus
from skills.investigate_dispute import METADATA, investigate_dispute
from tools.case_context import get_case_context


def _clm_1001_presets():
    return build_demo_presets(get_case_context("CLM-1001"))


# --- basic contract -------------------------------------------------------------------------


def test_metadata_shape():
    assert METADATA.name == "investigate_dispute"
    assert METADATA.version == "1.0.0"
    assert "context.dispute_evidence_retriever.build_dispute_evidence_package" in METADATA.allowed_tools


def test_unknown_claim_returns_not_found():
    result = investigate_dispute("CLM-9999", DisputeSubmission(member_id="M-1001"))
    assert result.status == SkillStatus.NOT_FOUND
    assert "CLM-9999" in result.error
    assert result.evidence == {}


def test_unexpected_exception_returns_error_not_a_crash(monkeypatch):
    import skills.investigate_dispute as module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(module, "build_dispute_evidence_package", _boom)
    result = investigate_dispute("CLM-1001", DisputeSubmission(member_id="M-1001"))
    assert result.status == SkillStatus.ERROR
    assert result.error
    assert "simulated unexpected failure" in result.error  # skill-boundary message, not hidden


# --- all three presets, COMPLETED with an evidence package ----------------------------------


@pytest.mark.parametrize("preset_name", ["matching", "different_servicing_provider", "incomplete"])
def test_all_three_presets_complete_with_evidence_package(preset_name):
    presets = _clm_1001_presets()
    submission = getattr(presets, preset_name)
    result = investigate_dispute("CLM-1001", submission)

    assert result.status == SkillStatus.COMPLETED
    assert result.error is None
    assert "dispute_evidence_package" in result.evidence
    package = result.evidence["dispute_evidence_package"]
    assert package["claim_id"] == "CLM-1001"
    assert len(package["comparison_findings"]) == 4


def test_no_blanket_sufficiency_rule_status_is_always_completed_when_claim_found():
    # Even the "incomplete" preset (2 Unknown comparison rows) still
    # produces SkillStatus.COMPLETED -- this skill does not compute any
    # evidence-sufficiency verdict itself (that is explicitly deferred to
    # Module 6B); it only reports what it found.
    presets = _clm_1001_presets()
    result = investigate_dispute("CLM-1001", presets.incomplete)
    assert result.status == SkillStatus.COMPLETED
    package = result.evidence["dispute_evidence_package"]
    statuses = {row["status"] for row in package["comparison_result"]["rows"]}
    assert ComparisonStatus.UNKNOWN.value in statuses


def test_missing_information_reflects_structured_gaps():
    presets = _clm_1001_presets()
    result = investigate_dispute("CLM-1001", presets.matching)
    assert any("prior_authorization" in item for item in result.missing_information)


# --- actual invocation of all three evidence adapters ----------------------------------------


def test_skill_actually_invokes_all_three_evidence_adapters(monkeypatch):
    import context.dispute_evidence_retriever as retriever_module

    calls: dict[str, int] = {"structured": 0, "policy": 0, "graph": 0}

    real_structured = retriever_module.gather_structured_evidence
    real_policy = retriever_module.gather_policy_evidence
    real_graph = retriever_module.gather_graph_evidence

    def spy_structured(*args, **kwargs):
        calls["structured"] += 1
        return real_structured(*args, **kwargs)

    def spy_policy(*args, **kwargs):
        calls["policy"] += 1
        return real_policy(*args, **kwargs)

    def spy_graph(*args, **kwargs):
        calls["graph"] += 1
        return real_graph(*args, **kwargs)

    monkeypatch.setattr(retriever_module, "gather_structured_evidence", spy_structured)
    monkeypatch.setattr(retriever_module, "gather_policy_evidence", spy_policy)
    monkeypatch.setattr(retriever_module, "gather_graph_evidence", spy_graph)

    presets = _clm_1001_presets()
    result = investigate_dispute("CLM-1001", presets.matching)

    assert result.status == SkillStatus.COMPLETED
    assert calls == {"structured": 1, "policy": 1, "graph": 1}


# --- zero model calls: fail-fast spies at the actual boundaries ------------------------------


def test_investigate_dispute_never_calls_openai_client_or_investigation_service(monkeypatch):
    """Same fail-fast-spy approach as
    tests/test_dispute_review_ui.py::test_dispute_review_never_calls_openai_client_or_investigation_service
    -- patches the ONLY two boundaries that matter and proves neither is
    reached while running all three presets through this skill."""
    import openai as openai_module
    import application.investigation_service as investigation_service_module

    def _fail_openai_construction(*args, **kwargs):
        raise AssertionError("openai.OpenAI must never be constructed by investigate_dispute")

    def _fail_run_investigation(*args, **kwargs):
        raise AssertionError("run_investigation must never be called by investigate_dispute")

    monkeypatch.setattr(openai_module, "OpenAI", _fail_openai_construction)
    monkeypatch.setattr(investigation_service_module, "run_investigation", _fail_run_investigation)

    presets = _clm_1001_presets()
    for preset_name in ("matching", "different_servicing_provider", "incomplete"):
        result = investigate_dispute("CLM-1001", getattr(presets, preset_name))
        assert result.status == SkillStatus.COMPLETED
