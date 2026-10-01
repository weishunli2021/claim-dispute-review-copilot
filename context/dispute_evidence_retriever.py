"""Dispute-evidence assembly for the billing-correction scenario: combines
the deterministic billing comparator with the scenario's own isolated
evidence sources, for one BillingCorrectionSubmission against the one fixed
original claim (CLM-BILL-9001).

    build_dispute_evidence_package(claim_id, submission) -> DisputeEvidencePackage

Four evidence sources, each bounded and non-iterative -- never an
autonomous retrieval loop, never more than one embedding-model call per
source per run:

  - gather_recorded_evidence          -- the isolated original claim +
    recorded decision (dispute_review.billing_fixtures.get_billing_claim_record).
  - gather_support_record_evidence    -- the independent supporting service
    records (dispute_review.billing_fixtures.get_billing_support_records).
    Authored and stored separately from the editable submission; never
    generated or overwritten from form values.
  - gather_policy_evidence            -- REAL semantic vector search (top-k,
    ranked) over the synthetic billing policy's own ISOLATED index
    (dispute_review.billing_policy.search_billing_policy) -- the LOCAL
    "semantic" embedding provider, no API key, no paid model call.
  - gather_network_evidence           -- the isolated provider-network graph
    (dispute_review.billing_graph) for the original and (if different) the
    proposed servicing-provider id -- a SEPARATE graph, never the shared
    golden-dataset graph.

Before any comparison, support-record, policy, or network gathering, this
module checks dispute_review.comparison.check_claim_member_linkage: if the
submission's own claim/member identifiers have been changed away from this
fixed claim's actual identifiers, evidence retrieval is BLOCKED (an
explicit `linkage_issue` on the returned package, `comparison_result=None`)
rather than silently proceeding as if the identity were still safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from context.dispute_evidence_models import (
    DisputeEvidencePackage,
    EvidenceProvenanceCategory as Provenance,
    EvidenceReference,
    EvidenceSourceOutcome,
    EvidenceSourceStatus as Status,
)
from dispute_review.billing_fixtures import (
    BILLING_CLAIM_ID,
    BillingSupportRecord,
    get_billing_claim_record,
    get_billing_support_records,
)
from dispute_review.billing_graph import (
    DEFAULT_PLAN_ID,
    get_billing_network_fixture,
    get_billing_network_plan,
    is_provider_in_plan_network,
)
from dispute_review.billing_policy import BillingPolicyLoadError, search_billing_policy
from dispute_review.comparison import (
    build_billing_claim_snapshot,
    check_claim_member_linkage,
    compare_billing_correction,
)
from dispute_review.models import (
    BillingClaimSnapshot,
    BillingComparisonResult,
    BillingCorrectionSubmission,
    BillingFindingStatus,
)

DEFAULT_BILLING_POLICY_TOP_K = 3


@dataclass
class _GatherResult:
    """Internal accumulator returned by each gather_* adapter -- not part
    of the public evidence package shape (see DisputeEvidencePackage)."""

    references: list[EvidenceReference] = field(default_factory=list)
    outcomes: list[EvidenceSourceOutcome] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


# --- 1. recorded claim + decision -----------------------------------------------------------


def gather_recorded_evidence(claim: BillingClaimSnapshot) -> _GatherResult:
    """The isolated original claim and its recorded decision, exactly as
    seeded -- never re-derived, never re-worded."""
    references = [
        EvidenceReference(
            ref_id=f"claim:{claim.claim_id}",
            source_type="claim",
            provenance=Provenance.RECORDED,
            label="Original claim record",
            detail=(
                f"claim_type={claim.claim_type} submission_date={claim.submission_date} "
                f"member_id={claim.member_id} plan_id={claim.plan_id} "
                f"billing_provider_id={claim.billing_provider_id} "
                f"servicing_provider_id={claim.servicing_provider_id} "
                f"service_date={claim.service_date} service_code={claim.service_code} "
                f"modifier={claim.modifier} units={claim.units} "
                f"place_of_service={claim.place_of_service} diagnosis_code={claim.diagnosis_code} "
                f"charge_per_unit={claim.charge_per_unit} total_billed={claim.total_billed} "
                f"authorization_reference={claim.authorization_reference}"
            ),
        ),
        EvidenceReference(
            ref_id=f"decision:{claim.claim_id}",
            source_type="recorded_decision",
            provenance=Provenance.RECORDED,
            label="Recorded decision on the original claim",
            detail=(
                f"status={claim.decision_status} decision_date={claim.decision_date} "
                f"decision_code={claim.decision_code} decision_reason={claim.decision_reason} "
                f"decision_explanation={claim.decision_explanation} "
                f"decision_flagged_fields={claim.decision_flagged_fields} "
                f"allowed_amount={claim.allowed_amount if claim.allowed_amount_recorded else 'NOT RECORDED'} "
                f"paid_amount={claim.paid_amount if claim.paid_amount_recorded else 'NOT RECORDED'}"
            ),
        ),
    ]
    outcomes = [
        EvidenceSourceOutcome(
            source="billing_claim_record", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(references)
        )
    ]
    return _GatherResult(references=references, outcomes=outcomes)


# --- 2. independent supporting service records ----------------------------------------------


def gather_support_record_evidence(records: list[BillingSupportRecord]) -> _GatherResult:
    """The independent supporting service records for this claim, verbatim
    -- authored and stored separately from the editable submission."""
    if not records:
        return _GatherResult(
            outcomes=[
                EvidenceSourceOutcome(
                    source="independent_support_records",
                    status=Status.SUCCESS_NO_RESULTS,
                    detail="No independent supporting records are on file for this claim.",
                )
            ]
        )
    references = [
        EvidenceReference(
            ref_id=f"support:{record.record_id}",
            source_type="independent_support_record",
            provenance=Provenance.INDEPENDENT_SUPPORTING_RECORD,
            label=f"{record.record_type} ({record.record_id})",
            detail=(
                f"record_date={record.record_date} author={record.author} summary={record.summary} "
                f"supports_service_code={record.supports_service_code} "
                f"supports_modifier={record.supports_modifier} supports_units={record.supports_units} "
                f"supports_servicing_provider_id={record.supports_servicing_provider_id}"
            ),
        )
        for record in records
    ]
    outcomes = [
        EvidenceSourceOutcome(
            source="independent_support_records", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(references)
        )
    ]
    return _GatherResult(references=references, outcomes=outcomes)


# --- 3. synthetic billing policy: REAL semantic vector search ------------------------------


def _build_billing_policy_query(comparison_result: Optional[BillingComparisonResult]) -> str:
    """Bounded, deterministic query -- plain string composition, never an
    LLM and never the submission's own free-text correction_explanation
    (letting submitted text steer retrieval scope is exactly what this
    layer must not do). Built only from the four fixed field names plus,
    when available, which fields the comparator found CONFLICTS or
    INSUFFICIENT_EVIDENCE on -- those are the fields most likely to need
    the analyst-verification and corrected-claim-review policy language.
    """
    parts = ["Billing correction review: service code, modifier, units, servicing provider."]
    if comparison_result is not None:
        for row in comparison_result.rows:
            if row.status in (BillingFindingStatus.CONFLICTS, BillingFindingStatus.INSUFFICIENT_EVIDENCE):
                parts.append(f"{row.field}: {row.status.value}.")
    return " ".join(parts)


def gather_policy_evidence(comparison_result: Optional[BillingComparisonResult] = None) -> _GatherResult:
    """Retrieve up to DEFAULT_BILLING_POLICY_TOP_K policy chunks by REAL
    semantic vector search (dispute_review.billing_policy.search_billing_policy)
    over this scenario's own isolated index -- ranked by similarity to a
    bounded, deterministic query (see _build_billing_policy_query). Uses
    the LOCAL "semantic" embedding provider (no API key, no paid model
    call) -- the same provider context/hybrid_retriever.py already uses by
    default for the original investigation pipeline.
    """
    query = _build_billing_policy_query(comparison_result)
    try:
        results = search_billing_policy(query, top_k=DEFAULT_BILLING_POLICY_TOP_K)
    except BillingPolicyLoadError as exc:
        return _GatherResult(
            outcomes=[EvidenceSourceOutcome(source="billing_policy", status=Status.FAILURE, detail=str(exc))]
        )
    except Exception:  # noqa: BLE001 -- external subsystem boundary (embedding model / vector store)
        return _GatherResult(
            outcomes=[
                EvidenceSourceOutcome(
                    source="billing_policy", status=Status.FAILURE, detail="Policy retrieval failed unexpectedly."
                )
            ]
        )

    if not results:
        return _GatherResult(
            outcomes=[
                EvidenceSourceOutcome(
                    source="billing_policy",
                    status=Status.SUCCESS_NO_RESULTS,
                    detail="No policy chunk met the retriever's relevance threshold for this query.",
                )
            ]
        )
    references = [
        EvidenceReference(
            ref_id=f"policy:{chunk.chunk_id}",
            source_type="policy",
            provenance=Provenance.SYNTHETIC_BILLING_POLICY,
            label=f"Policy [{chunk.section_id}] {chunk.section_title}",
            detail=chunk.text,
            score=chunk.score,
        )
        for chunk in results
    ]
    outcomes = [
        EvidenceSourceOutcome(source="billing_policy", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(references))
    ]
    return _GatherResult(references=references, outcomes=outcomes)


# --- 4. isolated provider-network graph -----------------------------------------------------


def gather_network_evidence(
    original_provider_id: Optional[str],
    proposed_provider_id: Optional[str],
    *,
    plan_id: str = DEFAULT_PLAN_ID,
) -> _GatherResult:
    """Check network participation for the original claim's servicing
    provider and, if different, the proposed one, against `plan_id`'s
    required network in this scenario's own ISOLATED provider-network
    graph (dispute_review.billing_graph) -- never the shared golden-dataset
    graph. Establishes network participation only, never coverage,
    applicability, or payment.

    `plan_id` defaults to the claim's own actually-recorded plan
    (dispute_review.billing_graph.DEFAULT_PLAN_ID); a caller may pass a
    different known plan_id (dispute_review/ui.py's optional "Network
    check plan" control) to check the same providers against a different
    plan's required network -- this never changes the claim's own recorded
    plan_id/plan_name shown in Section 1, only which network this ONE
    check compares against.
    """
    provider_ids = [pid for pid in {original_provider_id, proposed_provider_id} if pid]
    if not provider_ids:
        return _GatherResult(
            outcomes=[
                EvidenceSourceOutcome(
                    source="provider_network", status=Status.SUCCESS_NO_RESULTS, detail="No servicing provider id to check."
                )
            ]
        )

    fixture = get_billing_network_fixture()
    plan = get_billing_network_plan(fixture, plan_id)
    name_by_id = {p.provider_id: p.name for p in fixture.providers}
    network_by_id = {p.provider_id: p.network_name for p in fixture.providers}
    references: list[EvidenceReference] = []
    not_found: list[str] = []

    for provider_id in sorted(provider_ids):
        in_network, _neighborhood = is_provider_in_plan_network(provider_id, plan_id=plan_id)
        if in_network is None:
            not_found.append(provider_id)
            continue
        references.append(
            EvidenceReference(
                ref_id=f"network:{provider_id}",
                source_type="provider_network",
                provenance=Provenance.INDEPENDENT_PROVIDER_NETWORK_RELATIONSHIP,
                label=f"Provider network participation ({provider_id})",
                detail=(
                    f"provider_id={provider_id} name={name_by_id.get(provider_id, 'unknown')} "
                    f"participates_in={network_by_id.get(provider_id, 'unknown')!r} "
                    f"plan_id={plan.plan_id!r} plan_name={plan.plan_name!r} "
                    f"plan_requires={plan.plan_network_name!r} in_network={in_network}"
                ),
            )
        )

    outcomes: list[EvidenceSourceOutcome] = []
    if references:
        outcomes.append(
            EvidenceSourceOutcome(
                source="provider_network", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(references)
            )
        )
    if not_found:
        outcomes.append(
            EvidenceSourceOutcome(
                source="provider_network",
                status=Status.SUCCESS_NO_RESULTS,
                detail=f"Not found in the isolated provider-network graph: {', '.join(not_found)}.",
            )
        )
    return _GatherResult(references=references, outcomes=outcomes)


# --- submitted-field and comparison-finding references (no retrieval involved) --------------


def _build_submitted_field_references(submission: BillingCorrectionSubmission) -> list[EvidenceReference]:
    """One reference per non-missing submitted field, verbatim -- always
    succeeds (there is nothing to retrieve; this is just the submission
    itself). Every reference here uses provenance=SUBMITTED_UNVERIFIED."""
    references: list[EvidenceReference] = []

    def add(field_name: str, value: object) -> None:
        if value is None or value == "":
            return
        references.append(
            EvidenceReference(
                ref_id=f"submitted:{field_name}",
                source_type="submitted_field",
                provenance=Provenance.SUBMITTED_UNVERIFIED,
                label=f"Submitted {field_name.replace('_', ' ')}",
                detail=str(value),
            )
        )

    add("supplied_by", submission.supplied_by)
    add("original_claim_reference", submission.original_claim_reference)
    add("member_id", submission.member_id)
    add("service_date", submission.service_date.isoformat() if submission.service_date else None)
    add("service_code", submission.service_code)
    add("modifier", submission.modifier)
    add("units", submission.units)
    add("servicing_provider_id", submission.servicing_provider_id)
    add("proposed_billed_amount", submission.proposed_billed_amount)
    add("correction_explanation", submission.correction_explanation)
    if submission.supporting_record_references:
        add("supporting_record_references", ", ".join(submission.supporting_record_references))
    return references


def _build_comparison_findings(result: BillingComparisonResult) -> list[EvidenceReference]:
    """One reference per comparison row -- always exactly four, matching
    dispute_review.models.BillingComparisonResult's own invariant."""
    return [
        EvidenceReference(
            ref_id=f"comparison:{row.field.lower().replace(' ', '_')}",
            source_type="comparison_finding",
            provenance=Provenance.DETERMINISTIC_COMPARISON,
            label=f"Comparison finding: {row.field} ({row.status.value})",
            detail=row.explanation,
        )
        for row in result.rows
    ]


def _identify_missing_evidence(comparison_result: BillingComparisonResult) -> list[str]:
    return [
        f"{row.field}: independent supporting records do not establish a single value for this field."
        for row in comparison_result.rows
        if row.status == BillingFindingStatus.INSUFFICIENT_EVIDENCE
    ]


# --- assembly --------------------------------------------------------------------------


def build_dispute_evidence_package(
    claim_id: str, submission: BillingCorrectionSubmission, *, plan_id: str = DEFAULT_PLAN_ID
) -> DisputeEvidencePackage:
    """Assemble the full dispute evidence package for one billing-correction
    submission against the one fixed original claim.

    Raises ValueError if `claim_id` is not this scenario's fixed claim id
    -- the caller (skills.investigate_dispute) maps this to
    SkillStatus.NOT_FOUND, exactly like the retired scenario's own
    unknown-claim path.

    `plan_id` is passed straight through to gather_network_evidence --
    see its own docstring for what it does and does not change.
    """
    if claim_id != BILLING_CLAIM_ID:
        raise ValueError(f"No matching claim was found for claim_id {claim_id!r}.")

    claim_record = get_billing_claim_record()
    claim_snapshot = build_billing_claim_snapshot(claim_record)

    recorded_result = gather_recorded_evidence(claim_snapshot)
    submitted_fields = _build_submitted_field_references(submission)

    linkage_issue = check_claim_member_linkage(claim_snapshot, submission)

    if linkage_issue is not None:
        return DisputeEvidencePackage(
            claim_id=claim_id,
            claim_snapshot=claim_snapshot,
            submission=submission,
            comparison_result=None,
            recorded_facts=recorded_result.references,
            submitted_fields=submitted_fields,
            comparison_findings=[],
            support_records=[],
            policy_passages=[],
            network_relationships=[],
            linkage_issue=linkage_issue,
            source_outcomes=(
                recorded_result.outcomes
                + [
                    EvidenceSourceOutcome(
                        source="independent_support_records",
                        status=Status.FAILURE,
                        detail="Skipped: claim/member linkage issue.",
                    ),
                    EvidenceSourceOutcome(
                        source="billing_policy", status=Status.FAILURE, detail="Skipped: claim/member linkage issue."
                    ),
                    EvidenceSourceOutcome(
                        source="provider_network", status=Status.FAILURE, detail="Skipped: claim/member linkage issue."
                    ),
                ]
            ),
            missing_evidence=[],
            conflicts=[linkage_issue],
            limitations=[],
        )

    support_records = get_billing_support_records()
    support_result = gather_support_record_evidence(support_records)
    comparison_result = compare_billing_correction(claim_snapshot, submission, support_records)
    comparison_findings = _build_comparison_findings(comparison_result)
    policy_result = gather_policy_evidence(comparison_result)
    network_result = gather_network_evidence(
        claim_snapshot.servicing_provider_id, submission.servicing_provider_id, plan_id=plan_id
    )

    conflicts = [
        f"{row.field}: {row.explanation}" for row in comparison_result.rows if row.status == BillingFindingStatus.CONFLICTS
    ]

    limitations = list(policy_result.limitations) + [
        "Absence of a matching independent record for a field is not proof that supporting "
        "documentation does not exist elsewhere; it is only absent from this scenario's fixed "
        "record set.",
        "An analyst should still confirm the independent supporting records were not altered to "
        "match the requested correction (see the synthetic policy's own verification checklist).",
        "Provider network participation establishes network status only -- never coverage, "
        "applicability, or payment.",
    ]

    return DisputeEvidencePackage(
        claim_id=claim_id,
        claim_snapshot=claim_snapshot,
        submission=submission,
        comparison_result=comparison_result,
        recorded_facts=recorded_result.references,
        submitted_fields=submitted_fields,
        comparison_findings=comparison_findings,
        support_records=support_result.references,
        policy_passages=policy_result.references,
        network_relationships=network_result.references,
        linkage_issue=None,
        source_outcomes=(
            recorded_result.outcomes + support_result.outcomes + policy_result.outcomes + network_result.outcomes
        ),
        missing_evidence=_identify_missing_evidence(comparison_result),
        conflicts=conflicts,
        limitations=limitations,
    )
