"""Typed structures for the hybrid GraphRAG / evidence-assembly layer.

An EvidencePackage bundles exactly three evidence sources this codebase
already has, for one claim investigation:

- StructuredEvidence: deterministic case facts (tools/, via
  tools.case_context.get_case_context) -- the authoritative, case-specific
  structured facts.
- PolicyEvidence: unstructured policy chunks (rag/, via
  rag.retriever.search_policy) -- what the policy documents say, in
  general.
- RelationshipEvidence: structured relationship facts (graph/, via
  graph.retriever.get_claim_neighborhood, filtered by
  context/graph_filter.py) -- how this case's specific records connect.

There is deliberately NO final-answer field anywhere in this module. This
is evidence for a future reasoning layer to consume, not a conclusion.

Each evidence type below subclasses its source module's own return type
(CaseContext, RetrievedChunk, GraphRelationship) rather than redefining
their fields -- this guarantees the evidence package can never drift from
what tools/rag/graph actually produce, and it preserves every source id
(claim_id, chunk_id, section_id, source_id/target_id, ...) automatically.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from graph.models import GraphRelationship
from rag.models import RetrievedChunk
from tools.case_context import CaseContext


class StructuredEvidence(CaseContext):
    """Deterministic case facts, exactly as tools.case_context.get_case_context
    returns them (claim, member, plan, benefit, prior_authorizations,
    servicing_provider, ordering_provider). No new fields -- see
    tools/case_context.py for what "no reasoning, facts only" means here.
    """


class PolicyEvidence(RetrievedChunk):
    """One policy chunk retrieved for this case, exactly as
    rag.retriever.search_policy returns it (chunk text, document/section id
    and title, chunk id, similarity score). No new fields.
    """


class RelationshipEvidence(GraphRelationship):
    """One graph relationship retained after claim-relevance filtering (see
    context/graph_filter.py). No new fields -- source_id, relation, and
    target_id are preserved exactly as graph/retriever.py produced them.
    """


class MissingEvidence(BaseModel):
    """One explicit statement that expected evidence was sought and not
    found. Describes an absence of information only -- never a business
    conclusion drawn from that absence. "No matching authorization record
    was retrieved" is correct; "the authorization was denied" is not, and
    nothing in context/hybrid_retriever.py ever writes the latter.
    """

    category: str
    description: str


class EvidenceProvenance(BaseModel):
    """How this evidence package was produced -- retrieval configuration,
    not business conclusions. Lets a golden-set comparison or a future
    reasoning layer know exactly which provider/config/depth produced the
    evidence it is looking at.
    """

    structured_source: str = "tools.case_context.get_case_context"
    policy_source: str = "rag.retriever.search_policy"
    policy_provider: str
    policy_chunking_config: str
    policy_top_k: int
    graph_source: str = "graph.retriever.get_claim_neighborhood + context.graph_filter"
    graph_max_hops: int


class EvidencePackage(BaseModel):
    """The complete evidence bundle assembled for one claim investigation.

    This is evidence only -- there is deliberately no final-answer field.
    A future reasoning layer (LLM/agent, not yet implemented) is expected
    to consume this package and produce an answer; nothing in this package
    or the code that builds it (context/hybrid_retriever.py) does that.
    """

    claim_id: str
    original_query: str
    enriched_query: str
    structured_facts: StructuredEvidence
    policy_chunks: list[PolicyEvidence] = Field(default_factory=list)
    graph_relationships: list[RelationshipEvidence] = Field(default_factory=list)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    provenance: EvidenceProvenance
