"""Tests for application/investigation_service.py -- the H1/H2 orchestration
boundary. Fully offline: every test injects a FakeInvestigationBriefAdapter
(or none, for the skip paths, which must never reach an adapter at all).

Also covers the H2 status-separation contract: EvidenceStatus
(agent_result.status), GenerationStatus, and ValidationStatus
(validation_result.status) must never collapse into one another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from application.investigation_service import run_investigation
from application.llm_adapter import FakeInvestigationBriefAdapter, LLMProviderError
from application.models import (
    ActionCode,
    Finding,
    GenerationFailureCategory,
    GenerationStatus,
    InvestigationBrief,
    SuggestedNextStep,
    ValidationStatus,
)


def _well_formed_brief(*evidence_refs: str) -> InvestigationBrief:
    """A brief that satisfies every H2 validation rule -- unlike
    FakeInvestigationBriefAdapter's own default canned brief, whose one
    Finding deliberately has no evidence_refs (it exists only to prove no
    live call was made, not to model a realistic well-formed brief)."""
    refs = list(evidence_refs)
    return InvestigationBrief(
        summary="Test summary.",
        findings=[Finding(statement="A well-formed finding.", evidence_refs=refs)],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="Test rationale.",
            evidence_refs=refs,
        ),
    )


def test_run_investigation_executes_evidence_workflow_exactly_once():
    """The application service must call the existing evidence workflow
    exactly once per request -- never re-run investigate_claim/
    hybrid_retriever after the agent already collected evidence.

    Patches the name as agents.nodes actually calls it (`from
    skills.investigate_claim import investigate_claim`, agents/nodes.py:29)
    -- patching skills.investigate_claim.investigate_claim itself would not
    intercept that already-bound reference.
    """
    import agents.nodes as nodes_module

    with patch.object(
        nodes_module,
        "investigate_claim",
        wraps=nodes_module.investigate_claim,
    ) as spy:
        result = run_investigation(
            "CLM-1001", "Why was this claim denied?", adapter=FakeInvestigationBriefAdapter()
        )

    assert spy.call_count == 1
    assert result.generation_status == GenerationStatus.DRAFTED


def test_drafted_case_produces_context_metadata_and_passing_validation():
    adapter = FakeInvestigationBriefAdapter(response=_well_formed_brief("claim:CLM-1001"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    # Case 1 successful: EvidenceStatus=EVIDENCE_SUFFICIENT, GenerationStatus=DRAFTED,
    # ValidationStatus=PASSED -- the three-way separation the H2 status model requires.
    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"
    assert result.generation_status == GenerationStatus.DRAFTED
    assert result.validation_result.status == ValidationStatus.PASSED
    assert result.validation_result.issues == []
    assert result.investigation_brief is not None
    assert result.assembled_context is not None
    assert result.evidence_package is not None
    assert result.generation_metadata is not None
    assert result.generation_metadata.adapter == "fake"
    assert result.generation_metadata.prompt_version == "investigation_brief.v2"
    assert len(adapter.calls) == 1
    # The original AgentResult/EvidencePackage are untouched -- the model
    # never assigns case identity or agent status.
    assert result.claim_id == "CLM-1001"


def test_case5_insufficient_evidence_makes_zero_provider_calls():
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation(
        "CLM-1005", "Why was my specialist visit claim denied?", adapter=adapter
    )

    # Case 5: EvidenceStatus=NEEDS_REVIEW, GenerationStatus=NOT_ATTEMPTED,
    # ValidationStatus=NOT_RUN.
    assert result.agent_result.status.value == "NEEDS_REVIEW"
    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None
    assert adapter.calls == []
    # The original NEEDS_REVIEW result and its missing-information details
    # are returned through the envelope, unmodified.
    assert result.agent_result.missing_information
    assert result.skip_or_error_reason is not None


def test_unknown_claim_makes_zero_provider_calls_and_is_not_relabeled_as_case5():
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation("CLM-9999", "What happened with this claim?", adapter=adapter)

    # Unknown claim: EvidenceStatus=ERROR, GenerationStatus=NOT_ATTEMPTED,
    # ValidationStatus=NOT_RUN. Same GenerationStatus as Case 5, but the
    # underlying AgentStatus (ERROR vs. NEEDS_REVIEW) is never lost or
    # relabeled -- it's still exactly what agent_result.status says.
    assert result.agent_result.status.value == "ERROR"
    assert result.agent_result.status.value != "NEEDS_REVIEW"
    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None
    assert adapter.calls == []
    assert result.evidence_package is None


def test_invalid_claim_id_format_makes_zero_provider_calls():
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation("bad claim id", "What happened?", adapter=adapter)

    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert adapter.calls == []


def test_provider_failure_is_a_separate_status_and_evidence_is_retained():
    adapter = FakeInvestigationBriefAdapter(raises=LLMProviderError("simulated outage"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    # Model/provider failure after sufficient evidence: EvidenceStatus stays
    # EVIDENCE_SUFFICIENT, GenerationStatus=FAILED, ValidationStatus=NOT_RUN.
    assert result.generation_status == GenerationStatus.FAILED
    assert result.generation_failure_category == GenerationFailureCategory.PROVIDER
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None
    assert result.skip_or_error_reason == "simulated outage"
    # Original evidence is retained even though generation failed.
    assert result.evidence_package is not None
    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"
    assert len(adapter.calls) == 1  # one attempt, no retry


def test_missing_configuration_is_a_clear_application_error(monkeypatch):
    from application.llm_adapter import OpenAIInvestigationBriefAdapter

    # Set to "" rather than delenv(): load_llm_config() calls load_dotenv(),
    # which repopulates a deleted variable from a real local .env file but
    # never overrides one already present (even blank) -- see the matching
    # comment in tests/test_application_llm_adapter.py.
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")

    result = run_investigation(
        "CLM-1001", "Why was this claim denied?", adapter=OpenAIInvestigationBriefAdapter()
    )

    assert result.generation_status == GenerationStatus.FAILED
    assert result.generation_failure_category == GenerationFailureCategory.CONFIGURATION
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert "OPENAI_API_KEY" in result.skip_or_error_reason
    assert result.evidence_package is not None  # evidence work still happened
    assert result.investigation_brief is None  # no fabricated brief


def test_structured_parsing_failure_is_a_separate_category(monkeypatch):
    from application.llm_adapter import LLMOutputError

    adapter = FakeInvestigationBriefAdapter(raises=LLMOutputError("model refused"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    assert result.generation_status == GenerationStatus.FAILED
    assert result.generation_failure_category == GenerationFailureCategory.STRUCTURED_PARSING
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None
    assert len(adapter.calls) == 1


def test_real_adapter_pydantic_validation_error_maps_to_structured_parsing_end_to_end(monkeypatch):
    """Post-audit remediation regression test. Unlike
    test_structured_parsing_failure_is_a_separate_category above (which
    injects LLMOutputError directly via FakeInvestigationBriefAdapter),
    this test drives the REAL OpenAIInvestigationBriefAdapter with only its
    network transport faked, so the fake transport's responses.parse(...)
    itself raises a real pydantic.ValidationError -- exercising the actual
    adapter boundary (application/llm_adapter.py) that must catch it and
    convert it to LLMOutputError, not just the downstream mapping that was
    already correct. No network call occurs."""
    import types

    from application.llm_adapter import OpenAIInvestigationBriefAdapter

    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test-only")
    monkeypatch.setenv("LLM_MODEL", "fake-model-for-test-only")

    adapter = OpenAIInvestigationBriefAdapter()

    class _FakeResponses:
        def parse(self, **kwargs):
            # Real SDK behavior being simulated: constructing InvestigationBrief
            # from the model's JSON raises pydantic.ValidationError because an
            # enum field doesn't match any allowed value.
            InvestigationBrief(
                summary="ok",
                findings=[Finding(statement="ok", evidence_refs=[])],
                missing_or_conflicting_evidence=[],
                suggested_next_step=SuggestedNextStep(
                    action_code="NOT_A_REAL_ACTION_CODE",
                    rationale="ok",
                    evidence_refs=[],
                ),
            )

    adapter._client = types.SimpleNamespace(responses=_FakeResponses())
    adapter.model_name = "fake-model-for-test-only"

    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    assert result.generation_status == GenerationStatus.FAILED
    assert result.generation_failure_category == GenerationFailureCategory.STRUCTURED_PARSING
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None  # no fabricated brief
    assert result.evidence_package is not None  # evidence work still happened


def test_timeout_failure_is_a_separate_category_from_generic_provider_error():
    from application.llm_adapter import LLMTimeoutError

    adapter = FakeInvestigationBriefAdapter(raises=LLMTimeoutError("timed out after 30s"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    assert result.generation_status == GenerationStatus.FAILED
    assert result.generation_failure_category == GenerationFailureCategory.TIMEOUT
    assert result.generation_failure_category != GenerationFailureCategory.PROVIDER
    assert result.validation_result.status == ValidationStatus.NOT_RUN


def test_existing_skills_and_agent_run_without_api_credentials(monkeypatch):
    """NEEDS_REVIEW/ERROR paths must not require OPENAI_API_KEY/LLM_MODEL
    at all -- they never touch the adapter."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    result = run_investigation("CLM-1005", "Why was my specialist visit claim denied?")
    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED

    result = run_investigation("CLM-9999", "What happened?")
    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED


