"""Tests for the investigate_claim skill, including the evidence-sufficiency
rule that now lives here (moved from agents/routing.py as of the
Skills-layer integration -- see skills/investigate_claim.py's docstring)."""

from __future__ import annotations

from context.hybrid_retriever import build_evidence_package
from context.models import EvidencePackage
from skills.base import SkillStatus
from skills.investigate_claim import investigate_claim, is_evidence_sufficient


def test_case1_completed():
    result = investigate_claim("CLM-1001", "My claim was denied. What's wrong with it?")
    assert result.status == SkillStatus.COMPLETED
    assert result.skill_name == "investigate_claim"
    assert result.skill_version == "1.0.0"
    assert "prior_authorization" in result.missing_information
    assert result.next_capability is None

    package = EvidencePackage.model_validate(result.evidence["evidence_package"])
    assert package.claim_id == "CLM-1001"


def test_case5_insufficient_evidence_with_next_capability():
    result = investigate_claim("CLM-1005", "Why was my specialist visit claim denied?")
    assert result.status == SkillStatus.INSUFFICIENT_EVIDENCE
    assert result.next_capability == "escalate_case"
    assert set(result.missing_information) >= {"benefit", "servicing_provider"}


def test_unknown_claim_not_found():
    result = investigate_claim("CLM-9999", "anything")
    assert result.status == SkillStatus.NOT_FOUND
    assert "CLM-9999" in result.error


def test_invalid_claim_id_error():
    result = investigate_claim("bad claim id", "anything")
    assert result.status == SkillStatus.ERROR
    assert result.error


def test_investigate_claim_output_is_deterministic():
    first = investigate_claim("CLM-1001", "My claim was denied. What's wrong with it?")
    second = investigate_claim("CLM-1001", "My claim was denied. What's wrong with it?")
    assert first.status == second.status
    assert first.missing_information == second.missing_information

    first_chunk_ids = [c["chunk_id"] for c in first.evidence["evidence_package"]["policy_chunks"]]
    second_chunk_ids = [c["chunk_id"] for c in second.evidence["evidence_package"]["policy_chunks"]]
    assert first_chunk_ids == second_chunk_ids


def test_is_evidence_sufficient_case1_true():
    package = build_evidence_package("CLM-1001", "My claim was denied. What's wrong with it?")
    assert is_evidence_sufficient(package) is True


def test_is_evidence_sufficient_case3_true_despite_not_covered():
    # Present-but-not-covered benefit must count as resolved evidence.
    package = build_evidence_package("CLM-1003", "Why was my lab claim denied?")
    assert package.structured_facts.benefit is not None
    assert package.structured_facts.benefit.covered is False
    assert is_evidence_sufficient(package) is True


def test_is_evidence_sufficient_case4_true_despite_out_of_network():
    # A resolvable out-of-network provider must count as resolved evidence.
    package = build_evidence_package("CLM-1004", "Why was my physical therapy claim denied?")
    assert package.structured_facts.servicing_provider is not None
    assert package.structured_facts.servicing_provider.network_status == "out-of-network"
    assert is_evidence_sufficient(package) is True


def test_is_evidence_sufficient_case5_false():
    package = build_evidence_package("CLM-1005", "Why was my specialist visit claim denied?")
    assert package.structured_facts.benefit is None
    assert package.structured_facts.servicing_provider is None
    assert is_evidence_sufficient(package) is False
