"""Independent quality evaluation for the hybrid GraphRAG evidence-assembly layer.

Unlike evals/hybrid_regression_eval.py, every expectation in
evals/hybrid_retrieval_golden_set.json was derived INDEPENDENTLY: from
synthetic source-of-truth data (data/*.json), documented policy meaning
(documents/*.md), and the intended graph/domain schema (graph/builder.py)
-- never by running the pipeline and copying its output. Each golden-set
entry's "rationale" field names exactly which source justifies each
expectation.

That independence is what makes a score below 100% here meaningful. A
genuine mismatch is either:

  (a) an actual defect in context/ -- fix the code, or
  (b) a legitimate, documented limitation of evidence-only retrieval
      (e.g. a policy section a human would consider relevant but that
      the embedding model does not surface) -- report it plainly.

Never (c): edit the golden set to match whatever the pipeline currently
returns. That would silently turn this back into the regression suite it
is explicitly NOT meant to be.

Five things are measured per case:

- Structured Evidence Accuracy/Completeness: fraction of a case's
  expected_structured_facts keys whose actual value (read off
  EvidencePackage.structured_facts) matches the independently-derived one.
- Policy Section Recall: fraction of expected_policy_sections actually
  present among the retrieved policy_chunks.
- Graph Relationship Recall: fraction of expected_graph_relationships
  actually present among the (already claim-filtered) graph_relationships,
  combined with a check that no forbidden_relationship_pattern (a
  {source, relation} pair that must never appear regardless of target --
  e.g. an out-of-network provider must never get a PARTICIPATES_IN edge)
  is present.
- Missing Evidence Accuracy: whether the EvidencePackage's
  missing_evidence categories exactly match expected_missing_evidence_categories
  -- not "close enough," since flagging the wrong thing as missing (or
  failing to flag a genuine gap) is itself a defect worth catching.
- Cross-Case Contamination Rate = (cases containing any forbidden entity)
  / (total evaluated cases). Target is 0%; this script reports whatever
  the real number is rather than forcing it.

Per-case results are always printed individually -- the aggregate summary
at the end never replaces or hides them.

Run with:

    python -m evals.hybrid_retrieval_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from context.hybrid_retriever import build_evidence_package
from context.models import EvidencePackage

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "hybrid_retrieval_golden_set.json"


@dataclass
class CaseResult:
    case_id: str
    claim_id: str

    structured_completeness: float
    structured_mismatches: list[str]

    policy_section_recall: float
    missing_policy_sections: list[str]

    graph_relationship_recall: float
    missing_graph_relationships: list[tuple[str, str, str]]
    forbidden_pattern_violations: list[tuple[str, str]]

    missing_evidence_accurate: bool
    missing_evidence_expected: list[str]
    missing_evidence_actual: list[str]

    contaminating_nodes: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            not self.structured_mismatches
            and not self.missing_policy_sections
            and not self.missing_graph_relationships
            and not self.forbidden_pattern_violations
            and self.missing_evidence_accurate
            and not self.contaminating_nodes
        )


def load_golden_set(path: Path | None = None) -> list[dict]:
    """Load the independently-authored golden-set cases. See each entry's
    "rationale" field for the source-of-truth justification behind its
    expectations -- none of them were derived from pipeline output."""
    return json.loads((path or GOLDEN_SET_PATH).read_text(encoding="utf-8"))


def _actual_structured_facts(package: EvidencePackage) -> dict:
    sf = package.structured_facts
    return {
        "claim_status": sf.claim.status if sf.claim else None,
        "denial_reason_code": sf.claim.denial_reason_code if sf.claim else None,
        "benefit_present": sf.benefit is not None,
        "benefit_covered": sf.benefit.covered if sf.benefit else None,
        "benefit_requires_prior_auth": sf.benefit.requires_prior_auth if sf.benefit else None,
        "prior_authorizations_count": len(sf.prior_authorizations),
        "servicing_provider_present": sf.servicing_provider is not None,
        "ordering_provider_present": sf.ordering_provider is not None,
    }


def evaluate_case(item: dict) -> CaseResult:
    package = build_evidence_package(item["claim_id"], item["query"])

    # --- Structured Evidence Accuracy/Completeness ---
    expected_facts = item.get("expected_structured_facts", {})
    actual_facts = _actual_structured_facts(package)
    mismatches = [
        f"{key}: expected {expected!r}, got {actual_facts.get(key)!r}"
        for key, expected in expected_facts.items()
        if actual_facts.get(key) != expected
    ]
    structured_completeness = (
        (len(expected_facts) - len(mismatches)) / len(expected_facts) if expected_facts else 1.0
    )

    # --- Policy Section Recall ---
    found_sections = {chunk.section_id for chunk in package.policy_chunks}
    expected_sections = item.get("expected_policy_sections", [])
    missing_sections = [s for s in expected_sections if s not in found_sections]
    policy_recall = (
        (len(expected_sections) - len(missing_sections)) / len(expected_sections)
        if expected_sections
        else 1.0
    )

    # --- Graph Relationship Recall (+ forbidden relationship patterns) ---
    actual_relationships = [
        (rel.source_id, rel.relation, rel.target_id) for rel in package.graph_relationships
    ]
    found_relationship_set = set(actual_relationships)
    expected_relationships = [
        (r["source"], r["relation"], r["target"]) for r in item.get("expected_graph_relationships", [])
    ]
    missing_relationships = [r for r in expected_relationships if r not in found_relationship_set]
    relationship_recall = (
        (len(expected_relationships) - len(missing_relationships)) / len(expected_relationships)
        if expected_relationships
        else 1.0
    )

    forbidden_patterns = [
        (p["source"], p["relation"]) for p in item.get("forbidden_relationship_patterns", [])
    ]
    forbidden_pattern_violations = [
        (source, relation)
        for source, relation in forbidden_patterns
        if any(s == source and r == relation for s, r, _ in actual_relationships)
    ]

    # --- Missing Evidence Accuracy ---
    expected_missing = sorted(item.get("expected_missing_evidence_categories", []))
    actual_missing = sorted({m.category for m in package.missing_evidence})
    missing_evidence_accurate = expected_missing == actual_missing

    # --- Cross-case contamination (per case; aggregated into a rate below) ---
    forbidden_nodes = set(item.get("forbidden_nodes", []))
    present_nodes = set()
    for rel in package.graph_relationships:
        present_nodes.add(rel.source_id)
        present_nodes.add(rel.target_id)
    contaminating = sorted(forbidden_nodes & present_nodes)

    return CaseResult(
        case_id=item["case_id"],
        claim_id=item["claim_id"],
        structured_completeness=structured_completeness,
        structured_mismatches=mismatches,
        policy_section_recall=policy_recall,
        missing_policy_sections=missing_sections,
        graph_relationship_recall=relationship_recall,
        missing_graph_relationships=missing_relationships,
        forbidden_pattern_violations=forbidden_pattern_violations,
        missing_evidence_accurate=missing_evidence_accurate,
        missing_evidence_expected=expected_missing,
        missing_evidence_actual=actual_missing,
        contaminating_nodes=contaminating,
    )


def evaluate_all(golden_set: list[dict] | None = None) -> list[CaseResult]:
    golden_set = golden_set if golden_set is not None else load_golden_set()
    return [evaluate_case(item) for item in golden_set]


def summarize(results: list[CaseResult]) -> dict[str, float]:
    """Aggregate summary. Reported ALONGSIDE per-case results, never instead of them."""
    n = len(results)
    if n == 0:
        return {
            "structured_completeness": 0.0,
            "policy_section_recall": 0.0,
            "graph_relationship_recall": 0.0,
            "missing_evidence_accuracy": 0.0,
            "cross_case_contamination_rate": 0.0,
        }
    contaminated_count = sum(1 for r in results if r.contaminating_nodes)
    return {
        "structured_completeness": sum(r.structured_completeness for r in results) / n,
        "policy_section_recall": sum(r.policy_section_recall for r in results) / n,
        "graph_relationship_recall": sum(r.graph_relationship_recall for r in results) / n,
        "missing_evidence_accuracy": sum(1 for r in results if r.missing_evidence_accurate) / n,
        "cross_case_contamination_rate": contaminated_count / n,
    }


def print_report(results: list[CaseResult]) -> None:
    print(f"=== Hybrid retrieval INDEPENDENT quality evaluation ({len(results)} cases) ===")
    print("(Expectations derived from source data/policy/schema, not from pipeline output.)\n")

    print("Per-case results:")
    for r in results:
        status = "OK" if r.is_clean else "FAIL"
        print(
            f"  [{status}] {r.case_id} (claim={r.claim_id}): "
            f"structured={r.structured_completeness:.0%} "
            f"policy={r.policy_section_recall:.0%} "
            f"graph={r.graph_relationship_recall:.0%} "
            f"missing_evidence_accurate={r.missing_evidence_accurate}"
        )
        if r.structured_mismatches:
            print(f"        structured mismatches: {r.structured_mismatches}")
        if r.missing_policy_sections:
            print(f"        missing policy sections: {r.missing_policy_sections}")
        if r.missing_graph_relationships:
            print(f"        missing graph relationships: {r.missing_graph_relationships}")
        if r.forbidden_pattern_violations:
            print(f"        FORBIDDEN RELATIONSHIP PATTERN PRESENT: {r.forbidden_pattern_violations}")
        if not r.missing_evidence_accurate:
            print(
                f"        missing_evidence mismatch: expected {r.missing_evidence_expected}, "
                f"got {r.missing_evidence_actual}"
            )
        if r.contaminating_nodes:
            print(f"        CROSS-CASE CONTAMINATION: {r.contaminating_nodes}")
    print()

    summary = summarize(results)
    print("Aggregate summary (see per-case results above -- this does not replace them):")
    print(f"  Structured Evidence Accuracy/Completeness: {summary['structured_completeness']:.2%}")
    print(f"  Policy Section Recall:                      {summary['policy_section_recall']:.2%}")
    print(f"  Graph Relationship Recall:                   {summary['graph_relationship_recall']:.2%}")
    print(f"  Missing Evidence Accuracy:                    {summary['missing_evidence_accuracy']:.2%}")
    print(f"  Cross-Case Contamination Rate:               {summary['cross_case_contamination_rate']:.2%}")


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Independent quality evaluation for the hybrid GraphRAG evidence-assembly layer. "
            "Expectations were derived from source data/policy/schema, not from pipeline output."
        )
    )
    parser.parse_args(argv)

    results = evaluate_all()
    print_report(results)


if __name__ == "__main__":
    _main(sys.argv[1:])
