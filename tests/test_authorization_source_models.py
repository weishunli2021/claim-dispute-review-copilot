"""Tests for dispute_review/authorization_source_models.py (Module 2, v2).

Uses hand-constructed AuthorizationSourceRecord/AuthorizationSourceLookupResult
instances only -- no fixture file, no store. tests/test_authorization_source_lookup.py
covers the isolated store/lookup layer built on top of this model.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from dispute_review.authorization_source_models import (
    SYNTHETIC_AUTHORIZATION_SOURCE_SYSTEM,
    AuthorizationSourceLookupResult,
    AuthorizationSourceLookupStatus,
    AuthorizationSourceRecord,
)


def _record(**overrides) -> AuthorizationSourceRecord:
    defaults = dict(
        source_record_id="AUTHSRC-T1",
        authorization_reference_number="REF-T1",
        member_id="M-T1",
        service_code="SVC-T1",
        servicing_provider_id="PRV-T1",
        status="APPROVED",
        record_version=1,
    )
    defaults.update(overrides)
    return AuthorizationSourceRecord(**defaults)


# --- valid construction / defaults ------------------------------------------------------


def test_valid_record_parses_with_all_fields():
    record = _record(
        authorized_start_date=date(2026, 1, 1),
        authorized_end_date=date(2026, 6, 30),
        decided_at=datetime(2025, 12, 15, 9, 0, 0),
        notes="A synthetic note.",
    )
    assert record.source_record_id == "AUTHSRC-T1"
    assert record.status == "APPROVED"
    assert record.authorized_start_date == date(2026, 1, 1)
    assert record.source_system == SYNTHETIC_AUTHORIZATION_SOURCE_SYSTEM
    assert record.amended_at is None
    assert record.amends_source_record_id is None
    assert record.retroactive_effective is None


def test_record_is_frozen_and_rejects_unknown_fields():
    record = _record()
    with pytest.raises(ValidationError):
        record.status = "DENIED"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _record(unexpected_field="x")


@pytest.mark.parametrize(
    "field_name",
    ["source_record_id", "authorization_reference_number", "member_id", "service_code", "servicing_provider_id", "status"],
)
def test_required_identifier_fields_reject_blank(field_name):
    with pytest.raises(ValidationError):
        _record(**{field_name: "   "})


# --- date-range validation ---------------------------------------------------------------


def test_invalid_authorized_period_start_after_end_is_rejected():
    with pytest.raises(ValidationError):
        _record(authorized_start_date=date(2026, 6, 1), authorized_end_date=date(2026, 1, 1))


def test_authorized_period_may_be_entirely_absent_eg_for_a_denial():
    # A DENIED record legitimately has no authorized period at all.
    record = _record(status="DENIED", authorized_start_date=None, authorized_end_date=None)
    assert record.authorized_start_date is None
    assert record.authorized_end_date is None


def test_record_is_never_rejected_for_a_period_that_excludes_some_external_date():
    """The model has no concept of 'the claim's service date' at all --
    an authorized period that would not cover some other date is a valid,
    constructible record; whether it 'applies' is left entirely to a later
    comparison layer (Module 3). This directly demonstrates Module 3
    Scenario B's precondition: approval and service-date applicability are
    independent facts."""
    claim_service_date = date(2026, 2, 10)
    record = _record(
        status="APPROVED",
        authorized_start_date=date(2026, 3, 1),  # starts AFTER the claim's service date
        authorized_end_date=date(2026, 5, 31),
    )
    assert record.status == "APPROVED"
    # The record itself makes no "applies" determination -- confirmed by
    # there being no such field/method on the model at all, and by the
    # fact that the caller must do this comparison manually:
    assert record.authorized_start_date > claim_service_date
    assert not hasattr(record, "applies_to_service_date")


# --- version-chain integrity ---------------------------------------------------------------


def test_version_one_cannot_set_amends_source_record_id():
    with pytest.raises(ValidationError):
        _record(record_version=1, amends_source_record_id="AUTHSRC-OTHER")


def test_version_greater_than_one_requires_amends_source_record_id():
    with pytest.raises(ValidationError):
        _record(record_version=2, amends_source_record_id=None)


def test_record_cannot_amend_itself():
    with pytest.raises(ValidationError):
        _record(source_record_id="AUTHSRC-X", record_version=2, amends_source_record_id="AUTHSRC-X")


def test_record_version_must_be_at_least_one():
    with pytest.raises(ValidationError):
        _record(record_version=0)


def test_valid_amendment_version_two_parses():
    amended = _record(
        source_record_id="AUTHSRC-T2",
        record_version=2,
        amends_source_record_id="AUTHSRC-T1",
        amendment_reason="Corrected servicing provider.",
        amended_at=datetime(2026, 1, 25, 11, 0, 0),
    )
    assert amended.amends_source_record_id == "AUTHSRC-T1"
    assert amended.amendment_reason == "Corrected servicing provider."


# --- retroactive_effective stays a tri-state, never inferred ------------------------------


def test_retroactive_effective_defaults_to_unknown_none():
    amended = _record(
        source_record_id="AUTHSRC-T2",
        record_version=2,
        amends_source_record_id="AUTHSRC-T1",
        amended_at=datetime(2026, 1, 25, 11, 0),
    )
    assert amended.retroactive_effective is None


def test_retroactive_effective_can_be_explicitly_true_or_false_when_the_source_says_so():
    asserted_true = _record(
        source_record_id="AUTHSRC-T3", record_version=2, amends_source_record_id="AUTHSRC-T1",
        retroactive_effective=True,
    )
    asserted_false = _record(
        source_record_id="AUTHSRC-T4", record_version=2, amends_source_record_id="AUTHSRC-T1",
        retroactive_effective=False,
    )
    assert asserted_true.retroactive_effective is True
    assert asserted_false.retroactive_effective is False


def test_decided_at_is_never_defaulted_or_invented():
    # A missing original-denial timestamp stays missing -- no default_factory,
    # no "now", nothing invented.
    record = _record(status="DENIED")
    assert record.decided_at is None


# --- lookup result shape -------------------------------------------------------------------


def test_lookup_result_not_found_carries_no_record_or_candidates():
    result = AuthorizationSourceLookupResult(
        status=AuthorizationSourceLookupStatus.NOT_FOUND, queried_reference_number="REF-MISSING"
    )
    assert result.record is None
    assert result.version_history == []
    assert result.candidates == []
