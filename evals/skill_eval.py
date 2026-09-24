"""Offline evaluation of the Skills layer against evals/skill_golden_set.json.

Every expectation was authored independently, from the same source
synthetic data used throughout this project (data/*.json) and from each
skill's own documented contract (skills/*.py) -- never by running the
skills first and copying their output. See each golden-set entry's
"rationale" field for the source-of-truth justification behind it.

Five things are measured:

- Skill Completion Accuracy: fraction of cases where the skill's actual
  status matches expected_status.
- Required Evidence Recall: fraction of a case's
  expected_evidence_characteristics whose actual value (read off the
  SkillResult's evidence dict via a small per-skill accessor) matches.
- Missing-Evidence Accuracy: whether the skill's actual
  missing_information (as a set) exactly matches
  expected_missing_categories.
- Correct Next-Capability Rate: fraction of cases where actual
  next_capability matches expected_next_capability.
- Unexpected Dependency/Tool Usage Rate: fraction of cases where the
  invoked skill's declared SkillMetadata.allowed_tools does not match
  this golden set's independently-authored expected_dependencies for
  that case -- a governance check that a skill's declared dependencies
  haven't silently drifted from what it's actually supposed to depend on.

Run with:

    python -m evals.skill_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from skills.base import SkillResult
from skills.check_prior_authorization import METADATA as CHECK_PRIOR_AUTH_METADATA
from skills.check_prior_authorization import check_prior_authorization
from skills.escalate_case import METADATA as ESCALATE_CASE_METADATA
from skills.escalate_case import escalate_case
from skills.explain_benefit import METADATA as EXPLAIN_BENEFIT_METADATA
from skills.explain_benefit import explain_benefit
from skills.investigate_claim import METADATA as INVESTIGATE_CLAIM_METADATA
from skills.investigate_claim import investigate_claim

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "skill_golden_set.json"

_SKILL_FUNCTIONS = {
    "investigate_claim": investigate_claim,
    "explain_benefit": explain_benefit,
    "check_prior_authorization": check_prior_authorization,
    "escalate_case": escalate_case,
}

_SKILL_METADATA = {
    "investigate_claim": INVESTIGATE_CLAIM_METADATA,
    "explain_benefit": EXPLAIN_BENEFIT_METADATA,
    "check_prior_authorization": CHECK_PRIOR_AUTH_METADATA,
    "escalate_case": ESCALATE_CASE_METADATA,
}


def _extract_characteristic(result: SkillResult, key: str) -> Any:
    """Read one named evidence characteristic off a SkillResult, in a way
    that's meaningful across differently-shaped skills."""
    evidence = result.evidence

    if key == "claim_id":
        package = evidence.get("evidence_package") or {}
        return package.get("claim_id")
    if key == "policy_chunks_present":
        package = evidence.get("evidence_package")
        if package is not None:
            return bool(package.get("policy_chunks"))
        return bool(evidence.get("policy_chunks"))
    if key == "graph_relationships_present":
        package = evidence.get("evidence_package") or {}
        return bool(package.get("graph_relationships"))
    if key == "benefit_present":
        return evidence.get("benefit") is not None
    if key == "benefit_covered":
        benefit = evidence.get("benefit")
        return benefit.get("covered") if benefit else None
    if key == "authorizations_count":
        return len(evidence.get("authorizations", []))
    if key == "authorization_ids":
        return sorted(a["authorization_id"] for a in evidence.get("authorizations", []))
    if key == "synthetic":
        return evidence.get("synthetic")
    if key == "external_system_contacted":
        return evidence.get("external_system_contacted")

    raise KeyError(f"Unknown evidence characteristic: {key!r}")


@dataclass
class CaseResult:
    case_id: str
    skill_name: str
    expected_status: str
    actual_status: str
    status_correct: bool
    evidence_recall: float
    evidence_mismatches: list[str]
    missing_evidence_accurate: bool
    expected_missing: list[str]
    actual_missing: list[str]
    next_capability_correct: bool
    expected_next_capability: Optional[str]
    actual_next_capability: Optional[str]
    dependency_usage_correct: bool
    expected_dependencies: list[str]
    actual_dependencies: list[str]

    @property
    def is_clean(self) -> bool:
        return (
            self.status_correct
            and not self.evidence_mismatches
            and self.missing_evidence_accurate
            and self.next_capability_correct
            and self.dependency_usage_correct
        )


def load_golden_set(path: Path | None = None) -> list[dict]:
    """Load the skill golden-set cases: case_id, skill_name, input,
    expected_status, expected_evidence_characteristics,
    expected_missing_categories, expected_next_capability,
    expected_dependencies, rationale."""
    return json.loads((path or GOLDEN_SET_PATH).read_text(encoding="utf-8"))


