"""Synthetic demo presets for the dispute-review comparison layer.

Every preset DERIVES its field values from an actual claim snapshot (and,
for the alternate-provider preset, the real synthetic provider dataset) --
none of these hardcodes a comparison outcome, and none is special-cased by
claim_id. Running any preset's submission through
dispute_review.comparison.compare_submission is what produces its result;
this module never computes or asserts a Match/Mismatch/Unknown verdict
itself. The demo is focused on CLM-1001, but nothing here branches on that
specific claim_id -- these functions work identically for any resolved
claim.

Presets never mutate the DataStore, the knowledge graph, or any *.json
fixture. `pick_alternate_servicing_provider_id` performs a READ-ONLY scan
of tools.data_store.get_data_store().providers (the same singleton every
other read-only tool already reads) -- it never writes to it. When no
suitable real alternative exists, it falls back to an explicitly
fictional, demo-only provider id that is never inserted anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from dispute_review.comparison import build_claim_snapshot
from dispute_review.models import ClaimSnapshot, DisputeSubmission, SubmissionSource
from tools.case_context import CaseContext
from tools.data_store import get_data_store

_SYNTHETIC_AUTH_REFERENCE = "DEMO-AUTH-0001"

# Explicitly fictional -- never written to the DataStore or any fixture.
# Used only when the synthetic provider dataset has no other record of the
# same provider_type to offer as a "different servicing provider" preset.
FICTIONAL_DEMO_PROVIDER_ID = "PRV-DEMO-ALT-FACILITY"

_VALIDITY_WINDOW_PADDING_DAYS = 30


def pick_alternate_servicing_provider_id(case_context: CaseContext) -> str:
    """Pick a real, different servicing-provider id from the synthetic
    provider dataset for the "different servicing provider" demo preset.

    Chosen to be the SAME provider_type as the claim's actual servicing
    provider (so the substitution stays a plausible same-role swap, e.g.
    facility-for-facility), and explicitly excludes both the claim's own
    servicing provider id and its ordering provider id -- even though
    ClaimSnapshot itself has no ordering-provider field, this function
    reads case_context.ordering_provider directly so the ordering
    physician is never accidentally chosen as a "different servicing
    facility" merely because its id differs from the servicing provider's.

    Falls back to FICTIONAL_DEMO_PROVIDER_ID (never inserted into the
    DataStore) when no suitable alternative exists in the dataset.
    """
    current = case_context.servicing_provider
    if current is None:
        return FICTIONAL_DEMO_PROVIDER_ID

    exclude_ids = {current.provider_id}
    if case_context.ordering_provider is not None:
        exclude_ids.add(case_context.ordering_provider.provider_id)

    candidates = sorted(
        provider.provider_id
        for provider in get_data_store().providers.values()
        if provider.provider_type == current.provider_type and provider.provider_id not in exclude_ids
    )
    return candidates[0] if candidates else FICTIONAL_DEMO_PROVIDER_ID


def _default_validity_window(claim: ClaimSnapshot) -> tuple[Optional[str], Optional[str]]:
    """A start/end pair (ISO strings) that contains the claim's actual
    date of service, padded symmetrically -- or (None, None) if the claim
    has no recorded date of service to build a window around."""
    if claim.date_of_service is None:
        return None, None
    padding = timedelta(days=_VALIDITY_WINDOW_PADDING_DAYS)
    start = claim.date_of_service - padding
    end = claim.date_of_service + padding
    return start.isoformat(), end.isoformat()


def matching_preset(claim: ClaimSnapshot) -> DisputeSubmission:
    """All four comparable fields derived to match the claim exactly, with
    a validity window that contains the claim's actual date of service.
    Labeled as a synthetic demonstration via its dispute_explanation."""
    start, end = _default_validity_window(claim)
    return DisputeSubmission(
        authorization_reference_number=_SYNTHETIC_AUTH_REFERENCE,
        member_id=claim.member_id,
        service_code=claim.service_code,
        authorization_start_date=start,
        authorization_end_date=end,
        servicing_provider_id=claim.servicing_provider_id,
        dispute_explanation=(
            "Synthetic demo submission (all fields intentionally match the claim) -- "
            "not a real dispute."
        ),
        supplied_by=SubmissionSource.PROVIDER,
    )


def different_servicing_provider_preset(
    claim: ClaimSnapshot, alternate_provider_id: str
) -> DisputeSubmission:
    """Same as matching_preset except servicing_provider_id is a caller-
    supplied, DIFFERENT provider id than the claim's actual servicing
    provider -- see pick_alternate_servicing_provider_id for how that id
    should be chosen (never the claim's own servicing provider or its
    ordering provider).
    """
    if alternate_provider_id == claim.servicing_provider_id:
        raise ValueError(
            "different_servicing_provider_preset requires alternate_provider_id to differ "
            f"from the claim's actual servicing provider ({claim.servicing_provider_id!r})."
        )
    start, end = _default_validity_window(claim)
    return DisputeSubmission(
        authorization_reference_number=_SYNTHETIC_AUTH_REFERENCE,
        member_id=claim.member_id,
        service_code=claim.service_code,
        authorization_start_date=start,
        authorization_end_date=end,
        servicing_provider_id=alternate_provider_id,
        dispute_explanation=(
            "Synthetic demo submission (servicing provider intentionally differs from the "
            "claim) -- not a real dispute."
        ),
        supplied_by=SubmissionSource.PROVIDER,
    )


def incomplete_submission_preset(claim: ClaimSnapshot) -> DisputeSubmission:
    """Keeps member and service matching the claim; omits servicing_provider_id
    and the authorization end date, so those two comparisons resolve to
    Unknown when run through compare_submission (the outcome is produced
    by the real comparator, never asserted here)."""
    start, _ = _default_validity_window(claim)
    return DisputeSubmission(
        authorization_reference_number=_SYNTHETIC_AUTH_REFERENCE,
        member_id=claim.member_id,
        service_code=claim.service_code,
        authorization_start_date=start,
        authorization_end_date=None,
        servicing_provider_id=None,
        dispute_explanation=(
            "Synthetic demo submission (incomplete -- servicing provider and authorization "
            "end date omitted) -- not a real dispute."
        ),
        supplied_by=SubmissionSource.PATIENT,
    )


@dataclass(frozen=True)
class DisputeDemoPresets:
    """The three synthetic demo submissions for one claim, plus the
    ClaimSnapshot they were derived from -- everything a future UI layer
    (Module 4) needs to run all three presets through compare_submission."""

    claim: ClaimSnapshot
    matching: DisputeSubmission
    different_servicing_provider: DisputeSubmission
    incomplete: DisputeSubmission


def build_demo_presets(case_context: CaseContext) -> DisputeDemoPresets:
    """Build all three demo presets for one already-resolved CaseContext.
    Read-only: does not mutate case_context, the DataStore, or the graph."""
    claim = build_claim_snapshot(case_context)
    alternate_provider_id = pick_alternate_servicing_provider_id(case_context)
    return DisputeDemoPresets(
        claim=claim,
        matching=matching_preset(claim),
        different_servicing_provider=different_servicing_provider_preset(claim, alternate_provider_id),
        incomplete=incomplete_submission_preset(claim),
    )
