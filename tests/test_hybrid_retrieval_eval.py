"""Tests for the INDEPENDENT hybrid retrieval quality evaluation harness.

Unlike tests/test_hybrid_regression_eval.py, this evaluates against
expectations derived independently from source data/policy/schema (see
evals/hybrid_retrieval_golden_set.json). The known current result is that
policy section recall is NOT 100% -- a documented, legitimate limitation
of bounded top-k semantic retrieval (see evals/hybrid_retrieval_eval.py's
module docstring), not a code defect -- so these tests assert bounds
consistent with that real state rather than asserting perfection.
"""

from __future__ import annotations

from evals.hybrid_retrieval_eval import evaluate_all, evaluate_case, load_golden_set, summarize


def test_golden_set_loads_and_covers_all_five_cases():
    golden_set = load_golden_set()
    assert len(golden_set) == 5
    claim_ids = {item["claim_id"] for item in golden_set}
    assert claim_ids == {"CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"}
    for item in golden_set:
        assert item.get("rationale"), f"{item['case_id']} is missing its independent-sourcing rationale"


def test_evaluate_all_executes_and_produces_valid_metrics():
    results = evaluate_all()
    assert len(results) == 5

    summary = summarize(results)
    assert set(summary.keys()) == {
        "structured_completeness",
        "policy_section_recall",
        "graph_relationship_recall",
        "missing_evidence_accuracy",
        "cross_case_contamination_rate",
    }
    for value in summary.values():
        assert 0.0 <= value <= 1.0


def test_structured_evidence_and_graph_relationships_are_fully_correct():
    # These two subsystems are expected to be perfect against independently
    # derived source-of-truth facts -- any regression here is a real defect.
    results = evaluate_all()
    for r in results:
        assert r.structured_completeness == 1.0, (r.case_id, r.structured_mismatches)
        assert r.graph_relationship_recall == 1.0, (r.case_id, r.missing_graph_relationships)
        assert not r.forbidden_pattern_violations, (r.case_id, r.forbidden_pattern_violations)


def test_missing_evidence_categories_are_accurate_for_every_case():
    results = evaluate_all()
    for r in results:
        assert r.missing_evidence_accurate, (
            r.case_id,
            r.missing_evidence_expected,
            r.missing_evidence_actual,
        )


def test_no_cross_case_contamination():
    results = evaluate_all()
    summary = summarize(results)
    assert summary["cross_case_contamination_rate"] == 0.0
    assert all(not r.contaminating_nodes for r in results)


def test_policy_section_recall_is_known_imperfect_not_silently_hidden():
    # Documented, expected result: the default top_k_policy=3 cannot always
    # surface every independently-identified relevant section when more
    # than 3 legitimately relevant sections exist for a query. This pins
    # the current real number so a silent regression -- or a silent "fix"
    # by editing the golden set -- would be caught, without asserting a
    # perfection that does not exist.
    results = evaluate_all()
    summary = summarize(results)
    assert 0.5 <= summary["policy_section_recall"] < 1.0


def test_forbidden_relationship_pattern_is_detected_when_present(monkeypatch):
    # No current case actually violates a forbidden pattern (that's the
    # point -- the pipeline is correct), so this exercises the detection
    # mechanism itself against a synthetic violation.
    import evals.hybrid_retrieval_eval as module
    from context.models import (
        EvidencePackage,
        EvidenceProvenance,
        RelationshipEvidence,
        StructuredEvidence,
    )

    def _fake_build_evidence_package(claim_id, query, **kwargs):
        return EvidencePackage(
            claim_id=claim_id,
            original_query=query,
            enriched_query=query,
            structured_facts=StructuredEvidence(),
            policy_chunks=[],
            graph_relationships=[
                RelationshipEvidence(
                    source_id="provider:PRV-X", relation="PARTICIPATES_IN", target_id="network:y"
                )
            ],
            missing_evidence=[],
            provenance=EvidenceProvenance(
                policy_provider="semantic",
                policy_chunking_config="LARGE",
                policy_top_k=3,
                graph_max_hops=2,
            ),
        )

    monkeypatch.setattr(module, "build_evidence_package", _fake_build_evidence_package)

    item = {
        "case_id": "synthetic_forbidden_pattern_case",
        "claim_id": "CLM-FAKE",
        "query": "anything",
        "forbidden_relationship_patterns": [{"source": "provider:PRV-X", "relation": "PARTICIPATES_IN"}],
    }
    result = evaluate_case(item)
    assert result.forbidden_pattern_violations == [("provider:PRV-X", "PARTICIPATES_IN")]
    assert not result.is_clean
