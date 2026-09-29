"""Tests for dispute_review/authorization_source_lookup.py (Module 2, v2).

Every test here uses an ISOLATED, tmp_path-scoped fixture file (mirroring
tests/test_data_store.py's own `DataStore(data_dir=tmp_path)` pattern) --
never the real data/authorization_source_records.json singleton, and never
tools.data_store.DataStore/get_data_store(). This both keeps tests
independent of the real fixture's exact contents and directly demonstrates
the isolation Module 2 requires: nothing here can affect, or is affected
by, the baseline dataset.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from dispute_review.authorization_source_lookup import (
    AuthorizationSourceLoadError,
    AuthorizationSourceStore,
    get_authorization_source_record_by_id,
    get_authorization_source_store,
    lookup_authorization_source_by_reference,
)
from dispute_review.authorization_source_models import AuthorizationSourceLookupStatus
from dispute_review.models import BillingCorrectionSubmission
from tools.data_store import get_data_store
from tools.prior_auth_tool import get_prior_authorization_by_id, get_prior_authorizations


def _write_fixture(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "authorization_source_records.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def _store(tmp_path: Path, records: list[dict]) -> AuthorizationSourceStore:
    return AuthorizationSourceStore(path=_write_fixture(tmp_path, records))


_SINGLE_RECORD = {
    "source_record_id": "AUTHSRC-T1",
    "authorization_reference_number": "REF-SINGLE",
    "member_id": "M-T1",
    "service_code": "SVC-T1",
    "servicing_provider_id": "PRV-T1",
    "status": "APPROVED",
    "record_version": 1,
    "authorized_start_date": "2026-01-01",
    "authorized_end_date": "2026-06-30",
}


# --- store loading -------------------------------------------------------------------------


def test_store_loads_valid_fixture(tmp_path):
    store = _store(tmp_path, [_SINGLE_RECORD])
    assert store.records_by_id["AUTHSRC-T1"].status == "APPROVED"
    assert store.records_by_reference["REF-SINGLE"][0].source_record_id == "AUTHSRC-T1"


def test_store_rejects_duplicate_source_record_id(tmp_path):
    with pytest.raises(AuthorizationSourceLoadError):
        _store(tmp_path, [_SINGLE_RECORD, _SINGLE_RECORD])


def test_store_rejects_dangling_amends_link(tmp_path):
    dangling = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-T2", record_version=2, amends_source_record_id="AUTHSRC-DOES-NOT-EXIST")
    with pytest.raises(AuthorizationSourceLoadError):
        _store(tmp_path, [dangling])


def test_store_rejects_missing_file(tmp_path):
    with pytest.raises(AuthorizationSourceLoadError):
        AuthorizationSourceStore(path=tmp_path / "does_not_exist.json")


def test_store_rejects_malformed_record(tmp_path):
    bad = dict(_SINGLE_RECORD)
    del bad["status"]  # required field missing
    with pytest.raises(AuthorizationSourceLoadError):
        _store(tmp_path, [bad])


# --- lookup: FOUND / NOT_FOUND ---------------------------------------------------------------


def test_lookup_not_found_is_explicit(tmp_path):
    store = _store(tmp_path, [_SINGLE_RECORD])
    result = lookup_authorization_source_by_reference("REF-DOES-NOT-EXIST", store=store)
    assert result.status == AuthorizationSourceLookupStatus.NOT_FOUND
    assert result.record is None
    assert result.candidates == []


def test_lookup_found_single_version(tmp_path):
    store = _store(tmp_path, [_SINGLE_RECORD])
    result = lookup_authorization_source_by_reference("REF-SINGLE", store=store)
    assert result.status == AuthorizationSourceLookupStatus.FOUND
    assert result.record.source_record_id == "AUTHSRC-T1"
    assert result.version_history == [result.record]


def test_lookup_requires_a_nonblank_reference_number():
    with pytest.raises(ValueError):
        lookup_authorization_source_by_reference("")
    with pytest.raises(ValueError):
        lookup_authorization_source_by_reference(None)  # type: ignore[arg-type]


# --- lookup: AMBIGUOUS -----------------------------------------------------------------------


def test_lookup_ambiguous_when_two_independent_chains_share_a_reference(tmp_path):
    record_a = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-A", authorization_reference_number="SHARED-REF")
    record_b = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-B", authorization_reference_number="SHARED-REF", member_id="M-OTHER")
    store = _store(tmp_path, [record_a, record_b])

    result = lookup_authorization_source_by_reference("SHARED-REF", store=store)
    assert result.status == AuthorizationSourceLookupStatus.AMBIGUOUS
    assert result.record is None  # nothing silently selected
    assert {c.source_record_id for c in result.candidates} == {"AUTHSRC-A", "AUTHSRC-B"}


def test_lookup_ambiguous_for_a_branching_chain_never_silently_picks_highest_version(tmp_path):
    """Module 3 Step 2's explicit gap check: one parent amended by TWO
    different children (a fork) must never resolve to 'latest wins' --
    it is a conflicting/inconsistent chain and must come back AMBIGUOUS,
    with every version surfaced, none selected."""
    root = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-ROOT", authorization_reference_number="FORK-REF")
    child_a = dict(root, source_record_id="AUTHSRC-CHILD-A", record_version=2, amends_source_record_id="AUTHSRC-ROOT")
    child_b = dict(root, source_record_id="AUTHSRC-CHILD-B", record_version=3, amends_source_record_id="AUTHSRC-ROOT")
    store = _store(tmp_path, [root, child_a, child_b])

    result = lookup_authorization_source_by_reference("FORK-REF", store=store)
    assert result.status == AuthorizationSourceLookupStatus.AMBIGUOUS
    assert result.record is None
    assert {c.source_record_id for c in result.candidates} == {"AUTHSRC-ROOT", "AUTHSRC-CHILD-A", "AUTHSRC-CHILD-B"}


def test_store_rejects_a_two_cycle_amendment_chain(tmp_path):
    """A must not be constructible as amending B while B amends A -- caught
    at load time by the strictly-increasing record_version integrity
    check, not silently tolerated by the lookup's cycle guard."""
    a = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-CYCLE-A", record_version=2, amends_source_record_id="AUTHSRC-CYCLE-B")
    b = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-CYCLE-B", record_version=1, amends_source_record_id="AUTHSRC-CYCLE-A")
    with pytest.raises(AuthorizationSourceLoadError):
        _store(tmp_path, [a, b])


