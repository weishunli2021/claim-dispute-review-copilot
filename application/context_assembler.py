"""Assembles a labeled, budgeted AssembledContext from an already-built
EvidencePackage. This is the "Context Assembler" box in
docs/architecture.md's target diagram, downstream of the existing GraphRAG
evidence-assembly layer (context/) -- it does NOT re-run retrieval, re-call
tools.case_context, or re-derive evidence sufficiency. It only relabels and
budgets evidence that agents.case_agent.run_case_agent already produced in
one execution (see application/investigation_service.py).

Every reference id below (REF_ID_*) is built deterministically from a real
field already present on the source record (claim_id, authorization_id,
chunk_id, a graph edge's own source/relation/target triple, ...) -- never
an invented database id. Where the underlying model has no id of its own
(graph.models.GraphRelationship), the composite of its own source_id,
relation, and target_id fields is used as the reference id: still built
from real fields, not fabricated.
"""

from __future__ import annotations

from agents.state import AgentResult
from application.models import AssembledContext, ContextReference
from context.models import EvidencePackage

# Character budget (NOT a token count -- see module docstring below) applied
# only to policy-excerpt text, the one field whose length varies widely.
# Structured facts, authorization candidates, and missing-information labels
# are never truncated: they are small, bounded, and considered essential.
# At the pipeline's current defaults (top_k_policy=3, ~900-char LARGE
# chunks), 3 policy excerpts total well under this budget in every
# verified case, so truncation does not fire today -- the mechanism exists
# and is tested (tests/test_application_context_assembler.py), not tuned
# against these five cases.
MAX_POLICY_CONTEXT_CHARS = 6000
TRUNCATED_CHUNK_KEEP_CHARS = 400


