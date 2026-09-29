"""Shared result types and helpers for the H4 end-to-end evaluations
(evals/e2e_eval.py: E01-E08 product scenarios; evals/e2e_safety_eval.py:
S01-S09 safety/failure tests). Kept as its own module so the two suites
share one result shape without duplicating it, and so neither imports the
other (they are deliberately separate artifacts -- see each module's own
docstring for why).

Every ScenarioResult is a PASS/FAIL over a small list of named, individually
inspectable checks -- never a single opaque score. Nothing here computes or
implies a percentage, an "accuracy," or a production-readiness verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from application.models import (
    ActionCode,
    Finding,
    InvestigationBrief,
    SuggestedNextStep,
)

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"


@dataclass
class Check:
    name: str
    passed: bool
    observed: Any
    expected: Any

    @staticmethod
    def equals(name: str, observed: Any, expected: Any) -> "Check":
        """The common case: passed is derived from observed == expected,
        never a bare copy of `observed` itself (a bug this project hit
        during development -- a truthy-but-wrong observed value would
        otherwise silently read as "passed"). Use the raw Check(...)
        constructor directly only when `passed` is a genuinely different
        boolean expression than plain equality."""
        return Check(name=name, passed=(observed == expected), observed=observed, expected=expected)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "observed": _jsonable(self.observed),
            "expected": _jsonable(self.expected),
        }


@dataclass
class ScenarioResult:
    scenario_id: str
    description: str
    checks: list[Check] = field(default_factory=list)
    notes: str = ""

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "notes": self.notes,
        }


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of a check's observed/expected value into
    something json.dumps can serialize, for the optional artifact file --
    never used for the pass/fail decision itself, only for the report."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def well_formed_brief(
    summary: str,
    findings: list[tuple[str, list[str]]],
    action_code: ActionCode,
    rationale: str,
    next_step_refs: list[str],
    missing: list[str] | None = None,
) -> InvestigationBrief:
    """Builds a deterministic, H2-conforming InvestigationBrief for use as
    a mocked adapter response -- NOT a live model output. Used throughout
    E01-E08/S01-S09 so the automated suite is reproducible (see each
    module's docstring: H4's core scenarios use a mocked adapter by
    design, going through the REAL application service and H2 validator).
    """
    return InvestigationBrief(
        summary=summary,
        findings=[Finding(statement=statement, evidence_refs=refs) for statement, refs in findings],
        missing_or_conflicting_evidence=missing or [],
        suggested_next_step=SuggestedNextStep(
            action_code=action_code, rationale=rationale, evidence_refs=next_step_refs
        ),
    )


def print_scenario_report(title: str, results: list[ScenarioResult]) -> None:
    print(f"=== {title} ({len(results)} scenarios) ===\n")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.scenario_id} - {result.description}")
        for check in result.checks:
            mark = "ok" if check.passed else "FAIL"
            print(f"    [{mark}] {check.name}: observed={check.observed!r} expected={check.expected!r}")
        if result.notes:
            print(f"    note: {result.notes}")
        print()

    n_passed = sum(1 for r in results if r.passed)
    print(f"{n_passed} of {len(results)} defined prototype scenarios passed their specified checks.")
    if n_passed < len(results):
        print("Failing scenarios: " + ", ".join(r.scenario_id for r in results if not r.passed))


def write_artifact(results: list[ScenarioResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([r.to_dict() for r in results], indent=2, default=str),
        encoding="utf-8",
    )
