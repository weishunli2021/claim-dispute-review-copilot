"""H3 tests: underlying view-model / state-helper logic for the Streamlit
Interview Workbench (application/workbench.py, application/review_packet.py).
Fully offline -- every test uses FakeInvestigationBriefAdapter or a
plain dict standing in for st.session_state, never a real Streamlit
session and never a live model call.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from application.investigation_service import run_investigation
from application.llm_adapter import FakeInvestigationBriefAdapter, LLMProviderError
from application.models import (
    ActionCode,
    Finding,
    GenerationStatus,
    InvestigationBrief,
    SuggestedNextStep,
    ValidationStatus,
)
from application.review_packet import build_needs_review_packet
from application.workbench import (
    CASE_IDS,
    case_catalog,
    get_display_context,
    is_accepted_draft,
    on_case_selected,
    on_question_changed,
    record_review_decision,
    reset_investigation_state,
)

ROOT = Path(__file__).resolve().parents[1]


def _well_formed_brief(*evidence_refs: str) -> InvestigationBrief:
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


# --- A. Case 1 successful state ----------------------------------------------------


def test_case1_successful_state_is_eligible_for_display():
    adapter = FakeInvestigationBriefAdapter(response=_well_formed_brief("claim:CLM-1001"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"
    assert result.generation_status == GenerationStatus.DRAFTED
    assert result.validation_result.status == ValidationStatus.PASSED
    assert is_accepted_draft(result) is True
    assert result.investigation_brief is not None

    context = get_display_context(result)
    assert context is not None
    assert context is result.assembled_context  # reused, not recomputed


# --- B. Case 5 -----------------------------------------------------------------------


def test_case5_needs_review_state_has_no_brief_and_zero_llm_calls():
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation(
        "CLM-1005", "Why was my specialist visit claim denied?", adapter=adapter
    )

    assert result.agent_result.status.value == "NEEDS_REVIEW"
    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None
    assert is_accepted_draft(result) is False
    assert adapter.calls == []  # zero LLM calls

    # A display context is still available for Case 5 (evidence WAS
    # assembled, just judged insufficient) -- reused via assemble_context,
    # not a second evidence-workflow run.
    context = get_display_context(result)
    assert context is not None
    assert result.assembled_context is None  # investigation_service never built one
    assert context.claim_id == "CLM-1005"


def test_case5_review_packet_is_available_and_reuses_existing_evidence():
    result = run_investigation(
        "CLM-1005", "Why was my specialist visit claim denied?", adapter=FakeInvestigationBriefAdapter()
    )

    packet = build_needs_review_packet(result)

    assert packet.status.value == "COMPLETED"
    assert packet.evidence["case_id"] == "CLM-1005"
    assert packet.evidence["external_system_contacted"] is False
    assert packet.evidence["synthetic"] is True
    assert set(packet.missing_information) == set(result.agent_result.missing_information)
    # Reuses the SAME structured facts already gathered -- no re-lookup.
    assert (
        packet.evidence["evidence_summary"]["structured_facts"]["claim"]["claim_id"] == "CLM-1005"
    )


def test_build_needs_review_packet_rejects_non_needs_review_results():
    adapter = FakeInvestigationBriefAdapter(response=_well_formed_brief("claim:CLM-1001"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    with pytest.raises(ValueError):
        build_needs_review_packet(result)


# --- C. Unknown/error state ----------------------------------------------------------


def test_unknown_claim_state_has_no_fabricated_brief():
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation("CLM-9999", "What happened?", adapter=adapter)

    assert result.agent_result.status.value == "ERROR"
    assert result.generation_status == GenerationStatus.NOT_ATTEMPTED
    assert result.investigation_brief is None
    assert is_accepted_draft(result) is False
    assert adapter.calls == []
    assert get_display_context(result) is None  # no EvidencePackage at all


# --- D. Generation failure -------------------------------------------------------------


def test_generation_failure_keeps_evidence_sufficient_with_no_brief():
    adapter = FakeInvestigationBriefAdapter(raises=LLMProviderError("simulated outage"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"
    assert result.generation_status == GenerationStatus.FAILED
    assert result.validation_result.status == ValidationStatus.NOT_RUN
    assert result.investigation_brief is None
    assert is_accepted_draft(result) is False
    # Evidence is still displayable even though generation failed.
    assert get_display_context(result) is not None


# --- E. Validation failure ---------------------------------------------------------


def test_validation_failure_is_not_presented_as_an_accepted_draft():
    # A brief citing a reference that does not exist in the evidence made
    # available -- Rule A fails, so validation_result.status == FAILED.
    bad_brief = _well_formed_brief("claim:CLM-1001", "auth:DOES-NOT-EXIST")
    adapter = FakeInvestigationBriefAdapter(response=bad_brief)
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)

    assert result.generation_status == GenerationStatus.DRAFTED
    assert result.validation_result.status == ValidationStatus.FAILED
    assert result.validation_result.issues
    # The brief object exists (it WAS drafted), but the UI's own gating
    # condition for presenting it as accepted content must be False.
    assert result.investigation_brief is not None
    assert is_accepted_draft(result) is False


# --- F. Stale-state reset ------------------------------------------------------------


def test_changing_case_clears_prior_investigation_result():
    state: dict = {}
    on_case_selected(state, "CLM-1001")
    state["investigation_result"] = "stale-case1-result"
    state["review_decision"] = {"claim_id": "CLM-1001", "decision": "ACCEPTED"}

    on_case_selected(state, "CLM-1005")

    assert "investigation_result" not in state
    assert "review_decision" not in state
    assert state["selected_claim_id"] == "CLM-1005"


def test_changing_question_clears_prior_investigation_result():
    state: dict = {"selected_claim_id": "CLM-1001", "question_text": "original question"}
    state["investigation_result"] = "stale-result-for-original-question"

    on_question_changed(state, "a completely different question")

    assert "investigation_result" not in state
    assert state["question_text"] == "a completely different question"


def test_reset_investigation_state_is_a_no_op_when_nothing_to_clear():
    state: dict = {"selected_claim_id": "CLM-1001"}
    reset_investigation_state(state)  # must not raise
    assert "investigation_result" not in state


# --- G. Case 2 ambiguity ---------------------------------------------------------------


def test_case2_both_authorization_candidates_remain_visible():
    adapter = FakeInvestigationBriefAdapter(
        response=_well_formed_brief("auth:PA-1501", "auth:PA-2001")
    )
    result = run_investigation(
        "CLM-1002", "Is there an approved authorization on file for this MRI?", adapter=adapter
    )

    context = get_display_context(result)
    auth_refs = {r.ref_id for r in context.references if r.kind == "authorization"}
    assert auth_refs == {"auth:PA-1501", "auth:PA-2001"}
    # Nothing in the reference itself (label/detail) declares one "applicable" --
    # each candidate only carries its own recorded status/effective/expiration.
    for ref in context.references:
        if ref.kind == "authorization":
            assert "applicable" not in ref.label.lower()
            assert "applicable" not in ref.detail.lower()


# --- H/I. Human review actions: local only, no source mutation -----------------------


def _data_file_hashes() -> dict[str, str]:
    hashes = {}
    for name in [
        "members.json", "plans.json", "claims.json",
        "benefits.json", "prior_authorizations.json", "providers.json",
    ]:
        path = ROOT / "data" / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def test_accept_investigation_draft_records_only_local_state():
    before_hashes = _data_file_hashes()
    adapter = FakeInvestigationBriefAdapter(response=_well_formed_brief("claim:CLM-1001"))
    result = run_investigation("CLM-1001", "Why was this claim denied?", adapter=adapter)
    before_agent_result = copy.deepcopy(result.agent_result.model_dump())

    state: dict = {}
    entry = record_review_decision(state, result.claim_id, "ACCEPTED")

    assert state["review_decision"] == entry
    assert entry["claim_id"] == "CLM-1001"
    assert entry["decision"] == "ACCEPTED"
    assert "recorded_at" in entry
    # No source data or typed result mutation.
    assert result.agent_result.model_dump() == before_agent_result
    assert _data_file_hashes() == before_hashes


def test_mark_for_further_review_records_only_local_state():
    before_hashes = _data_file_hashes()
    result = run_investigation(
        "CLM-1005", "Why was my specialist visit claim denied?", adapter=FakeInvestigationBriefAdapter()
    )
    before_agent_result = copy.deepcopy(result.agent_result.model_dump())

    state: dict = {}
    entry = record_review_decision(state, result.claim_id, "MARKED_FOR_REVIEW")

    assert state["review_decision"] == entry
    assert entry["decision"] == "MARKED_FOR_REVIEW"
    assert result.agent_result.model_dump() == before_agent_result
    assert _data_file_hashes() == before_hashes


def test_record_review_decision_rejects_unknown_decision():
    with pytest.raises(ValueError):
        record_review_decision({}, "CLM-1001", "SOMETHING_ELSE")


# --- Case catalog / defaults ----------------------------------------------------------


def test_case_catalog_covers_exactly_the_five_known_cases():
    catalog = case_catalog()
    assert [claim_id for claim_id, _ in catalog] == CASE_IDS
    assert len(catalog) == 5
    # Descriptors are derived from real recorded fields, never fabricated.
    labels = dict(catalog)
    assert "DENIED" in labels["CLM-1001"]
    assert "AUTH_REQUIRED" in labels["CLM-1001"]
    assert "PAID" in labels["CLM-1002"]
