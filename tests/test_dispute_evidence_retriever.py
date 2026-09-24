"""Tests for context.dispute_evidence_retriever: the three evidence
adapters (structured, policy, graph) and the full assembly function.

Uses REAL components (tools/, rag/, graph/) against the actual CLM-1001
synthetic dataset for the primary integration checks -- no mocking of the
retrieval stack itself. Controlled fixtures (monkeypatch) are used only
for the specific edge/failure-path tests that real data cannot exercise
(a malformed identifier, a forced retrieval exception, empty-vs-failure).
"""

from __future__ import annotations

import pytest

from context.dispute_evidence_models import EvidenceProvenanceCategory as Provenance
from context.dispute_evidence_models import EvidenceSourceStatus as Status
from context.dispute_evidence_retriever import (
    build_dispute_evidence_package,
    gather_graph_evidence,
    gather_policy_evidence,
    gather_structured_evidence,
)
from dispute_review.comparison import build_claim_snapshot, compare_submission
from dispute_review.models import ComparisonStatus, DisputeSubmission
from dispute_review.presets import build_demo_presets
from tools.case_context import get_case_context

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _clm_1001_case_context():
    return get_case_context("CLM-1001")


# --- full assembly: all three presets, real components -------------------------------------


def test_matching_preset_assembles_full_package():
    case_context = _clm_1001_case_context()
    presets = build_demo_presets(case_context)
    package = build_dispute_evidence_package("CLM-1001", presets.matching)

    assert package.claim_id == "CLM-1001"
    assert len(package.comparison_findings) == 4
    assert [row.status for row in package.comparison_result.rows] == [ComparisonStatus.MATCH] * 4
    assert len(package.recorded_facts) >= 5  # claim, member, plan, servicing+ordering provider
    assert len(package.submitted_fields) > 0
    assert len(package.policy_passages) > 0
    assert len(package.graph_relationships) > 0
    sources = {o.source for o in package.source_outcomes}
    assert {"structured_case_context", "policy", "graph_claim"} <= sources
    # matching preset uses the claim's own provider -- no separate submitted-provider lookup
    assert "submitted_provider_lookup" not in sources
    assert "graph_submitted_provider" not in sources


