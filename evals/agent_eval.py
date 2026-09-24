"""Offline evaluation of the claim-investigation agent against
evals/agent_golden_set.json.

This evaluates agent BEHAVIOR (which status it reached, which nodes it
ran, whether it correctly flagged a case for human review) -- not
final-answer quality, because final-answer generation does not exist yet
at this stage. Every expectation in the golden set was authored
independently, from the source synthetic data and the prototype
sufficiency rule's own documented logic (see each entry's "rationale"),
never by running the agent first and copying its output.

Five things are measured:

- Terminal Status Accuracy: fraction of cases where the agent's final
  status matches the expected terminal_status.
- Required Action Recall: fraction of a case's required_actions actually
  present (as a set) in the agent's actions_taken trace, averaged across
  cases.
- Unexpected Action Rate: fraction of cases where any forbidden_actions
  entry appears anywhere in the actual trace. Target is 0%.
- Human Review Recall: of cases where human_review_required=true was
  expected, the fraction where the agent actually reached NEEDS_REVIEW.
- Human Review Precision: of cases where the agent actually reached
  NEEDS_REVIEW, the fraction where human_review_required=true was
  expected. Together with recall, this catches both under- and
  over-flagging for review.

Per-case failures are always reported alongside the aggregate summary.

Run with:

    python -m evals.agent_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agents.case_agent import run_case_agent
from agents.state import AgentStatus

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "agent_golden_set.json"


@dataclass
class CaseResult:
    case_id: str
    claim_id: str
    expected_status: str
    actual_status: str
    status_correct: bool
    required_action_recall: float
    missing_required_actions: list[str]
    unexpected_actions_present: list[str]
    expected_human_review: bool
    actual_human_review: bool
    actual_trace: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            self.status_correct
            and not self.missing_required_actions
            and not self.unexpected_actions_present
            and self.expected_human_review == self.actual_human_review
        )


def load_golden_set(path: Path | None = None) -> list[dict]:
    """Load the agent golden-set cases: case_id, claim_id, query,
    terminal_status, required_actions, forbidden_actions,
    evidence_should_be_built, human_review_required, rationale."""
    return json.loads((path or GOLDEN_SET_PATH).read_text(encoding="utf-8"))


def evaluate_case(item: dict) -> CaseResult:
    result = run_case_agent(item["claim_id"], item["query"])
    actual_trace = result.actions_taken

    expected_status = item["terminal_status"]
    actual_status = result.status.value
    status_correct = actual_status == expected_status

    required_actions = item.get("required_actions", [])
    present = [a for a in required_actions if a in actual_trace]
    missing_required = [a for a in required_actions if a not in actual_trace]
    required_recall = len(present) / len(required_actions) if required_actions else 1.0

    forbidden_actions = item.get("forbidden_actions", [])
    unexpected_present = [a for a in forbidden_actions if a in actual_trace]

    expected_human_review = bool(item.get("human_review_required", False))
    actual_human_review = actual_status == AgentStatus.NEEDS_REVIEW.value

    return CaseResult(
        case_id=item["case_id"],
        claim_id=item["claim_id"],
        expected_status=expected_status,
        actual_status=actual_status,
        status_correct=status_correct,
        required_action_recall=required_recall,
        missing_required_actions=missing_required,
        unexpected_actions_present=unexpected_present,
        expected_human_review=expected_human_review,
        actual_human_review=actual_human_review,
        actual_trace=actual_trace,
    )


def evaluate_all(golden_set: list[dict] | None = None) -> list[CaseResult]:
    golden_set = golden_set if golden_set is not None else load_golden_set()
    return [evaluate_case(item) for item in golden_set]


def summarize(results: list[CaseResult]) -> dict[str, float]:
    n = len(results)
    if n == 0:
        return {
            "terminal_status_accuracy": 0.0,
            "required_action_recall": 0.0,
            "unexpected_action_rate": 0.0,
            "human_review_recall": 0.0,
            "human_review_precision": 0.0,
        }

    status_accuracy = sum(1 for r in results if r.status_correct) / n
    action_recall = sum(r.required_action_recall for r in results) / n
    unexpected_rate = sum(1 for r in results if r.unexpected_actions_present) / n

    expected_positive = [r for r in results if r.expected_human_review]
    actual_positive = [r for r in results if r.actual_human_review]
    human_review_recall = (
        sum(1 for r in expected_positive if r.actual_human_review) / len(expected_positive)
        if expected_positive
        else 1.0
    )
    human_review_precision = (
        sum(1 for r in actual_positive if r.expected_human_review) / len(actual_positive)
        if actual_positive
        else 1.0
    )

    return {
        "terminal_status_accuracy": status_accuracy,
        "required_action_recall": action_recall,
        "unexpected_action_rate": unexpected_rate,
        "human_review_recall": human_review_recall,
        "human_review_precision": human_review_precision,
    }


def print_report(results: list[CaseResult]) -> None:
    print(f"=== Agent offline evaluation ({len(results)} cases) ===\n")

    print("Per-case results:")
    for r in results:
        status = "OK" if r.is_clean else "FAIL"
        print(
            f"  [{status}] {r.case_id} (claim={r.claim_id}): "
            f"expected={r.expected_status} actual={r.actual_status} "
            f"action_recall={r.required_action_recall:.0%} "
            f"human_review(expected={r.expected_human_review}, actual={r.actual_human_review})"
        )
        if not r.status_correct:
            print(f"        STATUS MISMATCH: expected {r.expected_status}, got {r.actual_status}")
        if r.missing_required_actions:
            print(f"        missing required actions: {r.missing_required_actions}")
        if r.unexpected_actions_present:
            print(f"        UNEXPECTED ACTIONS PRESENT: {r.unexpected_actions_present}")
        if r.expected_human_review != r.actual_human_review:
            print(
                f"        human review mismatch: expected {r.expected_human_review}, "
                f"got {r.actual_human_review}"
            )
        print(f"        trace: {r.actual_trace}")
    print()

    summary = summarize(results)
    print("Aggregate summary (see per-case results above -- this does not replace them):")
    print(f"  Terminal Status Accuracy: {summary['terminal_status_accuracy']:.2%}")
    print(f"  Required Action Recall:  {summary['required_action_recall']:.2%}")
    print(f"  Unexpected Action Rate:  {summary['unexpected_action_rate']:.2%}")
    print(f"  Human Review Recall:     {summary['human_review_recall']:.2%}")
    print(f"  Human Review Precision:  {summary['human_review_precision']:.2%}")


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Offline evaluation of agent behavior (terminal status, action trace, "
            "human-review flagging) against the independently-authored agent golden set. "
            "Does not evaluate final-answer quality -- no answer is generated yet."
        )
    )
    parser.parse_args(argv)

    results = evaluate_all()
    print_report(results)


if __name__ == "__main__":
    _main(sys.argv[1:])
