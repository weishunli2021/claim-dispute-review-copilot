"""Tests for the deterministic tools layer and the case-context fixture view.

Each tool's contract has two independent axes, both covered below:
  - a syntactically valid but unknown identifier -> None (or [] for
    get_prior_authorizations)
  - a blank/None/malformed identifier -> ValueError (see test_validation.py
    for exhaustive format-checking; here we only confirm each tool wires
    validation in)
"""

from __future__ import annotations

import pytest

from tools.benefits_tool import get_benefits
from tools.case_context import get_case_context
from tools.claim_tool import get_claim
from tools.member_tool import get_member
from tools.plan_tool import get_plan
from tools.prior_auth_tool import get_prior_authorization_by_id, get_prior_authorizations
from tools.provider_tool import get_provider


# ---- member_tool ----------------------------------------------------------


def test_get_member_success():
    member = get_member("M-1001")
    assert member is not None
    assert member.first_name == "Jordan"
    assert member.last_name == "Ellis"
    assert member.plan_id == "PLAN-GOLD"


def test_get_member_unknown_returns_none():
    assert get_member("M-9999") is None


@pytest.mark.parametrize("value", ["", None, "  "])
def test_get_member_invalid_input_raises(value):
    with pytest.raises(ValueError):
        get_member(value)


# ---- claim_tool -------------------------------------------------------------


def test_get_claim_success():
    claim = get_claim("CLM-1002")
    assert claim is not None
    assert claim.member_id == "M-1002"
    assert claim.status == "PAID"


def test_get_claim_unknown_returns_none():
    assert get_claim("CLM-9999") is None


@pytest.mark.parametrize("value", ["", None])
def test_get_claim_invalid_input_raises(value):
    with pytest.raises(ValueError):
        get_claim(value)


# ---- plan_tool --------------------------------------------------------------


def test_get_plan_success():
    plan = get_plan("PLAN-GOLD")
    assert plan is not None
    assert plan.plan_name == "Meridian Gold PPO"


def test_get_plan_unknown_returns_none():
    assert get_plan("PLAN-DOES-NOT-EXIST") is None


@pytest.mark.parametrize("value", ["", None])
def test_get_plan_invalid_input_raises(value):
    with pytest.raises(ValueError):
        get_plan(value)


# ---- benefits_tool ----------------------------------------------------------


def test_get_benefits_found():
    benefit = get_benefits("PLAN-GOLD", "MRI-KNEE")
    assert benefit is not None
    assert benefit.covered is True
    assert benefit.requires_prior_auth is True


def test_get_benefits_not_covered_is_a_real_record_not_none():
    benefit = get_benefits("PLAN-SILVER", "LAB-BASIC")
    assert benefit is not None
    assert benefit.covered is False


def test_get_benefits_no_record_returns_none():
    # Case 5: PLAN-BRONZE has no benefit record at all for SPECIALIST-VISIT.
    assert get_benefits("PLAN-BRONZE", "SPECIALIST-VISIT") is None


@pytest.mark.parametrize("plan_id,service_code", [("", "MRI-KNEE"), ("PLAN-GOLD", ""), (None, "MRI-KNEE")])
def test_get_benefits_invalid_input_raises(plan_id, service_code):
    with pytest.raises(ValueError):
        get_benefits(plan_id, service_code)


# ---- prior_auth_tool --------------------------------------------------------


def test_get_prior_authorizations_returns_all_matching_records_over_time():
    # Case 2: two authorization records exist for M-1002 + MRI-KNEE over time.
    authorizations = get_prior_authorizations("M-1002", "MRI-KNEE")
    assert len(authorizations) == 2
    assert {a.authorization_id for a in authorizations} == {"PA-1501", "PA-2001"}

    approved = [a for a in authorizations if a.status == "APPROVED"]
    assert len(approved) == 1
    assert approved[0].authorization_id == "PA-2001"


def test_get_prior_authorizations_not_found_returns_empty_list_not_denied():
    # Case 1: no PA record exists for M-1001 + MRI-KNEE.
    assert get_prior_authorizations("M-1001", "MRI-KNEE") == []


@pytest.mark.parametrize(
    "member_id,service_code", [("", "MRI-KNEE"), ("M-1001", ""), (None, "MRI-KNEE")]
)
def test_get_prior_authorizations_invalid_input_raises(member_id, service_code):
    with pytest.raises(ValueError):
        get_prior_authorizations(member_id, service_code)


def test_get_prior_authorization_by_id_returns_matching_record():
    found = get_prior_authorization_by_id("PA-1501")
    assert found is not None
    assert found.authorization_id == "PA-1501"
    assert found.member_id == "M-1002"


def test_get_prior_authorization_by_id_unknown_id_returns_none():
    assert get_prior_authorization_by_id("PA-DOES-NOT-EXIST") is None


@pytest.mark.parametrize("authorization_id", ["", None, "PA 1501"])
def test_get_prior_authorization_by_id_invalid_input_raises(authorization_id):
    with pytest.raises(ValueError):
        get_prior_authorization_by_id(authorization_id)


# ---- provider_tool ----------------------------------------------------------


def test_get_provider_success():
    provider = get_provider("PRV-1001")
    assert provider is not None
    assert provider.network_status == "in-network"


def test_get_provider_unknown_returns_none():
    assert get_provider("PRV-9999") is None


@pytest.mark.parametrize("value", ["", None])
def test_get_provider_invalid_input_raises(value):
    with pytest.raises(ValueError):
        get_provider(value)


# ---- case_context: Case 1 structured facts ---------------------------------


def test_case_1_structured_facts_show_auth_required_denial():
    context = get_case_context("CLM-1001")

    assert context.claim is not None
    assert context.claim.status == "DENIED"
    assert context.claim.denial_reason_code == "AUTH_REQUIRED"

    assert context.benefit is not None
    assert context.benefit.covered is True
    assert context.benefit.requires_prior_auth is True

    # No authorization record at all exists for this member/service.
    assert context.prior_authorizations == []

    assert context.member is not None
    assert context.member.member_id == "M-1001"
    assert context.plan is not None
    assert context.plan.plan_id == "PLAN-GOLD"
    assert context.servicing_provider is not None
    assert context.ordering_provider is not None


def test_case_2_structured_facts_show_matching_approved_authorization():
    context = get_case_context("CLM-1002")

    assert context.claim is not None
    assert context.claim.status == "PAID"

    assert len(context.prior_authorizations) == 2
    approved = [a for a in context.prior_authorizations if a.status == "APPROVED"]
    assert len(approved) == 1

    # The claim's date of service falls within the approved record's
    # effective window. This is a plain data check, not tool-computed
    # reasoning -- get_case_context itself makes no such determination.
    auth = approved[0]
    assert auth.effective_date <= context.claim.date_of_service <= auth.expiration_date


def test_case_context_unknown_claim_returns_all_empty():
    context = get_case_context("CLM-9999")
    assert context.claim is None
    assert context.member is None
    assert context.plan is None
    assert context.benefit is None
    assert context.prior_authorizations == []


def test_case_context_invalid_claim_id_raises():
    with pytest.raises(ValueError):
        get_case_context("")


def test_case_5_incomplete_data_is_visible_not_masked():
    # Case 5 is deliberately incomplete: no benefit rule, no prior auth
    # records, and a servicing provider_id that does not resolve.
    context = get_case_context("CLM-1005")

    assert context.claim is not None
    assert context.benefit is None
    assert context.prior_authorizations == []
    assert context.servicing_provider is None  # PRV-9999 does not exist
    assert context.ordering_provider is not None  # PRV-4004 does exist