def test_store_rejects_amendment_with_non_increasing_record_version(tmp_path):
    parent = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-P1", record_version=2)
    child_same_version = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-P2", record_version=2, amends_source_record_id="AUTHSRC-P1")
    with pytest.raises(AuthorizationSourceLoadError):
        _store(tmp_path, [parent, child_same_version])


def test_lookup_is_not_ambiguous_for_a_normal_two_version_amendment_chain(tmp_path):
    v1 = dict(_SINGLE_RECORD, source_record_id="AUTHSRC-C1", authorization_reference_number="CHAIN-REF")
    v2 = dict(
        v1,
        source_record_id="AUTHSRC-C2",
        record_version=2,
        amends_source_record_id="AUTHSRC-C1",
    )
    store = _store(tmp_path, [v1, v2])

    result = lookup_authorization_source_by_reference("CHAIN-REF", store=store)
    # A single, traceable amendment history is NOT the same as ambiguity.
    assert result.status == AuthorizationSourceLookupStatus.FOUND
    assert result.record.source_record_id == "AUTHSRC-C2"


# --- lookup: ERROR, distinguishable from NOT_FOUND --------------------------------------------


class _BrokenStore:
    """Simulates an unexpected failure reading the underlying fixture --
    NOT a normal empty result."""

    @property
    def records_by_reference(self):
        raise RuntimeError("simulated fixture-access failure")


def test_lookup_error_is_distinguishable_from_not_found():
    result = lookup_authorization_source_by_reference("ANY-REFERENCE", store=_BrokenStore())
    assert result.status == AuthorizationSourceLookupStatus.ERROR
    assert result.status != AuthorizationSourceLookupStatus.NOT_FOUND
    assert "unexpectedly" in result.detail.lower()


# --- get_authorization_source_record_by_id ----------------------------------------------------


def test_get_by_id_returns_none_when_absent(tmp_path):
    store = _store(tmp_path, [_SINGLE_RECORD])
    assert get_authorization_source_record_by_id("AUTHSRC-NOPE", store=store) is None


def test_get_by_id_returns_exact_version(tmp_path):
    store = _store(tmp_path, [_SINGLE_RECORD])
    record = get_authorization_source_record_by_id("AUTHSRC-T1", store=store)
    assert record is not None
    assert record.source_record_id == "AUTHSRC-T1"


def test_get_by_id_requires_nonblank_id():
    with pytest.raises(ValueError):
        get_authorization_source_record_by_id("")


# --- Module 3 Scenario A: authorization amended after denial ---------------------------------


