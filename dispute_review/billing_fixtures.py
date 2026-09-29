"""Read-only, ISOLATED lookups over the billing-correction scenario's two
separately-seeded synthetic fixtures.

Mirrors dispute_review/authorization_source_lookup.py's established
pattern exactly: validate every record against a Pydantic model at load
time, index once, expose read-only lookups. Neither fixture is read via
tools.data_store.DataStore or tools.case_context -- CLM-BILL-9001 is
deliberately ISOLATED from data/claims.json and the CLM-1001..CLM-1005
golden-dataset claims, so this scenario can never interact with, or be
picked up by, the separate Golden Dataset & Evaluation tab.

No database, no external service, no network call: two JSON fixture files,
each loaded once per process (an lru_cache singleton, re-loadable with an
explicit alternate path for test isolation).
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError

DEFAULT_BILLING_CLAIM_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "billing_correction_claim.json"
)
DEFAULT_BILLING_SUPPORT_RECORDS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "billing_correction_support_records.json"
)

BILLING_CLAIM_ID = "CLM-BILL-9001"


class BillingFixtureLoadError(RuntimeError):
    """Raised when a billing-scenario fixture is missing, malformed, or
    fails schema validation. Mirrors AuthorizationSourceLoadError's role
    for the retired authorization fixture -- never raised alongside it."""


class RawBillingClaimRecord(BaseModel):
    """Schema for data/billing_correction_claim.json -- validated once at
    load time. Field shape matches dispute_review.models.BillingClaimSnapshot
    field-for-field; kept as its own model (rather than loading directly
    into BillingClaimSnapshot) so a future fixture-format change never
    silently changes the frozen product-facing snapshot type's own
    validation rules.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_type: str
    submission_date: Optional[date] = None
    member_id: Optional[str] = None
    member_name: Optional[str] = None
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    billing_provider_id: Optional[str] = None
    billing_provider_name: Optional[str] = None
    servicing_provider_id: Optional[str] = None
    servicing_provider_name: Optional[str] = None
    ordering_provider_id: Optional[str] = None
    ordering_provider_name: Optional[str] = None
    service_date: Optional[date] = None
    service_code: Optional[str] = None
    service_code_description: Optional[str] = None
    modifier: Optional[str] = None
    modifier_description: Optional[str] = None
    units: Optional[int] = None
    place_of_service: Optional[str] = None
    diagnosis_code: Optional[str] = None
    diagnosis_description: Optional[str] = None
    charge_per_unit: Optional[float] = None
    total_billed: Optional[float] = None
    authorization_reference: Optional[str] = None
    decision_status: Optional[str] = None
    decision_date: Optional[date] = None
    decision_code: Optional[str] = None
    decision_reason: Optional[str] = None
    decision_explanation: Optional[str] = None
    decision_flagged_fields: list[str] = []
    allowed_amount: Optional[float] = None
    allowed_amount_recorded: bool = False
    paid_amount: Optional[float] = None
    paid_amount_recorded: bool = False
    original_supporting_references: list[str] = []
    original_notes: Optional[str] = None


class BillingSupportRecord(BaseModel):
    """One independent supporting service record -- authored and stored
    separately from the editable BillingCorrectionSubmission, and never
    generated or overwritten from form values. `supports_*` fields are
    None when this particular record does not speak to that attribute at
    all (not every record establishes every field)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str
    record_type: str
    record_date: Optional[date] = None
    author: Optional[str] = None
    summary: str
    supports_service_code: Optional[str] = None
    supports_modifier: Optional[str] = None
    supports_units: Optional[int] = None
    supports_servicing_provider_id: Optional[str] = None


class BillingSupportRecordSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    member_id: str
    records: list[BillingSupportRecord]


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise BillingFixtureLoadError(f"Missing billing-scenario fixture: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BillingFixtureLoadError(f"{path.name} is not valid JSON: {exc}") from exc


@lru_cache(maxsize=8)
def _load_claim_record(path_str: str) -> RawBillingClaimRecord:
    path = Path(path_str)
    raw = _load_json(path)
    try:
        return RawBillingClaimRecord.model_validate(raw)
    except ValidationError as exc:
        raise BillingFixtureLoadError(f"{path.name} failed schema validation: {exc}") from exc


@lru_cache(maxsize=8)
def _load_support_record_set(path_str: str) -> BillingSupportRecordSet:
    path = Path(path_str)
    raw = _load_json(path)
    try:
        return BillingSupportRecordSet.model_validate(raw)
    except ValidationError as exc:
        raise BillingFixtureLoadError(f"{path.name} failed schema validation: {exc}") from exc


def get_billing_claim_record(path: Path = DEFAULT_BILLING_CLAIM_PATH) -> RawBillingClaimRecord:
    """Load (and cache) the isolated original claim + recorded decision."""
    return _load_claim_record(str(path))


def get_billing_support_records(
    path: Path = DEFAULT_BILLING_SUPPORT_RECORDS_PATH,
) -> list[BillingSupportRecord]:
    """Load (and cache) the independent supporting service records, in
    fixture order."""
    return list(_load_support_record_set(str(path)).records)
