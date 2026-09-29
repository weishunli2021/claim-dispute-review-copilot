"""Tests for the hybrid GraphRAG evidence models: typing and no-final-answer contract."""

from __future__ import annotations

from context.models import (
    EvidencePackage,
    EvidenceProvenance,
    MissingEvidence,
    PolicyEvidence,
    RelationshipEvidence,
    StructuredEvidence,
)
from graph.models import GraphRelationship
from rag.models import RetrievedChunk
from tools.case_context import CaseContext


def test_structured_evidence_is_a_case_context():
    assert issubclass(StructuredEvidence, CaseContext)


def test_policy_evidence_is_a_retrieved_chunk():
    assert issubclass(PolicyEvidence, RetrievedChunk)


def test_relationship_evidence_is_a_graph_relationship():
    assert issubclass(RelationshipEvidence, GraphRelationship)


def test_evidence_package_has_no_final_answer_field():
    field_names = set(EvidencePackage.model_fields)
    for forbidden in ("answer", "final_answer", "response", "conclusion", "explanation"):
        assert forbidden not in field_names
    assert field_names == {
        "claim_id",
        "original_query",
        "enriched_query",
        "structured_facts",
        "policy_chunks",
        "graph_relationships",
        "missing_evidence",
        "provenance",
    }


def test_evidence_package_constructs_with_minimal_fields():
    package = EvidencePackage(
        claim_id="CLM-1001",
        original_query="q",
        enriched_query="q enriched",
        structured_facts=StructuredEvidence(),
        provenance=EvidenceProvenance(
            policy_provider="semantic",
            policy_chunking_config="LARGE",
            policy_top_k=3,
            graph_max_hops=2,
        ),
    )
    assert package.policy_chunks == []
    assert package.graph_relationships == []
    assert package.missing_evidence == []


def test_missing_evidence_is_a_plain_factual_statement():
    item = MissingEvidence(
        category="prior_authorization",
        description="No matching prior authorization record was retrieved for this member and service.",
    )
    assert item.category == "prior_authorization"
    assert "no matching" in item.description.lower()