def evaluate_case(item: dict) -> CaseResult:
    skill_name = item["skill_name"]
    func = _SKILL_FUNCTIONS[skill_name]
    metadata = _SKILL_METADATA[skill_name]

    result = func(**item["input"])

    expected_status = item["expected_status"]
    status_correct = result.status.value == expected_status

    expected_characteristics = item.get("expected_evidence_characteristics", {})
    mismatches = []
    for key, expected_value in expected_characteristics.items():
        actual_value = _extract_characteristic(result, key)
        if actual_value != expected_value:
            mismatches.append(f"{key}: expected {expected_value!r}, got {actual_value!r}")
    evidence_recall = (
        (len(expected_characteristics) - len(mismatches)) / len(expected_characteristics)
        if expected_characteristics
        else 1.0
    )

    expected_missing = sorted(item.get("expected_missing_categories", []))
    actual_missing = sorted(result.missing_information)
    missing_accurate = expected_missing == actual_missing

    expected_next = item.get("expected_next_capability")
    actual_next = result.next_capability
    next_correct = expected_next == actual_next

    expected_dependencies = sorted(item.get("expected_dependencies", []))
    actual_dependencies = sorted(metadata.allowed_tools)
    dependency_correct = expected_dependencies == actual_dependencies

    return CaseResult(
        case_id=item["case_id"],
        skill_name=skill_name,
        expected_status=expected_status,
        actual_status=result.status.value,
        status_correct=status_correct,
        evidence_recall=evidence_recall,
        evidence_mismatches=mismatches,
        missing_evidence_accurate=missing_accurate,
        expected_missing=expected_missing,
        actual_missing=actual_missing,
        next_capability_correct=next_correct,
        expected_next_capability=expected_next,
        actual_next_capability=actual_next,
        dependency_usage_correct=dependency_correct,
        expected_dependencies=expected_dependencies,
        actual_dependencies=actual_dependencies,
    )


def evaluate_all(golden_set: list[dict] | None = None) -> list[CaseResult]:
    golden_set = golden_set if golden_set is not None else load_golden_set()
    return [evaluate_case(item) for item in golden_set]


def summarize(results: list[CaseResult]) -> dict[str, float]:
    n = len(results)
    if n == 0:
        return {
            "skill_completion_accuracy": 0.0,
            "required_evidence_recall": 0.0,
            "missing_evidence_accuracy": 0.0,
            "correct_next_capability_rate": 0.0,
            "unexpected_dependency_usage_rate": 0.0,
        }
    return {
        "skill_completion_accuracy": sum(1 for r in results if r.status_correct) / n,
        "required_evidence_recall": sum(r.evidence_recall for r in results) / n,
        "missing_evidence_accuracy": sum(1 for r in results if r.missing_evidence_accurate) / n,
        "correct_next_capability_rate": sum(1 for r in results if r.next_capability_correct) / n,
        "unexpected_dependency_usage_rate": sum(
            1 for r in results if not r.dependency_usage_correct
        )
        / n,
    }


def print_report(results: list[CaseResult]) -> None:
    print(f"=== Skill offline evaluation ({len(results)} cases) ===\n")

    print("Per-case results:")
    for r in results:
        status = "OK" if r.is_clean else "FAIL"
        print(
            f"  [{status}] {r.case_id} ({r.skill_name}): "
            f"expected={r.expected_status} actual={r.actual_status} "
            f"evidence_recall={r.evidence_recall:.0%} "
            f"missing_accurate={r.missing_evidence_accurate} "
            f"next_capability(expected={r.expected_next_capability}, actual={r.actual_next_capability})"
        )
        if not r.status_correct:
            print("        STATUS MISMATCH")
        if r.evidence_mismatches:
            print(f"        evidence mismatches: {r.evidence_mismatches}")
        if not r.missing_evidence_accurate:
            print(
                f"        missing-evidence mismatch: expected {r.expected_missing}, "
                f"got {r.actual_missing}"
            )
        if not r.next_capability_correct:
            print("        next-capability mismatch")
        if not r.dependency_usage_correct:
            print(
                f"        DEPENDENCY MISMATCH: expected {r.expected_dependencies}, "
                f"got {r.actual_dependencies}"
            )
    print()

    summary = summarize(results)
    print("Aggregate summary (see per-case results above -- this does not replace them):")
    print(f"  Skill Completion Accuracy:              {summary['skill_completion_accuracy']:.2%}")
    print(f"  Required Evidence Recall:               {summary['required_evidence_recall']:.2%}")
    print(f"  Missing-Evidence Accuracy:               {summary['missing_evidence_accuracy']:.2%}")
    print(f"  Correct Next-Capability Rate:            {summary['correct_next_capability_rate']:.2%}")
    print(f"  Unexpected Dependency/Tool Usage Rate:   {summary['unexpected_dependency_usage_rate']:.2%}")


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Offline evaluation of the Skills layer against the independently-authored golden set."
    )
    parser.parse_args(argv)

    results = evaluate_all()
    print_report(results)


if __name__ == "__main__":
    _main(sys.argv[1:])
