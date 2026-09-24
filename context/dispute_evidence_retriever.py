"""Dispute-evidence assembly: combines the existing deterministic
comparator with three bounded evidence sources this codebase already has,
for one dispute-review submission against one existing claim.

    build_dispute_evidence_package(claim_id, submission) -> DisputeEvidencePackage

This is the dispute-evidence analogue of context/hybrid_retriever.py's
build_evidence_package -- same shape (structured + policy + graph ->
one typed evidence package, no LLM, no final answer), but built from a
DisputeSubmission instead of a free-text query, and additionally covering
a SUBMITTED provider/authorization reference the original pipeline has no
concept of.

Three evidence adapters, each wrapping an EXISTING reused function rather
than reimplementing retrieval:

  - gather_structured_evidence  -- tools.case_context (already-loaded
    CaseContext) + tools.provider_tool.get_provider /
    tools.prior_auth_tool.get_prior_authorization_by_id for the two
    SUBMITTED-identifier lookups (only performed when the submitted value
    actually differs from what's already recorded, to avoid redundant
    evidence).
  - gather_policy_evidence      -- rag.retriever.search_policy, with a
    small, deterministic query built from recorded service/denial reason,
    the comparator's own discrepancy categories, and normalized submitted
    fields (dispute_explanation is never folded into the query -- see its
    own docstring note below).
  - gather_graph_evidence       -- graph.retriever.get_claim_neighborhood
    + context.graph_filter.filter_claim_graph_context (claim's own
    relationships, reused exactly as the existing pipeline uses them) and
    graph.retriever.get_provider_neighborhood (the submitted provider's
    own FORWARD-ONLY relationships -- never an edge from some other claim
    that merely references the same provider hub).

Deliberately NOT reused here: application/scenario_lab.py's
_temporary_data_overlay. Nothing in this module mutates the DataStore or
the graph -- every function here only reads already-loaded singletons, and
never creates a node/edge for the newly submitted authorization or
provider (see gather_graph_evidence's docstring).

No Streamlit/session-state dependency, no LLM call, no iterative retrieval
loop -- one structured pass, one policy query, one claim-graph query, and
(conditionally) one submitted-provider-graph query, each exactly once.
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
from context.graph_filter import filter_claim_graph_context
from context.hybrid_retriever import _identify_missing_evidence
from context.models import StructuredEvidence
from dispute_review.comparison import build_claim_snapshot, compare_submission
from dispute_review.models import ComparisonStatus, DisputeComparisonResult, DisputeSubmission
from graph.builder import provider_node_id
from graph.retriever import NodeNotFoundError, get_claim_neighborhood, get_provider_neighborhood
from rag.chunking import LARGE
from rag.retriever import search_policy
from tools.case_context import CaseContext, get_case_context
from tools.models import Claim
from tools.prior_auth_tool import get_prior_authorization_by_id
from tools.provider_tool import get_provider

# Small, documented bounds -- no iterative retrieval loop anywhere in this
# module; each of these is used for exactly one call per evidence-gathering
# pass. Policy provider/config match context/hybrid_retriever.py's own
# defaults (DEFAULT_POLICY_PROVIDER/DEFAULT_POLICY_CONFIG) so dispute
# evidence and claim-investigation evidence are retrieved the same way.
DEFAULT_DISPUTE_POLICY_TOP_K = 3
DEFAULT_DISPUTE_GRAPH_MAX_HOPS = 2
DEFAULT_SUBMITTED_PROVIDER_GRAPH_MAX_HOPS = 1
DEFAULT_POLICY_PROVIDER = "semantic"
DEFAULT_POLICY_CONFIG = LARGE

# A small, explicit keyword set used ONLY to decide whether to append a
# documented limitation when the comparator found a Servicing Provider
# mismatch but nothing retrieved actually discusses changing/substituting
# a servicing provider. This is a deterministic string-containment check,
# never a semantic judgment -- see gather_policy_evidence.
_SERVICING_PROVIDER_CHANGE_KEYWORDS = (
    "change",
    "transfer",
    "substitut",
    "different facility",
    "different provider",
    "alternate provider",
    "alternate facility",
    "another facility",
)

# Deterministic, per-discrepancy-field query hints -- plain string
# composition, never an LLM. Only fields the comparator can actually
# report are covered; a field with no hint here still contributes nothing
# beyond the base service/denial-reason terms already in the query.
_DISCREPANCY_QUERY_HINTS: dict[str, str] = {
    "Validity Dates": "prior authorization effective date expiration date validity window",
    "Servicing Provider": "servicing provider facility network status",
}


@dataclass
class _GatherResult:
    """Internal accumulator returned by each gather_* adapter -- not part
    of the public evidence package shape (see DisputeEvidencePackage)."""

    references: list[EvidenceReference] = field(default_factory=list)
    outcomes: list[EvidenceSourceOutcome] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


# --- 1. structured evidence tool ----------------------------------------------------------


def gather_structured_evidence(case_context: CaseContext, submission: DisputeSubmission) -> _GatherResult:
    """Structured facts already on file (claim, member, plan, benefit,
    authorization candidates, servicing/ordering provider) plus, only when
    the SUBMITTED value actually differs from what's already recorded, a
    separate lookup of the submitted servicing provider id and/or the
    submitted authorization reference number.

    `case_context.claim` must not be None (caller's responsibility -- see
    build_dispute_evidence_package).
    """
    claim = case_context.claim
    references: list[EvidenceReference] = []
    outcomes: list[EvidenceSourceOutcome] = []
    conflicts: list[str] = []

    references.append(
        EvidenceReference(
            ref_id=f"claim:{claim.claim_id}",
            source_type="claim",
            provenance=Provenance.RECORDED,
            label="Claim record",
            detail=(
                f"status={claim.status} service_code={claim.service_code} "
                f"date_of_service={claim.date_of_service} "
                f"denial_reason_code={claim.denial_reason_code} "
                f"denial_reason_description={claim.denial_reason_description}"
            ),
        )
    )
    if case_context.member is not None:
        references.append(
            EvidenceReference(
                ref_id=f"member:{case_context.member.member_id}",
                source_type="member",
                provenance=Provenance.RECORDED,
                label="Member record",
                detail=f"plan_id={case_context.member.plan_id} status={case_context.member.status}",
            )
        )
    if case_context.plan is not None:
        references.append(
            EvidenceReference(
                ref_id=f"plan:{case_context.plan.plan_id}",
                source_type="plan",
                provenance=Provenance.RECORDED,
                label="Plan record",
                detail=f"plan_name={case_context.plan.plan_name} plan_type={case_context.plan.plan_type}",
            )
        )
    if case_context.benefit is not None:
        b = case_context.benefit
        references.append(
            EvidenceReference(
                ref_id=f"benefit:{b.benefit_id}",
                source_type="benefit",
                provenance=Provenance.RECORDED,
                label="Benefit rule",
                detail=(
                    f"covered={b.covered} requires_prior_auth={b.requires_prior_auth} "
                    f"network_requirement={b.network_requirement}"
                ),
            )
        )
    for auth in case_context.prior_authorizations:
        references.append(
            EvidenceReference(
                ref_id=f"auth:{auth.authorization_id}",
                source_type="authorization_candidate",
                provenance=Provenance.RECORDED,
                label="Prior authorization candidate (recorded for this member+service)",
                detail=(
                    f"status={auth.status} service_code={auth.service_code} "
                    f"effective_date={auth.effective_date} expiration_date={auth.expiration_date}"
                ),
            )
        )
    if case_context.servicing_provider is not None:
        p = case_context.servicing_provider
        references.append(
            EvidenceReference(
                ref_id=f"provider:servicing:{p.provider_id}",
                source_type="servicing_provider",
                provenance=Provenance.RECORDED,
                label="Claim's recorded servicing provider",
                detail=f"name={p.name} provider_type={p.provider_type} network_status={p.network_status}",
            )
        )
    if case_context.ordering_provider is not None:
        p = case_context.ordering_provider
        references.append(
            EvidenceReference(
                ref_id=f"provider:ordering:{p.provider_id}",
                source_type="ordering_provider",
                provenance=Provenance.RECORDED,
                label="Claim's recorded ordering provider (context only)",
                detail=f"name={p.name} provider_type={p.provider_type} network_status={p.network_status}",
            )
        )
    outcomes.append(
        EvidenceSourceOutcome(
            source="structured_case_context", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(references)
        )
    )

    # --- submitted servicing-provider lookup: only if it differs from the
    # claim's own recorded provider_id (STEP 3: "If the submitted
    # servicing-provider ID differs, look it up separately.")
    submitted_provider_id = submission.servicing_provider_id
    if submitted_provider_id and submitted_provider_id != claim.provider_id:
        try:
            provider = get_provider(submitted_provider_id)
        except ValueError:
            outcomes.append(
                EvidenceSourceOutcome(
                    source="submitted_provider_lookup",
                    status=Status.FAILURE,
                    detail="The submitted servicing provider ID could not be looked up (malformed identifier).",
                )
            )
        else:
            if provider is None:
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="submitted_provider_lookup",
                        status=Status.SUCCESS_NO_RESULTS,
                        detail="Not found in the accessible synthetic dataset.",
                    )
                )
            else:
                references.append(
                    EvidenceReference(
                        ref_id=f"submitted_provider_lookup:{provider.provider_id}",
                        source_type="submitted_provider_lookup",
                        provenance=Provenance.RECORDED_VIA_SUBMITTED_LOOKUP,
                        label=(
                            "Provider record found using the SUBMITTED servicing provider ID -- "
                            "NOT the claim's recorded servicing provider"
                        ),
                        detail=(
                            f"name={provider.name} provider_type={provider.provider_type} "
                            f"network_status={provider.network_status}"
                        ),
                    )
                )
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="submitted_provider_lookup", status=Status.SUCCESS_WITH_EVIDENCE, item_count=1
                    )
                )

    # --- submitted authorization-reference lookup: exact id match only,
    # against the FULL dataset (not scoped to this member+service, since
    # the submission's reference is arbitrary free text).
    submitted_ref = submission.authorization_reference_number
    if submitted_ref:
        existing_ids = {auth.authorization_id for auth in case_context.prior_authorizations}
        try:
            found = get_prior_authorization_by_id(submitted_ref)
        except ValueError:
            outcomes.append(
                EvidenceSourceOutcome(
                    source="submitted_authorization_lookup",
                    status=Status.FAILURE,
                    detail="The submitted authorization reference could not be looked up (malformed identifier).",
                )
            )
        else:
            if found is None:
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="submitted_authorization_lookup",
                        status=Status.SUCCESS_NO_RESULTS,
                        detail="Not found in the accessible synthetic dataset.",
                    )
                )
            elif found.authorization_id in existing_ids:
                # Already listed above as an authorization_candidate -- no
                # separate, differently-labeled reference for the same record.
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="submitted_authorization_lookup",
                        status=Status.SUCCESS_WITH_EVIDENCE,
                        item_count=1,
                        detail=f"Matches existing candidate {found.authorization_id}, already listed above.",
                    )
                )
            else:
                references.append(
                    EvidenceReference(
                        ref_id=f"submitted_authorization_lookup:{found.authorization_id}",
                        source_type="submitted_authorization_lookup",
                        provenance=Provenance.RECORDED_VIA_SUBMITTED_LOOKUP,
                        label=(
                            "Authorization record found using the SUBMITTED reference number -- "
                            "not confirmed to apply to this claim"
                        ),
                        detail=(
                            f"status={found.status} member_id={found.member_id} "
                            f"service_code={found.service_code} effective_date={found.effective_date} "
                            f"expiration_date={found.expiration_date}"
                        ),
                    )
                )
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="submitted_authorization_lookup", status=Status.SUCCESS_WITH_EVIDENCE, item_count=1
                    )
                )
                if found.member_id != claim.member_id or found.service_code != claim.service_code:
                    conflicts.append(
                        f"The submitted authorization reference {submitted_ref!r} resolves to a real "
                        f"record ({found.authorization_id}), but its member_id/service_code "
                        f"({found.member_id}/{found.service_code}) does not match this claim's recorded "
                        f"member/service ({claim.member_id}/{claim.service_code}) -- it may belong to a "
                        "different case."
                    )

    return _GatherResult(references=references, outcomes=outcomes, conflicts=conflicts)


# --- 2. vector policy retrieval tool ------------------------------------------------------


def _build_policy_query(claim: Claim, comparison_result: DisputeComparisonResult, submission: DisputeSubmission) -> str:
    """Bounded, deterministic query -- plain string composition, never an
    LLM. Built only from: the recorded service code, the recorded denial
    reason (if any), a fixed per-field hint for each discrepancy category
    the comparator actually found, and the submitted servicing provider id
    (a normalized structured field, useful for exact-term overlap with the
    policy corpus). `submission.dispute_explanation` is deliberately NEVER
    included: it is free-text, unverified narrative, and letting it expand
    or redirect retrieval scope would let submitted text issue retrieval
    instructions -- exactly what Module 6A must not do.
    """
    parts = [f"Service: {claim.service_code}."]
    if claim.denial_reason_code:
        parts.append(f"Denial reason: {claim.denial_reason_code}.")

    for row in comparison_result.rows:
        if row.status == ComparisonStatus.MATCH:
            continue
        hint = _DISCREPANCY_QUERY_HINTS.get(row.field)
        if hint:
            parts.append(f"Dispute discrepancy ({row.field}): {hint}.")

    if submission.servicing_provider_id:
        parts.append(f"Submitted servicing provider: {submission.servicing_provider_id}.")

    return " ".join(parts)


def gather_policy_evidence(
    claim: Claim,
    comparison_result: DisputeComparisonResult,
    submission: DisputeSubmission,
    *,
    top_k: int = DEFAULT_DISPUTE_POLICY_TOP_K,
) -> _GatherResult:
    """Retrieve up to top_k policy passages for one bounded, deterministic
    query (see _build_policy_query). Uses the SAME
    rag.retriever.search_policy the existing investigation pipeline uses
    -- no hardcoded passages, no iterative re-querying.

    Inspects the retrieved passage text for one specific, documented
    limitation: if the comparator found a Servicing Provider MISMATCH but
    nothing retrieved actually discusses changing/substituting a servicing
    provider, that gap is recorded explicitly rather than left implicit --
    a generic prior-authorization requirement does not by itself establish
    a rule about changing servicing providers.
    """
    references: list[EvidenceReference] = []
    outcomes: list[EvidenceSourceOutcome] = []
    limitations: list[str] = []

    query = _build_policy_query(claim, comparison_result, submission)
    try:
        results = search_policy(query, top_k=top_k, config=DEFAULT_POLICY_CONFIG, provider_name=DEFAULT_POLICY_PROVIDER)
    except ValueError:
        outcomes.append(
            EvidenceSourceOutcome(
                source="policy", status=Status.FAILURE, detail="Policy retrieval could not run for this request."
            )
        )
        return _GatherResult(references=references, outcomes=outcomes, limitations=limitations)
    except Exception:  # noqa: BLE001 -- external subsystem boundary; never leak raw exception text
        outcomes.append(
            EvidenceSourceOutcome(
                source="policy", status=Status.FAILURE, detail="Policy retrieval failed unexpectedly."
            )
        )
        return _GatherResult(references=references, outcomes=outcomes, limitations=limitations)

    if not results:
        outcomes.append(
            EvidenceSourceOutcome(
                source="policy",
                status=Status.SUCCESS_NO_RESULTS,
                detail="No policy passage met the retriever's relevance threshold for this query.",
            )
        )
    else:
        for chunk in results:
            references.append(
                EvidenceReference(
                    ref_id=f"policy:{chunk.chunk_id}",
                    source_type="policy",
                    provenance=Provenance.RETRIEVED_POLICY,
                    label=f"Policy [{chunk.section_id}] {chunk.section_title}",
                    detail=chunk.text,
                    score=chunk.score,
                )
            )
        outcomes.append(
            EvidenceSourceOutcome(source="policy", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(results))
        )

    servicing_mismatch = any(
        row.field == "Servicing Provider" and row.status == ComparisonStatus.MISMATCH
        for row in comparison_result.rows
    )
    if servicing_mismatch:
        combined_text = " ".join(ref.detail.lower() for ref in references)
        if not any(keyword in combined_text for keyword in _SERVICING_PROVIDER_CHANGE_KEYWORDS):
            limitations.append(
                "No retrieved policy passage directly addresses whether a change in servicing "
                "provider affects a previously authorized service; a generic prior-authorization "
                "requirement does not by itself establish that rule."
            )

    return _GatherResult(references=references, outcomes=outcomes, limitations=limitations)


# --- 3. graph retrieval tool ---------------------------------------------------------------


def gather_graph_evidence(
    claim_id: str,
    submission: DisputeSubmission,
    recorded_servicing_provider_id: str,
    *,
    max_hops: int = DEFAULT_DISPUTE_GRAPH_MAX_HOPS,
    submitted_provider_max_hops: int = DEFAULT_SUBMITTED_PROVIDER_GRAPH_MAX_HOPS,
) -> _GatherResult:
    """The claim's own recorded relationships (exact reuse of
    graph.retriever.get_claim_neighborhood + context.graph_filter's
    existing claim-relevance filter) plus, only when the submitted
    servicing-provider id differs from the claim's recorded one, that
    provider's own FORWARD-ONLY relationships.

    Forward-only is what prevents unrelated-claim/member evidence from
    leaking through the submitted provider as a shared hub: only edges
    whose source_id is the provider node itself are kept (e.g.
    PARTICIPATES_IN -> Network) -- an edge FROM some other claim that
    merely references this same provider (SERVICED_BY/ORDERED_BY) is never
    included, regardless of max_hops. This never creates a node or edge
    for the newly submitted authorization or provider -- it only reads the
    graph that already exists.
    """
    references: list[EvidenceReference] = []
    outcomes: list[EvidenceSourceOutcome] = []

    try:
        raw_context = get_claim_neighborhood(claim_id, max_hops=max_hops)
    except NodeNotFoundError:
        outcomes.append(
            EvidenceSourceOutcome(
                source="graph_claim", status=Status.SUCCESS_NO_RESULTS, detail="Not found in the accessible synthetic dataset."
            )
        )
    except Exception:  # noqa: BLE001
        outcomes.append(
            EvidenceSourceOutcome(
                source="graph_claim", status=Status.FAILURE, detail="Claim graph retrieval failed unexpectedly."
            )
        )
    else:
        filtered_context = filter_claim_graph_context(raw_context)
        if filtered_context.relationships:
            for rel in filtered_context.relationships:
                references.append(
                    EvidenceReference(
                        ref_id=f"graph:{rel.source_id}--{rel.relation}-->{rel.target_id}",
                        source_type="graph_claim",
                        provenance=Provenance.RECORDED_RELATIONSHIP,
                        label="Recorded graph relationship (the claim's own)",
                        detail=f"{rel.source_id} --{rel.relation}--> {rel.target_id}",
                    )
                )
            outcomes.append(
                EvidenceSourceOutcome(
                    source="graph_claim", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(filtered_context.relationships)
                )
            )
        else:
            outcomes.append(
                EvidenceSourceOutcome(
                    source="graph_claim", status=Status.SUCCESS_NO_RESULTS, detail="No claim-relevant relationships were found."
                )
            )

    submitted_provider_id = submission.servicing_provider_id
    if submitted_provider_id and submitted_provider_id != recorded_servicing_provider_id:
        try:
            provider_context = get_provider_neighborhood(submitted_provider_id, max_hops=submitted_provider_max_hops)
        except NodeNotFoundError:
            outcomes.append(
                EvidenceSourceOutcome(
                    source="graph_submitted_provider",
                    status=Status.SUCCESS_NO_RESULTS,
                    detail="Not found in the accessible synthetic dataset.",
                )
            )
        except Exception:  # noqa: BLE001
            outcomes.append(
                EvidenceSourceOutcome(
                    source="graph_submitted_provider",
                    status=Status.FAILURE,
                    detail="Submitted-provider graph retrieval failed unexpectedly.",
                )
            )
        else:
            root_node = provider_node_id(submitted_provider_id)
            forward_edges = [rel for rel in provider_context.relationships if rel.source_id == root_node]
            if forward_edges:
                for rel in forward_edges:
                    references.append(
                        EvidenceReference(
                            ref_id=f"graph_submitted_provider:{rel.source_id}--{rel.relation}-->{rel.target_id}",
                            source_type="graph_submitted_provider",
                            provenance=Provenance.RECORDED_RELATIONSHIP,
                            label="Recorded graph relationship of the SUBMITTED provider (not the claim's own)",
                            detail=f"{rel.source_id} --{rel.relation}--> {rel.target_id}",
                        )
                    )
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="graph_submitted_provider", status=Status.SUCCESS_WITH_EVIDENCE, item_count=len(forward_edges)
                    )
                )
            else:
                outcomes.append(
                    EvidenceSourceOutcome(
                        source="graph_submitted_provider",
                        status=Status.SUCCESS_NO_RESULTS,
                        detail="The submitted provider exists in the graph but has no recorded outgoing relationships.",
                    )
                )

    return _GatherResult(references=references, outcomes=outcomes)


# --- submitted-field and comparison-finding references (no retrieval involved) ------------


def _build_submitted_field_references(submission: DisputeSubmission) -> list[EvidenceReference]:
    """One reference per non-missing submitted field, verbatim -- always
    succeeds (there is nothing to retrieve; this is just the submission
    itself). Every reference here uses provenance=SUBMITTED_UNVERIFIED.
    """
    references: list[EvidenceReference] = []

    def add(field_name: str, value: Optional[str]) -> None:
        if not value:
            return
        references.append(
            EvidenceReference(
                ref_id=f"submitted:{field_name}",
                source_type="submitted_field",
                provenance=Provenance.SUBMITTED_UNVERIFIED,
                label=f"Submitted {field_name.replace('_', ' ')}",
                detail=value,
            )
        )

    add("authorization_reference_number", submission.authorization_reference_number)
    add("member_id", submission.member_id)
    add("service_code", submission.service_code)
    add(
        "authorization_start_date",
        submission.authorization_start_date.isoformat() if submission.authorization_start_date else None,
    )
    add(
        "authorization_end_date",
        submission.authorization_end_date.isoformat() if submission.authorization_end_date else None,
    )
    add("servicing_provider_id", submission.servicing_provider_id)
    add("supplied_by", submission.supplied_by.value)
    add("dispute_explanation", submission.dispute_explanation)
    return references


def _build_comparison_findings(result: DisputeComparisonResult) -> list[EvidenceReference]:
    """One reference per comparison row -- always exactly four, matching
    dispute_review.models.DisputeComparisonResult's own invariant."""
    references: list[EvidenceReference] = []
    for row in result.rows:
        slug = row.field.lower().replace(" ", "_")
        references.append(
            EvidenceReference(
                ref_id=f"comparison:{slug}",
                source_type="comparison_finding",
                provenance=Provenance.DETERMINISTIC_COMPARISON,
                label=f"Comparison finding: {row.field} ({row.status.value})",
                detail=row.explanation,
            )
        )
    return references


