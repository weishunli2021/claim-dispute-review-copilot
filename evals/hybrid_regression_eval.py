"""REGRESSION suite for the hybrid GraphRAG evidence-assembly layer --
NOT an independent quality/accuracy evaluation.

Purpose: detect unexpected changes in known pipeline behavior. Every
expectation in evals/hybrid_regression_set.json was captured from this
pipeline's own observed output at the time it was written (structured
facts, policy sections, graph relationships, missing-evidence categories
for eight claim/query combinations across all five synthetic cases). That
makes this suite good at one specific job -- if a future code change
silently alters what the hybrid retriever returns for these exact
claim/query pairs, this suite will fail and say what changed -- and
unsuited to a different job: it cannot tell you whether the pipeline's
current behavior is actually CORRECT, because "correct" here is defined
as "whatever the pipeline already does." A 100% pass rate on this suite
means "nothing changed," not "the evidence is high quality."

For an evaluation that defines correctness independently -- from the
synthetic source data, the policy documents' actual meaning, and the
intended graph schema, without ever inspecting pipeline output -- see
evals/hybrid_retrieval_golden_set.json and evals/hybrid_retrieval_eval.py.
That is the one to trust for quality claims; this one is for catching
drift.

The four metrics below are unchanged from the original version of this
script (no implementation defect was found that would justify altering
them):

- Policy Section Recall: fraction of a case's expected_policy_sections
  actually present among the EvidencePackage's policy_chunks.
- Graph Relationship Recall: fraction of a case's
  expected_graph_relationships actually present among the (already
  claim-filtered) graph_relationships.
- Structured Evidence Completeness: fraction of a case's
  expected_structured_facts keys whose actual value (read off
  EvidencePackage.structured_facts) matches the expected one.
- Cross-case contamination: whether any of a case's forbidden_nodes
  (entities belonging to a DIFFERENT case, reachable only through a
  shared hub node in the raw graph neighborhood) leaked into this case's
  filtered graph_relationships. This should always be zero -- any
  non-empty result here is a real bug in context/graph_filter.py, not an
  expected tradeoff like the recall metrics above.

Run with:

    python -m evals.hybrid_regression_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from context.hybrid_retriever import build_evidence_package
from context.models import EvidencePackage

REGRESSION_SET_PATH = Path(__file__).resolve().parent / "hybrid_regression_set.json"


@dataclass
class CaseResult:
    case_id: str
    claim_id: str
    policy_section_recall: float
    missing_policy_sections: list[str]
    graph_relationship_recall: float
    missing_graph_relationships: list[tuple[str, str, str]]
    structured_completeness: float
    structured_mismatches: list[str]
    contaminating_nodes: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.missing_policy_sections and not self.missing_graph_relationships and not self.structured_mismatches and not self.contaminating_nodes


def load_regression_set(path: Path | None = None) -> list[dict]:
    """Load the regression-set cases: case_id, claim_id, query,
    expected_structured_facts, expected_policy_sections,
    expected_graph_relationships, expected_missing_evidence_categories,
    forbidden_nodes -- all captured from this pipeline's own past output,
    not derived independently. See the module docstring."""
    return json.loads((path or REGRESSION_SET_PATH).read_text(encoding="utf-8"))


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

    # --- Policy Section Recall ---
    found_sections = {chunk.section_id for chunk in package.policy_chunks}
    expected_sections = item.get("expected_policy_sections", [])
    missing_sections = [s for s in expected_sections if s not in found_sections]
    policy_recall = (
        (len(expected_sections) - len(missing_sections)) / len(expected_sections)
        if expected_sections
        else 1.0
    )

    # --- Graph Relationship Recall ---
    found_relationships = {
        (rel.source_id, rel.relation, rel.target_id) for rel in package.graph_relationships
    }
    expected_relationships = [
        (r["source"], r["relation"], r["target"]) for r in item.get("expected_graph_relationships", [])
    ]
    missing_relationships = [r for r in expected_relationships if r not in found_relationships]
    relationship_recall = (
        (len(expected_relationships) - len(missing_relationships)) / len(expected_relationships)
        if expected_relationships
        else 1.0
    )

    # --- Structured Evidence Completeness ---
    expected_facts = item.get("expected_structured_facts", {})
    actual_facts = _actual_structured_facts(package)
    mismatches = [
        f"{key}: expected {expected!r}, got {actual_facts.get(key)!r}"
        for key, expected in expected_facts.items()
        if actual_facts.get(key) != expected
    ]
    completeness = (
        (len(expected_facts) - len(mismatches)) / len(expected_facts) if expected_facts else 1.0
    )

    # --- Cross-case contamination ---
    forbidden_nodes = set(item.get("forbidden_nodes", []))
    present_nodes = set()
    for rel in package.graph_relationships:
        present_nodes.add(rel.source_id)
        present_nodes.add(rel.target_id)
    contaminating = sorted(forbidden_nodes & present_nodes)

    return CaseResult(
        case_id=item["case_id"],
        claim_id=item["claim_id"],
        policy_section_recall=policy_recall,
        missing_policy_sections=missing_sections,
        graph_relationship_recall=relationship_recall,
        missing_graph_relationships=missing_relationships,
        structured_completeness=completeness,
        structured_mismatches=mismatches,
        contaminating_nodes=contaminating,
    )


def evaluate_all(regression_set: list[dict] | None = None) -> list[CaseResult]:
    regression_set = regression_set if regression_set is not None else load_regression_set()
    return [evaluate_case(item) for item in regression_set]


def summarize(results: list[CaseResult]) -> dict[str, float]:
    n = len(results)
    if n == 0:
        return {"policy_section_recall": 0.0, "graph_relationship_recall": 0.0, "structured_completeness": 0.0}
    return {
        "policy_section_recall": sum(r.policy_section_recall for r in results) / n,
        "graph_relationship_recall": sum(r.graph_relationship_recall for r in results) / n,
        "structured_completeness": sum(r.structured_completeness for r in results) / n,
    }


def print_report(results: list[CaseResult]) -> None:
    summary = summarize(results)
    print(f"=== Hybrid retrieval REGRESSION suite ({len(results)} cases) ===")
    print("(Detects drift from past pipeline output -- not an independent quality claim;")
    print(" see evals/hybrid_retrieval_eval.py for that.)")
    print(f"  Policy Section Recall:          {summary['policy_section_recall']:.2%}")
    print(f"  Graph Relationship Recall:      {summary['graph_relationship_recall']:.2%}")
    print(f"  Structured Evidence Completeness: {summary['structured_completeness']:.2%}")

    contaminated = [r for r in results if r.contaminating_nodes]
    print(f"  Cross-case contamination: {len(contaminated)} case(s)")
    for r in contaminated:
        print(f"    [{r.case_id}] leaked forbidden nodes: {r.contaminating_nodes}")
    print()

    failures = [r for r in results if not r.is_clean]
    if failures:
        print(f"Per-case failures ({len(failures)}):")
        for r in failures:
            print(f"  [{r.case_id}] claim={r.claim_id}")
            if r.missing_policy_sections:
                print(f"      missing policy sections: {r.missing_policy_sections}")
            if r.missing_graph_relationships:
                print(f"      missing graph relationships: {r.missing_graph_relationships}")
            if r.structured_mismatches:
                print(f"      structured fact mismatches: {r.structured_mismatches}")
            if r.contaminating_nodes:
                print(f"      CONTAMINATION: {r.contaminating_nodes}")
        print()
    else:
        print("No per-case failures.\n")


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regression suite for the hybrid GraphRAG evidence-assembly layer: detects "
            "unexpected changes from past observed pipeline output. Not an independent "
            "quality evaluation -- see evals/hybrid_retrieval_eval.py for that."
        )
    )
    parser.parse_args(argv)

    results = evaluate_all()
    print_report(results)


if __name__ == "__main__":
    _main(sys.argv[1:])