def test_scenario_a_amendment_corrects_servicing_provider_and_preserves_both_versions(tmp_path):
    """Module 3 Scenario A's precondition: a source-record version corrects
    the servicing provider to align with a claim; original and amended
    versions both remain traceable; a later amendment timestamp alone does
    not establish retroactive applicability."""
    original = {
        "source_record_id": "AUTHSRC-SCEN-A-1",
        "authorization_reference_number": "SCEN-A-REF",
        "member_id": "M-1001",
        "service_code": "MRI-KNEE",
        "servicing_provider_id": "PRV-WRONG-FACILITY",  # does NOT match CLM-1001's real servicing provider
        "status": "DENIED",
        "record_version": 1,
        "decided_at": "2026-02-15T10:00:00Z",
    }
    amended = {
        "source_record_id": "AUTHSRC-SCEN-A-2",
        "authorization_reference_number": "SCEN-A-REF",
        "member_id": "M-1001",
        "service_code": "MRI-KNEE",
        "servicing_provider_id": "PRV-1001",  # corrected to align with CLM-1001
        "status": "APPROVED",
        "record_version": 2,
        "authorized_start_date": "2026-01-15",
        "authorized_end_date": "2026-04-15",
        "decided_at": "2026-03-01T09:00:00Z",
        "amended_at": "2026-03-01T09:00:00Z",
        "amends_source_record_id": "AUTHSRC-SCEN-A-1",
        "amendment_reason": "Servicing provider corrected on review.",
        # retroactive_effective deliberately omitted -- the source does not say.
    }
    store = _store(tmp_path, [original, amended])

    result = lookup_authorization_source_by_reference("SCEN-A-REF", store=store)
    assert result.status == AuthorizationSourceLookupStatus.FOUND

    # Both versions remain traceable, with distinct provenance (different
    # servicing_provider_id, different status) -- neither overwrites the other.
    by_id = {v.source_record_id: v for v in result.version_history}
    assert by_id["AUTHSRC-SCEN-A-1"].servicing_provider_id == "PRV-WRONG-FACILITY"
    assert by_id["AUTHSRC-SCEN-A-1"].status == "DENIED"
    assert by_id["AUTHSRC-SCEN-A-2"].servicing_provider_id == "PRV-1001"
    assert by_id["AUTHSRC-SCEN-A-2"].status == "APPROVED"

    # The current version is the corrected one.
    assert result.record.source_record_id == "AUTHSRC-SCEN-A-2"
    assert result.record.servicing_provider_id == "PRV-1001"

    # A later amended_at, by itself, does NOT establish retroactive
    # applicability -- it stays unknown/not established.
    assert result.record.retroactive_effective is None


# --- Module 3 Scenario B: approved authorization excludes the service date --------------------


def test_scenario_b_approval_and_service_date_applicability_are_separate_facts(tmp_path):
    """Module 3 Scenario B's precondition: approval status is confirmed,
    but the authorized period begins after CLM-1001's 2026-02-10 MRI --
    approval and service-date applicability must remain independently
    observable, with no field or method on the record silently resolving
    the mismatch."""
    claim_service_date = date(2026, 2, 10)
    record = {
        "source_record_id": "AUTHSRC-SCEN-B-1",
        "authorization_reference_number": "SCEN-B-REF",
        "member_id": "M-1001",
        "service_code": "MRI-KNEE",
        "servicing_provider_id": "PRV-1001",
        "status": "APPROVED",
        "record_version": 1,
        "authorized_start_date": "2026-03-01",  # starts AFTER the claim's service date
        "authorized_end_date": "2026-05-31",
        "decided_at": "2026-02-20T13:00:00Z",
    }
    store = _store(tmp_path, [record])

    result = lookup_authorization_source_by_reference("SCEN-B-REF", store=store)
    assert result.status == AuthorizationSourceLookupStatus.FOUND
    assert result.record.status == "APPROVED"  # approval is confirmed ...
    assert result.record.authorized_start_date > claim_service_date  # ... but excludes the service date
    assert not hasattr(result.record, "applies_to_service_date")  # no silent resolution anywhere


# --- isolation: submissions cannot mutate source records; baseline stays intact ---------------


def test_editing_a_submission_cannot_mutate_source_records(tmp_path):
    store = _store(tmp_path, [_SINGLE_RECORD])
    before = store.records_by_id["AUTHSRC-T1"].model_copy(deep=True)

    # No dispute-review submission type has a write path into
    # AuthorizationSourceStore at all -- constructing/varying one, including
    # with a value that coincidentally matches a source record's reference
    # number, must have zero effect on the store.
    BillingCorrectionSubmission(
        original_claim_reference="REF-SINGLE",
        member_id="SOMEONE-ELSE",
        service_code="DIFFERENT-SERVICE",
        servicing_provider_id="DIFFERENT-PROVIDER",
        supplied_by="PATIENT",
    )

    after = lookup_authorization_source_by_reference("REF-SINGLE", store=store).record
    assert after == before
    assert store.records_by_id["AUTHSRC-T1"] == before


def test_baseline_prior_authorization_fixtures_are_unaffected_by_this_module_existing():
    """Merely importing/using dispute_review.authorization_source_lookup
    must not change tools.prior_auth_tool's behavior for the ORIGINAL
    baseline fixture -- confirms the two stores are genuinely independent,
    not just conceptually described as such."""
    baseline_pa2001 = get_prior_authorization_by_id("PA-2001")
    assert baseline_pa2001 is not None
    assert baseline_pa2001.status == "APPROVED"
    assert baseline_pa2001.member_id == "M-1002"

    baseline_list = get_prior_authorizations("M-1002", "MRI-KNEE")
    assert {a.authorization_id for a in baseline_list} == {"PA-1501", "PA-2001"}

    # The new AUTHSRC-* ids are not, and must never be, resolvable through
    # the baseline lookup -- confirms no accidental merge of the two datasets.
    assert get_prior_authorization_by_id("AUTHSRC-9001") is None

    store = get_data_store()
    assert "AUTHSRC-9001" not in store.prior_authorizations
