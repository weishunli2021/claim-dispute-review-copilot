"""Typed structures for the billing-correction scenario's dispute-evidence
assembly layer.

A DisputeEvidencePackage bundles everything investigate_dispute (skills/)
assembles for one BillingCorrectionSubmission against the one isolated
original claim (CLM-BILL-9001):

- The recorded claim + decision snapshot and the deterministic comparison
  result (dispute_review/, reused exactly as built there -- never
  reimplemented here).
- The submitted, unverified correction fields, verbatim.
- The independent supporting service records (dispute_review.billing_fixtures)
  -- authored and stored separately from the editable submission.
- The synthetic billing policy's sections, retrieved by REAL semantic
  vector search over an isolated index (dispute_review.billing_policy).
- The isolated provider-network graph's relationships for the servicing
  provider(s) in play (dispute_review.billing_graph) -- a SEPARATE,
  scenario-only graph, never the shared golden-dataset graph.
- Explicit per-source outcomes, missing evidence, conflicts, and
  limitations -- never silently folded into "everything looked fine."

There is deliberately NO generation-related field anywhere in this module
(no brief, no verdict, no confidence score, no sufficiency judgment) --
this is evidence for the workflow/generator/judge to consume, never a
conclusion drawn here.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from dispute_review.models import (
    BillingClaimSnapshot,
    BillingComparisonResult,
    BillingCorrectionSubmission,
)


class EvidenceSourceStatus(str, Enum):
    """The outcome of ONE retrieval source's attempt for THIS request --
    distinct from whether the evidence it returns, once retrieved, is
    relevant, applicable, or sufficient (a generation-layer judgment,
    never made here).

    SUCCESS_NO_RESULTS is not a failure: it means the source was reachable
    and ran, and legitimately found nothing. FAILURE means the source
    itself could not be queried at all (e.g. a malformed identifier, an
    unexpected exception) and is preserved AS a failure, never silently
    downgraded into an empty success.
    """

    SUCCESS_WITH_EVIDENCE = "SUCCESS_WITH_EVIDENCE"
    SUCCESS_NO_RESULTS = "SUCCESS_NO_RESULTS"
    FAILURE = "FAILURE"


class EvidenceSourceOutcome(BaseModel):
    """One retrieval source's outcome for this request (e.g.
    "billing_claim_record", "independent_support_records", "billing_policy",
    "claim_member_linkage"). `detail` is always a short, clean, user-facing
    message -- never a raw exception message or traceback, and never a
    secret."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    status: EvidenceSourceStatus
    item_count: int = 0
    detail: Optional[str] = None


class EvidenceProvenanceCategory:
    """Closed, documented vocabulary for EvidenceReference.provenance --
    plain string constants (not an Enum) so tests and callers can compare
    against them without an extra import, and so a reader scanning
    evidence JSON sees the category name directly. Never invented ad hoc
    per reference; every EvidenceReference uses exactly one of these.
    """

    RECORDED = "RECORDED"
    """Already on file: the isolated original claim and its recorded
    decision (dispute_review.billing_fixtures.get_billing_claim_record)."""

    SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
    """Raw content from the BillingCorrectionSubmission itself -- never
    authenticated, never treated as equivalent to a RECORDED fact."""

    INDEPENDENT_SUPPORTING_RECORD = "INDEPENDENT_SUPPORTING_RECORD"
    """A record from the SEPARATELY-SEEDED, independent supporting-record
    fixture (dispute_review.billing_fixtures.get_billing_support_records)
    -- authored and stored independently of the editable submission and
    never generated or overwritten from form values. Treated as trusted
    ground truth for comparison purposes (unlike SUBMITTED_UNVERIFIED),
    but is still a synthetic fixture, never a real external system."""

    SYNTHETIC_BILLING_POLICY = "SYNTHETIC_BILLING_POLICY"
    """A chunk of the synthetic, fictional billing/corrected-claim policy
    for this scenario, retrieved by REAL semantic vector search
    (dispute_review.billing_policy.search_billing_policy) over this
    scenario's own ISOLATED vector index -- ranked by similarity, never a
    real payer policy."""

    INDEPENDENT_PROVIDER_NETWORK_RELATIONSHIP = "INDEPENDENT_PROVIDER_NETWORK_RELATIONSHIP"
    """A graph edge from the SEPARATELY-SEEDED, isolated provider-network
    graph (dispute_review.billing_graph) -- never the shared golden-dataset
    graph (graph.retriever). Establishes only network participation, never
    coverage, applicability, or payment."""

    DETERMINISTIC_COMPARISON = "DETERMINISTIC_COMPARISON"
    """One row of dispute_review.comparison.compare_billing_correction's
    output."""


class EvidenceReference(BaseModel):
    """One citable item in the dispute evidence package -- the same shape
    across every evidence category (recorded facts, submitted fields,
    independent supporting records, policy sections, comparison findings),
    so the generator/judge can cite any of them uniformly.

    `ref_id` is always namespaced by category and built only from real
    fields already present on the underlying record/document/submission --
    never a fabricated id.

    `score` carries the vector retriever's own similarity score for
    SYNTHETIC_BILLING_POLICY references (see
    dispute_review.billing_policy.search_billing_policy) -- a ranking
    signal, never an accuracy or confidence probability. Every other
    category leaves it None.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref_id: str
    source_type: str
    provenance: str
    label: str
    detail: str
    score: Optional[float] = None


class DisputeEvidencePackage(BaseModel):
    """The complete evidence bundle assembled for one billing-correction
    submission against the one isolated original claim
    (investigate_dispute's return payload, wrapped in
    skills.base.SkillResult.evidence).

    `claim_snapshot` and `comparison_result` are the EXACT objects
    dispute_review.comparison.build_billing_claim_snapshot/
    compare_billing_correction already produce -- never re-derived or
    re-computed by a different code path, so this package can never
    disagree with the Dispute Review tab's own displayed result for the
    same submission. `comparison_result` is None only when
    `linkage_issue` is set (comparison was blocked -- see
    dispute_review.comparison.check_claim_member_linkage).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_snapshot: BillingClaimSnapshot
    submission: BillingCorrectionSubmission
    comparison_result: Optional[BillingComparisonResult] = None

    recorded_facts: list[EvidenceReference] = Field(default_factory=list)
    submitted_fields: list[EvidenceReference] = Field(default_factory=list)
    comparison_findings: list[EvidenceReference] = Field(default_factory=list)
    support_records: list[EvidenceReference] = Field(default_factory=list)
    policy_passages: list[EvidenceReference] = Field(default_factory=list)
    network_relationships: list[EvidenceReference] = Field(default_factory=list)

    # None when the submission's own claim/member identifiers are safe
    # (match this fixed claim, or are blank); set to a block reason
    # otherwise -- evidence retrieval/comparison is never silently carried
    # out against a different, ambiguous identity.
    linkage_issue: Optional[str] = None

    source_outcomes: list[EvidenceSourceOutcome] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
