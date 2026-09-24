"""Tests for dispute_review.presets, plus a focused integration check
against the real CLM-1001 record via the existing read-only claim lookup
(tools.case_context.get_case_context) -- deliberately NOT using
application.scenario_lab's DataStore-boundary override (see
docs/DISPUTE_REVIEW_LOGIC.md for why dispute review must not reuse it).

Expected outcomes below are asserted directly against compare_submission's
real output -- these tests never hardcode a Match/Mismatch/Unknown verdict
independent of running the actual comparator, per Step 5/6's "derive
results by calling the comparison function; do not hardcode outcomes."
"""

from __future__ import annotations

import copy

from dispute_review.comparison import compare_submission
from dispute_review.models import ComparisonStatus
from dispute_review.presets import (
    FICTIONAL_DEMO_PROVIDER_ID,
    build_demo_presets,
    different_servicing_provider_preset,
    incomplete_submission_preset,
    matching_preset,
    pick_alternate_servicing_provider_id,
)
from tools.case_context import CaseContext, get_case_context
from tools.data_store import get_data_store
from tools.models import Provider
from tools.prior_auth_tool import get_prior_authorizations


def _clm_1001_case_context() -> CaseContext:
    return get_case_context("CLM-1001")


# --- pick_alternate_servicing_provider_id ------------------------------------------------


def test_pick_alternate_servicing_provider_prefers_real_dataset_provider():
    provider_id = pick_alternate_servicing_provider_id(_clm_1001_case_context())
    # PRV-1001 (facility) is CLM-1001's actual servicing provider; the
    # dataset also has PRV-1002 and PRV-2002 as other facilities.
    assert provider_id != "PRV-1001"
    assert provider_id in get_data_store().providers
    assert get_data_store().providers[provider_id].provider_type == "facility"


def test_pick_alternate_servicing_provider_never_returns_the_ordering_provider():
    case_context = _clm_1001_case_context()
    provider_id = pick_alternate_servicing_provider_id(case_context)
    assert provider_id != case_context.ordering_provider.provider_id


def test_pick_alternate_servicing_provider_excludes_ordering_provider_even_when_same_type():
    # Construct a synthetic CaseContext (no DataStore/graph mutation) where
    # the ordering provider happens to share the servicing provider's own
    # provider_type ("facility") -- unlike the real CLM-1001 data, where
    # the ordering provider is a physician. This is the exact scenario the
    # task calls out: never pick the ordering provider merely because its
    # id differs, even if its type would otherwise make it look "suitable."
    fake_context = CaseContext(
        servicing_provider=Provider(
            provider_id="PRV-1001", name="Lakeside Imaging Center", provider_type="facility", network_status="in-network"
        ),
        ordering_provider=Provider(
            provider_id="PRV-1002", name="QuickDraw Labs", provider_type="facility", network_status="in-network"
        ),
    )
    provider_id = pick_alternate_servicing_provider_id(fake_context)
    assert provider_id != "PRV-1001"
    assert provider_id != "PRV-1002"  # the (fake) ordering provider -- must never be chosen
    # PRV-2002 (Sunrise Physical Therapy Group) is the only remaining real
    # facility in the dataset once PRV-1001 and PRV-1002 are excluded.
    assert provider_id == "PRV-2002"


def test_pick_alternate_servicing_provider_falls_back_to_fictional_id_when_none_available():
    fake_context = CaseContext(
        servicing_provider=Provider(
            provider_id="PRV-ONLY-OF-TYPE", name="Only One", provider_type="nonexistent-type", network_status="in-network"
        )
    )
    provider_id = pick_alternate_servicing_provider_id(fake_context)
    assert provider_id == FICTIONAL_DEMO_PROVIDER_ID


def test_fictional_demo_provider_id_is_never_written_to_the_data_store():
    _clm_1001_case_context()
    build_demo_presets(_clm_1001_case_context())
    assert FICTIONAL_DEMO_PROVIDER_ID not in get_data_store().providers


# --- preset content / labeling -------------------------------------------------------------


def test_presets_are_labeled_as_synthetic_demonstrations():
    presets = build_demo_presets(_clm_1001_case_context())
    for submission in (presets.matching, presets.different_servicing_provider, presets.incomplete):
        assert submission.dispute_explanation is not None
        assert "synthetic demo" in submission.dispute_explanation.lower()


