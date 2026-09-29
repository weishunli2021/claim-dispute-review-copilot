"""Deterministic query enrichment using known structured case facts.

Never uses an LLM to rewrite the query -- this is plain string
composition from fields that are already present on StructuredEvidence. It
only ever appends facts that were actually retrieved; it never infers,
guesses, or fills in a fact that is missing (e.g. a claim with no
denial_reason_code gets no "Denial reason:" line -- it is never invented).
The original query is always preserved separately by the caller
(context/hybrid_retriever.py keeps both original_query and enriched_query
on the EvidencePackage); this module never mutates or discards it.
"""

from __future__ import annotations

from context.models import StructuredEvidence


def enrich_query(original_query: str, structured_facts: StructuredEvidence) -> str:
    """Return original_query with known case facts appended, or original_query
    unchanged if no claim (and therefore no facts) is available.

    Only the claim's service_code and denial_reason_code are appended, in
    that fixed order -- exactly the two facts most directly useful for
    matching policy-document vocabulary (service codes and denial reason
    codes both appear verbatim in the policy corpus). Other structured
    facts (plan, provider, authorization) are available in the evidence
    package's structured_facts field but are deliberately not folded into
    the retrieval query, to keep enrichment minimal and its effect legible.
    """
    claim = structured_facts.claim
    if claim is None:
        return original_query

    facts: list[str] = []
    if claim.service_code:
        facts.append(f"Service: {claim.service_code}.")
    if claim.denial_reason_code:
        facts.append(f"Denial reason: {claim.denial_reason_code}.")

    if not facts:
        return original_query

    return f"{original_query}\n" + " ".join(facts)