def assemble_context(agent_result: AgentResult) -> AssembledContext:
    """Build an AssembledContext from an AgentResult that already carries a
    complete EvidencePackage (agent_result.evidence_package is not None).

    Reads agent_result.evidence_package and agent_result.status /
    agent_result.missing_information only -- never mutates the
    EvidencePackage or any of its nested Pydantic objects (Pydantic models
    are immutable-by-convention here; this function only reads `.model_dump()`
    -free attributes and appends to new lists/dicts it owns).
    """
    if agent_result.evidence_package is None:
        raise ValueError(
            "assemble_context requires agent_result.evidence_package to be set "
            "(caller should only assemble context when the agent reached "
            "EVIDENCE_SUFFICIENT)."
        )
    package: EvidencePackage = agent_result.evidence_package
    sf = package.structured_facts

    references: list[ContextReference] = []
    truncation_notes: list[str] = []

    if sf.claim is not None:
        references.append(
            ContextReference(
                ref_id=f"claim:{sf.claim.claim_id}",
                kind="claim",
                label="Claim record",
                detail=(
                    f"status={sf.claim.status} service_code={sf.claim.service_code} "
                    f"date_of_service={sf.claim.date_of_service} "
                    f"billed_amount={sf.claim.billed_amount} allowed_amount={sf.claim.allowed_amount} "
                    f"denial_reason_code={sf.claim.denial_reason_code} "
                    f"denial_reason_description={sf.claim.denial_reason_description}"
                ),
            )
        )

    if sf.member is not None:
        references.append(
            ContextReference(
                ref_id=f"member:{sf.member.member_id}",
                kind="member",
                label="Member record",
                detail=f"plan_id={sf.member.plan_id} status={sf.member.status}",
            )
        )

    if sf.plan is not None:
        references.append(
            ContextReference(
                ref_id=f"plan:{sf.plan.plan_id}",
                kind="plan",
                label="Plan record",
                detail=f"plan_name={sf.plan.plan_name} plan_type={sf.plan.plan_type}",
            )
        )

    # Present-but-not-covered (Benefit.covered=False) is a real record and
    # gets a reference like any other; a genuinely absent benefit record
    # (sf.benefit is None) gets NO reference here -- its absence is already
    # carried on AssembledContext.missing_information, never invented.
    if sf.benefit is not None:
        references.append(
            ContextReference(
                ref_id=f"benefit:{sf.benefit.benefit_id}",
                kind="benefit",
                label="Benefit rule",
                detail=(
                    f"covered={sf.benefit.covered} "
                    f"requires_prior_auth={sf.benefit.requires_prior_auth} "
                    f"network_requirement={sf.benefit.network_requirement} "
                    f"notes={sf.benefit.notes}"
                ),
            )
        )

    # Every prior-authorization CANDIDATE is preserved as its own reference,
    # keyed by its own authorization_id -- this is what keeps both of Case
    # 2's records (PA-1501, PA-2001) distinct. No date/claim matching is
    # performed here or anywhere upstream; see the "matching" vs
    # "preserving candidates" distinction in docs/HUMANA_BUILD_STATUS.md.
    for auth in sf.prior_authorizations:
        references.append(
            ContextReference(
                ref_id=f"auth:{auth.authorization_id}",
                kind="authorization",
                label="Prior authorization candidate",
                detail=(
                    f"status={auth.status} service_code={auth.service_code} "
                    f"effective_date={auth.effective_date} expiration_date={auth.expiration_date}"
                ),
            )
        )

    if sf.servicing_provider is not None:
        references.append(
            ContextReference(
                ref_id=f"provider:servicing:{sf.servicing_provider.provider_id}",
                kind="servicing_provider",
                label="Servicing provider",
                detail=(
                    f"network_status={sf.servicing_provider.network_status} "
                    f"specialty={sf.servicing_provider.specialty} "
                    f"network_name={sf.servicing_provider.network_name}"
                ),
            )
        )

    if sf.ordering_provider is not None:
        references.append(
            ContextReference(
                ref_id=f"provider:ordering:{sf.ordering_provider.provider_id}",
                kind="ordering_provider",
                label="Ordering provider",
                detail=(
                    f"network_status={sf.ordering_provider.network_status} "
                    f"specialty={sf.ordering_provider.specialty} "
                    f"network_name={sf.ordering_provider.network_name}"
                ),
            )
        )

    policy_char_budget = MAX_POLICY_CONTEXT_CHARS
    for chunk in package.policy_chunks:
        text = chunk.text
        ref_id = f"policy:{chunk.chunk_id}"
        if len(text) > policy_char_budget:
            kept = max(policy_char_budget, 0)
            truncated_text = text[:kept] + " …[truncated]"
            truncation_notes.append(
                f"{ref_id}: kept {kept} of {len(text)} chars (policy context budget "
                f"{MAX_POLICY_CONTEXT_CHARS} exhausted; explicitly flagged, not silent)."
            )
            text = truncated_text
            policy_char_budget = 0
        elif len(text) > 0:
            policy_char_budget -= len(text)
        references.append(
            ContextReference(
                ref_id=ref_id,
                kind="policy",
                label=f"Policy [{chunk.section_id}] {chunk.section_title}",
                detail=text,
            )
        )

    for rel in package.graph_relationships:
        ref_id = f"graph:{rel.source_id}--{rel.relation}-->{rel.target_id}"
        references.append(
            ContextReference(
                ref_id=ref_id,
                kind="graph_relationship",
                label="Graph relationship",
                detail=f"{rel.source_id} --{rel.relation}--> {rel.target_id}",
            )
        )

    references = _dedupe_by_ref_id(references)

    return AssembledContext(
        claim_id=package.claim_id,
        original_query=package.original_query,
        enriched_query=package.enriched_query,
        evidence_sufficiency_status=agent_result.status.value,
        missing_information=list(agent_result.missing_information),
        claim_status=sf.claim.status if sf.claim is not None else None,
        denial_reason_code=sf.claim.denial_reason_code if sf.claim is not None else None,
        denial_reason_description=sf.claim.denial_reason_description if sf.claim is not None else None,
        references=references,
        # Read verbatim from the EvidencePackage this run actually produced
        # -- never assumed from RAG_EMBEDDING_PROVIDER's default. Note that
        # context.hybrid_retriever.build_evidence_package hardcodes its own
        # DEFAULT_POLICY_PROVIDER ("semantic"), independent of that env var.
        retrieval_config=package.provenance.model_dump(),
        context_truncated=bool(truncation_notes),
        truncation_notes=truncation_notes,
    )


def _dedupe_by_ref_id(references: list[ContextReference]) -> list[ContextReference]:
    """Keep the first occurrence of each ref_id. Never collapses distinct
    records (e.g. two different authorization_ids are two different
    ref_ids) -- only guards against the same underlying fact appearing
    twice (e.g. an identical graph edge reached via two traversal paths).
    """
    seen: dict[str, ContextReference] = {}
    for ref in references:
        seen.setdefault(ref.ref_id, ref)
    return list(seen.values())
