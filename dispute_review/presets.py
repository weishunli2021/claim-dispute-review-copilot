"""The single "Load billing correction example" demo submission for the
active Dispute Review scenario.

This request supersedes the earlier requirement to retain five
authorization-dispute presets (matching / different-servicing-provider /
incomplete / provider-corrected-after-denial / approved-authorization-
excludes-service-date) -- all five, and their supporting helper functions,
are removed. There is exactly one active scenario now: a provider proposes
corrected billing fields for CLM-BILL-9001, and this module's one preset
proposes all four corrections that the independent supporting records
(dispute_review.billing_fixtures.get_billing_support_records) actually
support, so the default example demonstrates a fully-supported correction.
"""

from __future__ import annotations

from dispute_review.billing_fixtures import BILLING_CLAIM_ID
from dispute_review.models import BillingCorrectionSubmission


def billing_correction_example_preset() -> BillingCorrectionSubmission:
    """The default, fully-supported example: corrects service code,
    modifier, units, and servicing provider to the values the independent
    supporting records (BILLREC-001/002/003) establish."""
    return BillingCorrectionSubmission(
        supplied_by="Billing Provider Office (Riverside Ortho Clinic)",
        original_claim_reference=BILLING_CLAIM_ID,
        member_id="MEM-BILL-01",
        service_date="2026-06-01",
        service_code="SURG-KNEE-REPAIR",
        modifier="MOD-L",
        units=1,
        servicing_provider_id="PRV-BILL-ACTUAL",
        proposed_billed_amount=1200.00,
        correction_explanation=(
            "Corrected service code, modifier, units, and servicing provider to match the operative "
            "note and provider roster."
        ),
        supporting_record_references=["BILLREC-001", "BILLREC-002", "BILLREC-003"],
    )
