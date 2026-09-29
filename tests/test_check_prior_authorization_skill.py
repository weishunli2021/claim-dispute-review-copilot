"""Tests for the check_prior_authorization skill."""

from __future__ import annotations

from skills.base import SkillStatus
from skills.check_prior_authorization import check_prior_authorization


def test_case1_no_matching_record_is_not_treated_as_denial():
    result = check_prior_authorization("M-1001", "MRI-KNEE")
    assert result.status == SkillStatus.COMPLETED
    assert result.evidence["authorizations"] == []
    assert "prior_authorization" in result.missing_information


def test_case2_both_authorization_records_preserved():
    result = check_prior_authorization("M-1002", "MRI-KNEE")
    assert result.status == SkillStatus.COMPLETED
    assert result.missing_information == []

    authorizations = result.evidence["authorizations"]
    assert len(authorizations) == 2
    ids = {a["authorization_id"] for a in authorizations}
    assert ids == {"PA-1501", "PA-2001"}
    for auth in authorizations:
        assert "status" in auth
        assert "effective_date" in auth
        assert "expiration_date" in auth
        assert auth["service_code"] == "MRI-KNEE"


def test_date_of_service_is_carried_through_not_evaluated():
    from datetime import date

    result = check_prior_authorization("M-1002", "MRI-KNEE", date_of_service=date(2026, 2, 12))
    assert result.evidence["date_of_service"] == "2026-02-12"
    # Still returns both records as-is -- no filtering/decision based on the date.
    assert len(result.evidence["authorizations"]) == 2


def test_invalid_member_id_raises_error_status():
    result = check_prior_authorization("bad id", "MRI-KNEE")
    assert result.status == SkillStatus.ERROR