def test_incomplete_preset_omits_servicing_provider_and_an_end_date():
    presets = build_demo_presets(_clm_1001_case_context())
    assert presets.incomplete.servicing_provider_id is None
    assert presets.incomplete.authorization_end_date is None
    # but keeps member/service matching, per Step 5
    assert presets.incomplete.member_id == presets.claim.member_id
    assert presets.incomplete.service_code == presets.claim.service_code


def test_different_servicing_provider_preset_rejects_same_provider_id():
    presets = build_demo_presets(_clm_1001_case_context())
    import pytest

    with pytest.raises(ValueError):
        different_servicing_provider_preset(presets.claim, presets.claim.servicing_provider_id)


# --- presets produce their expected behavior through the REAL comparator -------------------


def test_matching_preset_produces_all_match_via_real_comparator():
    presets = build_demo_presets(_clm_1001_case_context())
    result = compare_submission(presets.claim, presets.matching)
    assert [row.status for row in result.rows] == [ComparisonStatus.MATCH] * 4


def test_different_servicing_provider_preset_produces_exactly_one_mismatch_via_real_comparator():
    presets = build_demo_presets(_clm_1001_case_context())
    result = compare_submission(presets.claim, presets.different_servicing_provider)
    by_field = {row.field: row for row in result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.MISMATCH
    assert by_field["Member"].status == ComparisonStatus.MATCH
    assert by_field["Service"].status == ComparisonStatus.MATCH
    assert by_field["Validity Dates"].status == ComparisonStatus.MATCH


def test_incomplete_preset_produces_two_unknowns_via_real_comparator():
    presets = build_demo_presets(_clm_1001_case_context())
    result = compare_submission(presets.claim, presets.incomplete)
    by_field = {row.field: row for row in result.rows}
    assert by_field["Servicing Provider"].status == ComparisonStatus.UNKNOWN
    assert by_field["Validity Dates"].status == ComparisonStatus.UNKNOWN
    assert by_field["Member"].status == ComparisonStatus.MATCH
    assert by_field["Service"].status == ComparisonStatus.MATCH


# --- preset construction leaves inputs unchanged --------------------------------------------


def test_preset_construction_does_not_mutate_case_context():
    case_context = _clm_1001_case_context()
    before = case_context.model_dump()
    build_demo_presets(case_context)
    assert case_context.model_dump() == before


def test_matching_preset_does_not_mutate_claim_snapshot():
    presets = build_demo_presets(_clm_1001_case_context())
    before = presets.claim.model_dump()
    matching_preset(presets.claim)
    incomplete_submission_preset(presets.claim)
    assert presets.claim.model_dump() == before


# --- focused integration check: real CLM-1001 lookup, no Scenario Lab override -------------


def test_integration_clm_1001_lookup_leaves_original_data_unchanged():
    """Reads CLM-1001 via the existing read-only claim lookup, runs the
    dispute-review logic (presets + comparator), and verifies the original
    claim and prior-authorization records are byte-for-byte unchanged
    afterward. Deliberately does not import or use
    application.scenario_lab's DataStore-boundary override anywhere in
    this test.
    """
    case_context_before = get_case_context("CLM-1001")
    claim_snapshot_before = copy.deepcopy(case_context_before.model_dump())
    auths_before = [a.model_dump() for a in get_prior_authorizations("M-1001", "MRI-KNEE")]
    provider_ids_before = set(get_data_store().providers.keys())
    claim_ids_before = set(get_data_store().claims.keys())

    presets = build_demo_presets(case_context_before)
    for submission in (presets.matching, presets.different_servicing_provider, presets.incomplete):
        compare_submission(presets.claim, submission)

    case_context_after = get_case_context("CLM-1001")
    assert case_context_after.model_dump() == claim_snapshot_before

    auths_after = [a.model_dump() for a in get_prior_authorizations("M-1001", "MRI-KNEE")]
    assert auths_after == auths_before

    assert set(get_data_store().providers.keys()) == provider_ids_before
    assert set(get_data_store().claims.keys()) == claim_ids_before