def test_different_servicing_provider_preset_assembles_full_package():
    case_context = _clm_1001_case_context()
    presets = build_demo_presets(case_context)
    package = build_dispute_evidence_package("CLM-1001", presets.different_servicing_provider)

    by_field = {row.field: row for row in package.comparison_result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.MISMATCH

    sources = {o.source for o in package.source_outcomes}
    assert "submitted_provider_lookup" in sources
    assert "graph_submitted_provider" in sources

    # recorded vs submitted provider separation (see dedicated section below)
    recorded_provider_refs = [r for r in package.recorded_facts if r.source_type == "servicing_provider"]
    submitted_provider_refs = [r for r in package.recorded_facts if r.source_type == "submitted_provider_lookup"]
    assert len(recorded_provider_refs) == 1
    assert len(submitted_provider_refs) == 1
    assert recorded_provider_refs[0].ref_id.startswith("provider:servicing:")
    assert submitted_provider_refs[0].ref_id.startswith("submitted_provider_lookup:")
    assert recorded_provider_refs[0].ref_id != submitted_provider_refs[0].ref_id
    assert recorded_provider_refs[0].provenance == Provenance.RECORDED
    assert submitted_provider_refs[0].provenance == Provenance.RECORDED_VIA_SUBMITTED_LOOKUP

    # missing policy support preserved as a limitation
    assert any("change in servicing provider" in limitation for limitation in package.limitations)


def test_incomplete_preset_assembles_full_package():
    case_context = _clm_1001_case_context()
    presets = build_demo_presets(case_context)
    package = build_dispute_evidence_package("CLM-1001", presets.incomplete)

    by_field = {row.field: row for row in package.comparison_result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.UNKNOWN
    assert by_field["Validity Dates"].status == ComparisonStatus.UNKNOWN

    # no submitted servicing_provider_id -> no submitted-provider lookup at all
    sources = {o.source for o in package.source_outcomes}
    assert "submitted_provider_lookup" not in sources
    assert "graph_submitted_provider" not in sources

    submitted_field_names = {r.ref_id for r in package.submitted_fields}
    assert "submitted:servicing_provider_id" not in submitted_field_names
    assert "submitted:authorization_end_date" not in submitted_field_names
    assert "submitted:member_id" in submitted_field_names


# --- unverified submission provenance -------------------------------------------------------


def test_all_submitted_field_references_are_unverified_provenance():
    case_context = _clm_1001_case_context()
    presets = build_demo_presets(case_context)
    package = build_dispute_evidence_package("CLM-1001", presets.matching)
    assert package.submitted_fields  # non-empty
    for ref in package.submitted_fields:
        assert ref.provenance == Provenance.SUBMITTED_UNVERIFIED
        assert ref.ref_id.startswith("submitted:")


# --- unrelated graph evidence filtered out (real dataset: PRV-1001 is shared -------------
# by CLM-1001 AND CLM-1002; investigating a THIRD claim with a submission that
# names PRV-1001 as its servicing provider must never leak either claim's
# own relationships through that shared provider hub) -----------------------------------


def test_submitted_provider_graph_evidence_excludes_unrelated_claims():
    # CLM-1003's own recorded servicing provider is PRV-1002 -- PRV-1001
    # (shared by CLM-1001 and CLM-1002) is a genuinely different, submitted
    # provider for this claim.
    submission = DisputeSubmission(servicing_provider_id="PRV-1001")
    package = build_dispute_evidence_package("CLM-1003", submission)

    submitted_provider_refs = [r for r in package.graph_relationships if r.source_type == "graph_submitted_provider"]
    assert submitted_provider_refs  # the provider does exist and does have a forward relationship
    for ref in submitted_provider_refs:
        assert ref.detail.startswith("provider:PRV-1001 --")
        # never a reverse edge from some other claim that merely references this provider
        assert "claim:CLM-1001" not in ref.detail
        assert "claim:CLM-1002" not in ref.detail
        assert "SERVICED_BY" not in ref.detail
        assert "ORDERED_BY" not in ref.detail


# --- unknown submitted entity handled without insertion --------------------------------------


def test_unknown_submitted_provider_produces_no_results_and_no_insertion():
    from graph.builder import provider_node_id
    from graph.retriever import _get_graph  # only to assert absence, never mutated

    fake_id = "PRV-DOES-NOT-EXIST"
    submission = DisputeSubmission(servicing_provider_id=fake_id)
    package = build_dispute_evidence_package("CLM-1001", submission)

    outcomes_by_source = {o.source: o for o in package.source_outcomes}
    assert outcomes_by_source["submitted_provider_lookup"].status == Status.SUCCESS_NO_RESULTS
    assert outcomes_by_source["graph_submitted_provider"].status == Status.SUCCESS_NO_RESULTS
    assert not any(r.source_type == "submitted_provider_lookup" for r in package.recorded_facts)
    assert not any(r.source_type == "graph_submitted_provider" for r in package.graph_relationships)
    assert provider_node_id(fake_id) not in _get_graph()


def test_unknown_submitted_authorization_reference_produces_no_results():
    submission = DisputeSubmission(authorization_reference_number="AUTH-DOES-NOT-EXIST")
    package = build_dispute_evidence_package("CLM-1001", submission)
    outcomes_by_source = {o.source: o for o in package.source_outcomes}
    assert outcomes_by_source["submitted_authorization_lookup"].status == Status.SUCCESS_NO_RESULTS
    assert not any(r.source_type == "submitted_authorization_lookup" for r in package.recorded_facts)


# --- missing authorization is never asserted to be nonexistent everywhere ------------------


def test_missing_record_limitation_always_present():
    case_context = _clm_1001_case_context()
    presets = build_demo_presets(case_context)
    for preset_name in ("matching", "different_servicing_provider", "incomplete"):
        package = build_dispute_evidence_package("CLM-1001", getattr(presets, preset_name))
        assert any(
            "not proof that the record does not exist elsewhere" in limitation
            for limitation in package.limitations
        ), preset_name


# --- empty retrieval vs. failure (controlled fixtures) --------------------------------------


def test_policy_empty_result_is_success_no_results_not_failure(monkeypatch):
    import context.dispute_evidence_retriever as module

    monkeypatch.setattr(module, "search_policy", lambda *a, **k: [])
    case_context = _clm_1001_case_context()
    claim_snapshot = build_claim_snapshot(case_context)
    submission = DisputeSubmission(member_id="M-1001")
    comparison_result = compare_submission(claim_snapshot, submission)

    result = gather_policy_evidence(case_context.claim, comparison_result, submission)
    assert result.outcomes[0].status == Status.SUCCESS_NO_RESULTS
    assert result.outcomes[0].status != Status.FAILURE


def test_policy_exception_is_preserved_as_failure_not_empty_success(monkeypatch):
    import context.dispute_evidence_retriever as module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated retrieval outage with a secret token abc123")

    monkeypatch.setattr(module, "search_policy", _boom)
    case_context = _clm_1001_case_context()
    claim_snapshot = build_claim_snapshot(case_context)
    submission = DisputeSubmission(member_id="M-1001")
    comparison_result = compare_submission(claim_snapshot, submission)

    result = gather_policy_evidence(case_context.claim, comparison_result, submission)
    assert result.outcomes[0].status == Status.FAILURE
    assert result.references == []
    # the raw exception text (and anything resembling a secret) is never
    # exposed in the user-facing detail message
    assert "abc123" not in (result.outcomes[0].detail or "")
    assert "RuntimeError" not in (result.outcomes[0].detail or "")


def test_graph_claim_not_found_is_distinct_from_failure(monkeypatch):
    import context.dispute_evidence_retriever as module
    from graph.retriever import NodeNotFoundError

    def _raise_not_found(*args, **kwargs):
        raise NodeNotFoundError("no such node")

    monkeypatch.setattr(module, "get_claim_neighborhood", _raise_not_found)
    submission = DisputeSubmission(member_id="M-1001")
    result = gather_graph_evidence("CLM-1001", submission, "PRV-1001")
    assert result.outcomes[0].source == "graph_claim"
    assert result.outcomes[0].status == Status.SUCCESS_NO_RESULTS


def test_graph_claim_unexpected_exception_is_failure(monkeypatch):
    import context.dispute_evidence_retriever as module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated graph outage")

    monkeypatch.setattr(module, "get_claim_neighborhood", _boom)
    submission = DisputeSubmission(member_id="M-1001")
    result = gather_graph_evidence("CLM-1001", submission, "PRV-1001")
    assert result.outcomes[0].source == "graph_claim"
    assert result.outcomes[0].status == Status.FAILURE
    assert "simulated graph outage" not in (result.outcomes[0].detail or "")


def test_submitted_provider_malformed_id_is_failure_not_crash():
    case_context = _clm_1001_case_context()
    # internal whitespace -- tools.validation.require_identifier rejects this
    submission = DisputeSubmission(servicing_provider_id="PRV 1002")
    result = gather_structured_evidence(case_context, submission)
    outcomes_by_source = {o.source: o for o in result.outcomes}
    assert outcomes_by_source["submitted_provider_lookup"].status == Status.FAILURE


# --- conflict detection: submitted reference resolves to a real but unrelated record -------


def test_submitted_authorization_reference_resolving_to_unrelated_record_flags_conflict():
    # PA-1501 is a real authorization_id, but it belongs to M-1002 / MRI-KNEE,
    # not CLM-1001's member (M-1001).
    submission = DisputeSubmission(authorization_reference_number="PA-1501")
    package = build_dispute_evidence_package("CLM-1001", submission)
    assert package.conflicts
    assert "PA-1501" in package.conflicts[0]
    assert "different case" in package.conflicts[0]
    ref = next(r for r in package.recorded_facts if r.source_type == "submitted_authorization_lookup")
    assert ref.provenance == Provenance.RECORDED_VIA_SUBMITTED_LOOKUP


def test_submitted_authorization_reference_matching_existing_candidate_not_duplicated():
    # CLM-1002's member (M-1002) already has PA-1501/PA-2001 as recorded
    # candidates -- submitting one of those exact ids back should not
    # produce a second, differently-labeled reference for the same record.
    submission = DisputeSubmission(authorization_reference_number="PA-1501")
    package = build_dispute_evidence_package("CLM-1002", submission)
    lookup_refs = [r for r in package.recorded_facts if r.source_type == "submitted_authorization_lookup"]
    assert lookup_refs == []
    candidate_refs = [r for r in package.recorded_facts if r.ref_id == "auth:PA-1501"]
    assert len(candidate_refs) == 1


# --- inputs / DataStore / graph / source files unchanged ------------------------------------


def test_build_dispute_evidence_package_does_not_mutate_shared_state():
    import hashlib
    from pathlib import Path

    from graph.retriever import get_claim_neighborhood
    from tools.data_store import get_data_store

    data_dir = Path(__file__).resolve().parents[1] / "data"
    file_hashes_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir.glob("*.json"))}

    store = get_data_store()
    claims_before = {k: v.model_dump() for k, v in store.claims.items()}
    providers_before = {k: v.model_dump() for k, v in store.providers.items()}
    prior_auths_before = {k: v.model_dump() for k, v in store.prior_authorizations.items()}
    graph_before = get_claim_neighborhood("CLM-1001").model_dump()

    case_context = _clm_1001_case_context()
    presets = build_demo_presets(case_context)
    for name in ("matching", "different_servicing_provider", "incomplete"):
        build_dispute_evidence_package("CLM-1001", getattr(presets, name))

    file_hashes_after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data_dir.glob("*.json"))}
    assert file_hashes_after == file_hashes_before

    store_after = get_data_store()
    assert store_after is store
    assert {k: v.model_dump() for k, v in store_after.claims.items()} == claims_before
    assert {k: v.model_dump() for k, v in store_after.providers.items()} == providers_before
    assert {k: v.model_dump() for k, v in store_after.prior_authorizations.items()} == prior_auths_before
    assert get_claim_neighborhood("CLM-1001").model_dump() == graph_before


def test_unknown_claim_raises_value_error():
    submission = DisputeSubmission(member_id="M-1001")
    with pytest.raises(ValueError):
        build_dispute_evidence_package("CLM-9999", submission)