def test_case2_ambiguity_preserved_through_the_full_h2_pipeline():
    """H1's Case-2 authorization-candidate ambiguity must survive H2's
    validation pass unchanged -- H2 must not deterministically declare
    which of PA-1501/PA-2001 applies."""
    adapter = FakeInvestigationBriefAdapter(
        response=_well_formed_brief("auth:PA-1501", "auth:PA-2001")
    )
    result = run_investigation(
        "CLM-1002", "Is there an approved authorization on file for this MRI?", adapter=adapter
    )

    auth_refs = {r.ref_id for r in result.assembled_context.references if r.kind == "authorization"}
    assert auth_refs == {"auth:PA-1501", "auth:PA-2001"}
    # Nothing in the H2 validator resolves or removes either candidate --
    # validation only checks the brief's own citations against this same
    # reference set, never re-derives which authorization "applies."
    assert result.generation_status == GenerationStatus.DRAFTED
    assert result.validation_result.status == ValidationStatus.PASSED


@pytest.mark.parametrize(
    "claim_id,query,expected_status",
    [
        ("CLM-1001", "Why was this claim denied?", GenerationStatus.DRAFTED),
        ("CLM-1002", "Was this claim paid correctly?", GenerationStatus.DRAFTED),
        ("CLM-1003", "Why was my lab claim denied?", GenerationStatus.DRAFTED),
        ("CLM-1004", "Why was my physical therapy claim denied?", GenerationStatus.DRAFTED),
        ("CLM-1005", "Why was my specialist visit claim denied?", GenerationStatus.NOT_ATTEMPTED),
        ("CLM-9999", "What happened with this claim?", GenerationStatus.NOT_ATTEMPTED),
    ],
)
def test_all_five_verified_cases_plus_unknown_claim_route_correctly(claim_id, query, expected_status):
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation(claim_id, query, adapter=adapter)
    assert result.generation_status == expected_status
