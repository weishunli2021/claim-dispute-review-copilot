"""Hybrid retrieval: assembles an EvidencePackage for one claim investigation.

Combines exactly three evidence sources this codebase already has:

- Deterministic structured facts (tools/, via tools.case_context)
- Unstructured policy evidence (rag/, via rag.retriever.search_policy)
- Structured relationship evidence (graph/, via graph.retriever, filtered
  by context/graph_filter.py for claim relevance)

This is a lightweight, in-process hybrid "GraphRAG" -- the name describes
combining vector retrieval with graph retrieval, not a claim to implement
Microsoft GraphRAG, LlamaIndex's GraphRAG, or any other specific external
framework (no community detection, no graph summarization, no LLM anywhere
in this module). The output is an EVIDENCE PACKAGE, never a final
natural-language answer -- there is no LLM call here, and nothing in this
module explains, summarizes, or concludes anything about why a claim was
approved or denied.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from context.graph_filter import filter_claim_graph_context
from context.models import (
    EvidencePackage,
    EvidenceProvenance,
    MissingEvidence,
    PolicyEvidence,
    RelationshipEvidence,
    StructuredEvidence,
)
from context.query_enrichment import enrich_query
from graph.retriever import get_claim_neighborhood
from rag.chunking import LARGE
from rag.retriever import search_policy
from tools.case_context import get_case_context

DEFAULT_TOP_K_POLICY = 3
DEFAULT_GRAPH_MAX_HOPS = 2
# Matches rag.retriever.search_policy's own recommended prototype default
# (see evals/retrieval_eval.py's four-configuration comparison).
DEFAULT_POLICY_PROVIDER = "semantic"
DEFAULT_POLICY_CONFIG = LARGE


def build_evidence_package(
    claim_id: str,
    query: str,
    top_k_policy: int = DEFAULT_TOP_K_POLICY,
    graph_max_hops: int = DEFAULT_GRAPH_MAX_HOPS,
) -> EvidencePackage:
    """Assemble the full evidence package for one claim investigation.

    1. Retrieve CaseContext via deterministic tools (tools.case_context).
    2. Enrich the retrieval query from those facts (context.query_enrichment) --
       deterministic, no LLM.
    3. Retrieve policy chunks (rag.retriever.search_policy) using the
       enriched query.
    4. Retrieve the claim's raw graph neighborhood (graph.retriever).
    5. Filter it for claim relevance (context.graph_filter) -- prevents
       cross-case contamination through shared hub nodes.
    6. Explicitly identify missing structured evidence.
    7. Assemble everything into an EvidencePackage. No answer generation.
    """
    case_context = get_case_context(claim_id)
    structured_facts = StructuredEvidence(**case_context.model_dump())

    enriched_query = enrich_query(query, structured_facts)

    policy_results = search_policy(
        enriched_query,
        top_k=top_k_policy,
        config=DEFAULT_POLICY_CONFIG,
        provider_name=DEFAULT_POLICY_PROVIDER,
    )
    policy_chunks = [PolicyEvidence(**result.model_dump()) for result in policy_results]

    # A claim that doesn't exist has no node in the graph either -- skip
    # the lookup rather than let it raise. (Discovered via
    # skills/investigate_claim.py's own golden-set test for an unknown
    # claim_id, which calls this function without a prior existence
    # check -- unlike agents/nodes.py's load_case, which always checks
    # first and never reached this path.)
    if structured_facts.claim is not None:
        raw_graph_context = get_claim_neighborhood(claim_id, max_hops=graph_max_hops)
        filtered_graph_context = filter_claim_graph_context(raw_graph_context)
        graph_relationships = [
            RelationshipEvidence(**rel.model_dump()) for rel in filtered_graph_context.relationships
        ]
    else:
        graph_relationships = []

    missing_evidence = _identify_missing_evidence(structured_facts)

    provenance = EvidenceProvenance(
        policy_provider=DEFAULT_POLICY_PROVIDER,
        policy_chunking_config=DEFAULT_POLICY_CONFIG.name,
        policy_top_k=top_k_policy,
        graph_max_hops=graph_max_hops,
    )

    return EvidencePackage(
        claim_id=claim_id,
        original_query=query,
        enriched_query=enriched_query,
        structured_facts=structured_facts,
        policy_chunks=policy_chunks,
        graph_relationships=graph_relationships,
        missing_evidence=missing_evidence,
        provenance=provenance,
    )


def _identify_missing_evidence(structured_facts: StructuredEvidence) -> list[MissingEvidence]:
    """Explicitly flag structured facts that were sought but not found.

    Describes absence only -- never a business conclusion drawn from it.
    "No matching prior authorization record was retrieved" is correct;
    nothing here ever writes "authorization was denied" or "service is not
    covered" -- those would be conclusions a future reasoning layer might
    draw, not facts this layer observed.
    """
    missing: list[MissingEvidence] = []

    if structured_facts.claim is None:
        missing.append(
            MissingEvidence(
                category="claim",
                description="No matching claim record was retrieved for this claim_id.",
            )
        )
        return missing  # nothing else can be meaningfully assessed without a claim

    if structured_facts.member is None:
        missing.append(
            MissingEvidence(
                category="member",
                description="No matching member record was retrieved for this claim.",
            )
        )

    if structured_facts.plan is None:
        missing.append(
            MissingEvidence(
                category="plan",
                description="No matching plan record was retrieved for this claim.",
            )
        )

    if not structured_facts.prior_authorizations:
        missing.append(
            MissingEvidence(
                category="prior_authorization",
                description=(
                    "No matching prior authorization record was retrieved for this "
                    "member and service."
                ),
            )
        )

    if structured_facts.benefit is None:
        missing.append(
            MissingEvidence(
                category="benefit",
                description="Applicable benefit information is unavailable for this plan and service.",
            )
        )

    if structured_facts.servicing_provider is None:
        missing.append(
            MissingEvidence(
                category="servicing_provider",
                description="The servicing provider referenced on this claim could not be resolved.",
            )
        )

    if structured_facts.claim.ordering_provider_id and structured_facts.ordering_provider is None:
        missing.append(
            MissingEvidence(
                category="ordering_provider",
                description="The ordering provider referenced on this claim could not be resolved.",
            )
        )

    return missing


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug utility: assemble and print the evidence package for one claim "
            "investigation. Evidence only -- no answer is generated."
        ),
    )
    parser.add_argument("claim_id", help="e.g. CLM-1001")
    parser.add_argument("query", help="e.g. 'My claim was denied. What's wrong with it?'")
    parser.add_argument("--top-k-policy", type=int, default=DEFAULT_TOP_K_POLICY)
    parser.add_argument("--graph-max-hops", type=int, default=DEFAULT_GRAPH_MAX_HOPS)
    args = parser.parse_args(argv)

    package = build_evidence_package(
        args.claim_id,
        args.query,
        top_k_policy=args.top_k_policy,
        graph_max_hops=args.graph_max_hops,
    )

    print("ORIGINAL QUERY")
    print(f"  {package.original_query}")
    print()

    print("ENRICHED QUERY")
    print(f"  {package.enriched_query}")
    print()

    print("STRUCTURED EVIDENCE")
    sf = package.structured_facts
    if sf.claim:
        print(
            f"  claim: {sf.claim.claim_id} status={sf.claim.status} "
            f"denial_reason_code={sf.claim.denial_reason_code}"
        )
    if sf.member:
        print(f"  member: {sf.member.member_id} ({sf.member.first_name} {sf.member.last_name})")
    if sf.plan:
        print(f"  plan: {sf.plan.plan_id} ({sf.plan.plan_name})")
    if sf.benefit:
        print(
            f"  benefit: {sf.benefit.benefit_id} covered={sf.benefit.covered} "
            f"requires_prior_auth={sf.benefit.requires_prior_auth}"
        )
    for auth in sf.prior_authorizations:
        print(
            f"  prior_authorization: {auth.authorization_id} status={auth.status} "
            f"effective={auth.effective_date}..{auth.expiration_date}"
        )
    if sf.servicing_provider:
        print(
            f"  servicing_provider: {sf.servicing_provider.provider_id} "
            f"network_status={sf.servicing_provider.network_status}"
        )
    if sf.ordering_provider:
        print(
            f"  ordering_provider: {sf.ordering_provider.provider_id} "
            f"network_status={sf.ordering_provider.network_status}"
        )
    print()

    print("POLICY EVIDENCE")
    for chunk in package.policy_chunks:
        score = f" (score={chunk.score:.4f})" if chunk.score is not None else ""
        print(f"  [{chunk.section_id}] {chunk.section_title}{score}")
        print(f"      {chunk.text}")
    print()

    print("GRAPH RELATIONSHIPS")
    for rel in package.graph_relationships:
        print(f"  {rel.source_id} --{rel.relation}--> {rel.target_id}")
    print()

    print("MISSING EVIDENCE")
    for item in package.missing_evidence:
        print(f"  [{item.category}] {item.description}")
    print()

    print("PROVENANCE")
    for key, value in package.provenance.model_dump().items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    _main(sys.argv[1:])