# --- assembly --------------------------------------------------------------------------


def build_dispute_evidence_package(
    claim_id: str,
    submission: DisputeSubmission,
    *,
    policy_top_k: int = DEFAULT_DISPUTE_POLICY_TOP_K,
    graph_max_hops: int = DEFAULT_DISPUTE_GRAPH_MAX_HOPS,
) -> DisputeEvidencePackage:
    """Assemble the full dispute evidence package for one submission
    against one existing claim.

    1. Read CaseContext (tools.case_context.get_case_context) -- raises
       ValueError if no matching claim exists (caller -- skills.investigate_dispute
       -- maps this to SkillStatus.NOT_FOUND, exactly like
       skills.investigate_claim's own NOT_FOUND path).
    2. Build the claim snapshot and run the EXISTING deterministic
       comparator (dispute_review.comparison.build_claim_snapshot /
       compare_submission) -- never re-derived here.
    3. Gather structured, policy, and graph evidence (the three adapters
       above), each exactly once.
    4. Assemble everything into one DisputeEvidencePackage. No generation,
       no sufficiency judgment -- see this module's docstring.
    """
    case_context = get_case_context(claim_id)
    if case_context.claim is None:
        raise ValueError(f"No matching claim was found for claim_id {claim_id!r}.")

    claim_snapshot = build_claim_snapshot(case_context)
    comparison_result = compare_submission(claim_snapshot, submission)

    structured_result = gather_structured_evidence(case_context, submission)
    policy_result = gather_policy_evidence(case_context.claim, comparison_result, submission, top_k=policy_top_k)
    graph_result = gather_graph_evidence(
        claim_id, submission, case_context.claim.provider_id, max_hops=graph_max_hops
    )

    # Reuses context.hybrid_retriever's own missing-structured-evidence
    # logic verbatim (AGENTS.md rule 4: do not re-derive retrieval/business
    # rules elsewhere) -- StructuredEvidence adds no fields over
    # CaseContext, so this is a pure relabeling, not a new computation.
    structured_facts = StructuredEvidence(**case_context.model_dump())
    # Module 6D live-check fix: deliberately NOT wrapped in square brackets.
    # A prior "[category] description" format visually collided with the
    # EVIDENCE section's "[ref_id] ..." citation format (both used square
    # brackets around a short identifier-shaped token), and a live model
    # cited the bracketed category name itself ("prior_authorization") as a
    # fabricated evidence_refs entry -- confirmed via a live smoke check,
    # see docs/DISPUTE_AI_VALIDATION.md. This formatting is not itself a
    # citable reference id and was never intended to be one; the colon
    # form below removes the visual collision at its source. See also
    # prompts/dispute_brief_prompt.py's strengthened citation instructions.
    missing_evidence = [
        f"{item.category}: {item.description}" for item in _identify_missing_evidence(structured_facts)
    ]

    limitations = list(policy_result.limitations) + [
        "Absence of a matching record in this synthetic dataset is not proof that the record does "
        "not exist elsewhere.",
        "Graph relationships and structured facts are drawn from the same underlying synthetic "
        "dataset and should not be treated as independent confirmation of one another.",
        "A retrieved policy passage's applicability to this specific plan, service, and dispute is "
        "not independently confirmed by retrieval alone.",
    ]

    return DisputeEvidencePackage(
        claim_id=claim_id,
        claim_snapshot=claim_snapshot,
        submission=submission,
        comparison_result=comparison_result,
        recorded_facts=structured_result.references,
        submitted_fields=_build_submitted_field_references(submission),
        comparison_findings=_build_comparison_findings(comparison_result),
        policy_passages=policy_result.references,
        graph_relationships=graph_result.references,
        source_outcomes=structured_result.outcomes + policy_result.outcomes + graph_result.outcomes,
        missing_evidence=missing_evidence,
        conflicts=structured_result.conflicts,
        limitations=limitations,
    )
