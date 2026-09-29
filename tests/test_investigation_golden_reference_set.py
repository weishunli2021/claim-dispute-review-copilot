"""Validation tests for Module 4's investigation golden reference dataset
(evals/investigation_golden_reference_set.json).

These tests check dataset INTEGRITY and, for DETERMINISTICALLY_CHECKABLE
findings, that the cited field path actually resolves on real output from
this codebase's own offline (non-LLM) evidence/agent layers and matches
the expected value -- never that a generated brief or judge score agrees
with the reference (no brief/judge is ever run here). All of these run
fully offline: context.hybrid_retriever.build_evidence_package and
agents.case_agent.run_case_agent do local structured/graph/vector
retrieval only, no OpenAI call, matching this project's convention (see
evals/hybrid_retrieval_eval.py, evals/agent_eval.py) of driving these same
two functions without any API key.

review_status is asserted to still be pending_human_review for every case
-- these tests intentionally do NOT and must never flip that themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

import pytest
from pydantic import ValidationError

from agents.case_agent import run_case_agent
from application.workbench import CASE_IDS
from context.hybrid_retriever import build_evidence_package
from evals.golden_reference_models import (
    GOLDEN_REFERENCE_SCHEMA_VERSION,
    DeterministicCheckSpec,
    GoldenReferenceSet,
    RequirementClassification,
    ReviewStatus,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_SET_PATH = REPO_ROOT / "evals" / "investigation_golden_reference_set.json"

_RECORD_ID_PATTERN = re.compile(
    r"^(CLM-\d+|PRV-\d+|PA-\d+|BEN-[A-Z0-9-]+|M-\d+)$"
)
_POLICY_SECTION_PATTERN = re.compile(r"^## ([A-Z]+-\d+)\. ")


@pytest.fixture(scope="module")
def raw_golden_set() -> dict:
    return json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden_set(raw_golden_set: dict) -> GoldenReferenceSet:
    return GoldenReferenceSet.model_validate(raw_golden_set)


@pytest.fixture(scope="module")
def known_record_ids() -> set[str]:
    ids: set[str] = set()
    for filename, id_field in [
        ("claims.json", "claim_id"),
        ("providers.json", "provider_id"),
        ("prior_authorizations.json", "authorization_id"),
        ("benefits.json", "benefit_id"),
        ("members.json", "member_id"),
    ]:
        records = json.loads((REPO_ROOT / "data" / filename).read_text(encoding="utf-8"))
        ids.update(r[id_field] for r in records)
    return ids


@pytest.fixture(scope="module")
def known_policy_sections() -> set[str]:
    sections: set[str] = set()
    documents_dir = REPO_ROOT / "documents"
    for path in documents_dir.glob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _POLICY_SECTION_PATTERN.match(line)
            if match:
                sections.add(match.group(1))
    return sections


# ---------------------------------------------------------------------------
# Structural integrity
# ---------------------------------------------------------------------------


def test_golden_set_validates_against_schema(raw_golden_set: dict) -> None:
    GoldenReferenceSet.model_validate(raw_golden_set)


def test_schema_version_matches_module_constant(golden_set: GoldenReferenceSet) -> None:
    assert golden_set.schema_version == GOLDEN_REFERENCE_SCHEMA_VERSION
    for case in golden_set.cases:
        assert case.schema_version == GOLDEN_REFERENCE_SCHEMA_VERSION


def test_exactly_five_unique_cases_for_the_five_original_claims(
    golden_set: GoldenReferenceSet,
) -> None:
    claim_ids = [case.claim_id for case in golden_set.cases]
    assert len(claim_ids) == 5
    assert len(set(claim_ids)) == 5
    assert set(claim_ids) == set(CASE_IDS)


def test_reference_case_ids_are_unique(golden_set: GoldenReferenceSet) -> None:
    case_ids = [case.reference_case_id for case in golden_set.cases]
    assert len(case_ids) == len(set(case_ids))


def test_no_dispute_preset_or_scenario_claims_present(golden_set: GoldenReferenceSet) -> None:
    """This dataset is for the five original predefined claims only --
    never the dispute-review presets and never the two Module 3 dispute
    scenarios (which are not part of CASE_IDS at all)."""
    for case in golden_set.cases:
        assert case.claim_id.startswith("CLM-100")
        assert case.claim_id in CASE_IDS


# ---------------------------------------------------------------------------
# Metadata / review-status discipline
# ---------------------------------------------------------------------------


def test_every_case_is_ai_drafted_and_pending_human_review(
    golden_set: GoldenReferenceSet,
) -> None:
    for case in golden_set.cases:
        assert case.reference_origin == "AI-drafted"
        assert case.review_status == ReviewStatus.PENDING_HUMAN_REVIEW


def test_review_notes_reflect_drafting_corrections_only_never_a_review(golden_set: GoldenReferenceSet) -> None:
    """review_notes presence is never itself evidence of a human review --
    every case's review_status is checked as PENDING_HUMAN_REVIEW
    elsewhere (test_every_case_is_ai_drafted_and_pending_human_review).
    This test tracks WHICH cases have notes and WHY, so that an
    unexplained note appearing on a case this test doesn't expect gets
    caught rather than silently passing. CLM-1001 carries a note as of
    Module 6 documenting a drafting correction (see
    docs/V2_GOLDEN_REFERENCE_REVIEW.md, "Module 6 correction") -- update
    this test deliberately, never work around it, when an actual human
    review adds a note to any case."""
    by_claim = {case.claim_id: case for case in golden_set.cases}
    assert by_claim["CLM-1001"].review_notes is not None
    assert "Module 6 correction" in by_claim["CLM-1001"].review_notes
    for claim_id in ("CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"):
        assert by_claim[claim_id].review_notes in (None, "")


def test_every_case_has_a_non_empty_source_fingerprint(golden_set: GoldenReferenceSet) -> None:
    for case in golden_set.cases:
        assert case.source_fingerprint.strip() != ""


def test_pending_case_may_carry_review_notes_without_changing_status() -> None:
    """Module 4 review follow-up: a case partway through review, or one
    where a reviewer has only left a question, is still legitimately
    PENDING_HUMAN_REVIEW -- review_notes and review_status are independent
    fields, and the schema must accept notes on a pending case rather than
    forcing a premature status change or rejecting the note."""
    case_set = GoldenReferenceSet.model_validate(
        {
            "schema_version": GOLDEN_REFERENCE_SCHEMA_VERSION,
            "cases": [
                {
                    "reference_case_id": "NOTED-CASE",
                    "claim_id": "CLM-1001",
                    "scenario_title": "x",
                    "scenario_description": "x",
                    "required_findings": [],
                    "unsupported_or_prohibited_conclusions": ["x"],
                    "review_status": "pending_human_review",
                    "review_notes": "Open question: is IMG-2 or PA-2 the more central citation here?",
                    "source_fingerprint": "data/claims.json@sha256:deadbeef",
                }
            ],
        }
    )
    case = case_set.cases[0]
    assert case.review_status == ReviewStatus.PENDING_HUMAN_REVIEW
    assert case.review_notes == "Open question: is IMG-2 or PA-2 the more central citation here?"


def test_deterministically_checkable_finding_without_check_spec_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoldenReferenceSet.model_validate(
            {
                "schema_version": GOLDEN_REFERENCE_SCHEMA_VERSION,
                "cases": [
                    {
                        "reference_case_id": "BAD-CASE-2",
                        "claim_id": "CLM-1001",
                        "scenario_title": "x",
                        "scenario_description": "x",
                        "required_findings": [
                            {
                                "finding_id": "f1",
                                "category": "SOURCE_DERIVED",
                                "classification": "DETERMINISTICALLY_CHECKABLE",
                                "statement": "missing its check spec",
                            }
                        ],
                        "unsupported_or_prohibited_conclusions": ["x"],
                        "source_fingerprint": "data/claims.json@sha256:deadbeef",
                    }
                ],
            }
        )


# ---------------------------------------------------------------------------
# Required content is non-empty (the qualitative-safety fields especially --
# every case must say what it is NOT allowed to conclude).
# ---------------------------------------------------------------------------


def test_every_case_lists_at_least_one_prohibited_conclusion(
    golden_set: GoldenReferenceSet,
) -> None:
    for case in golden_set.cases:
        assert len(case.unsupported_or_prohibited_conclusions) >= 1


def test_every_case_has_at_least_one_required_finding(golden_set: GoldenReferenceSet) -> None:
    for case in golden_set.cases:
        assert len(case.required_findings) >= 1


def test_every_finding_id_is_unique_within_its_case(golden_set: GoldenReferenceSet) -> None:
    for case in golden_set.cases:
        finding_ids = [f.finding_id for f in case.required_findings]
        assert len(finding_ids) == len(set(finding_ids))


# ---------------------------------------------------------------------------
# Resolvable references: cited record IDs and policy sections must be real.
# ---------------------------------------------------------------------------


def test_supporting_record_ids_resolve_to_real_records_or_named_source_files(
    golden_set: GoldenReferenceSet, known_record_ids: set[str]
) -> None:
    for case in golden_set.cases:
        for finding in case.required_findings:
            for record_id in finding.supporting_record_ids:
                is_known_record = record_id in known_record_ids
                is_source_file_citation = record_id.startswith("data/") and record_id.endswith(
                    ".json"
                )
                assert is_known_record or is_source_file_citation, (
                    f"{case.reference_case_id}/{finding.finding_id}: "
                    f"{record_id!r} is neither a known record id nor a "
                    "data/*.json absence citation"
                )


def test_supporting_policy_section_ids_resolve_to_real_document_headers(
    golden_set: GoldenReferenceSet, known_policy_sections: set[str]
) -> None:
    for case in golden_set.cases:
        for finding in case.required_findings:
            for section_id in finding.supporting_policy_section_ids:
                assert section_id in known_policy_sections, (
                    f"{case.reference_case_id}/{finding.finding_id}: "
                    f"policy section {section_id!r} does not exist in documents/*.md"
                )


def test_every_record_id_pattern_used_is_recognized(known_record_ids: set[str]) -> None:
    """Sanity check on the test's own record-id regex: every id in the
    actual synthetic dataset must match it, or the resolvability check
    above would be silently too permissive."""
    for record_id in known_record_ids:
        assert _RECORD_ID_PATTERN.match(record_id), record_id


# ---------------------------------------------------------------------------
# Deterministic expectations use real fields, and evaluate correctly
# against this codebase's own offline evidence/agent output.
# ---------------------------------------------------------------------------


def _resolve_field_path(root: object, field_path: str) -> object:
    value = root
    for part in field_path.split("."):
        if value is None:
            return None
        value = getattr(value, part)
    return value


def _check_passes(spec: DeterministicCheckSpec, actual: object) -> bool:
    if spec.comparator == "equals":
        return actual == spec.expected_value
    if spec.comparator == "is_true":
        return actual is True
    if spec.comparator == "is_false":
        return actual is False
    if spec.comparator == "is_none":
        return actual is None
    if spec.comparator == "is_not_none":
        return actual is not None
    if spec.comparator == "length_equals":
        return len(actual) == spec.expected_value
    if spec.comparator == "length_greater_than":
        return len(actual) > spec.expected_value
    raise AssertionError(f"unhandled comparator {spec.comparator!r}")


_KNOWN_SOURCES = {
    "context.models.EvidencePackage": lambda claim_id: build_evidence_package(
        claim_id, "Explain this claim."
    ),
    "agents.state.AgentResult": lambda claim_id: run_case_agent(claim_id, "Explain this claim."),
}


def _iter_deterministic_findings(golden_set: GoldenReferenceSet):
    for case in golden_set.cases:
        for finding in case.required_findings:
            if finding.classification == RequirementClassification.DETERMINISTICALLY_CHECKABLE:
                assert finding.deterministic_check is not None
                yield case, finding


def test_every_deterministic_check_names_a_recognized_source(
    golden_set: GoldenReferenceSet,
) -> None:
    for case, finding in _iter_deterministic_findings(golden_set):
        assert finding.deterministic_check.source in _KNOWN_SOURCES, (
            f"{case.reference_case_id}/{finding.finding_id}: "
            f"unrecognized source {finding.deterministic_check.source!r}"
        )


@pytest.fixture(scope="module")
def built_objects_by_claim() -> dict[str, dict[str, object]]:
    """Build each source object once per claim_id, offline, and reuse it
    across every deterministic-check test below."""
    cache: dict[str, dict[str, object]] = {}
    for claim_id in CASE_IDS:
        cache[claim_id] = {
            source: factory(claim_id) for source, factory in _KNOWN_SOURCES.items()
        }
    return cache


def test_deterministic_checks_pass_against_real_offline_output(
    golden_set: GoldenReferenceSet, built_objects_by_claim: dict[str, dict[str, object]]
) -> None:
    failures = []
    for case, finding in _iter_deterministic_findings(golden_set):
        spec = finding.deterministic_check
        root = built_objects_by_claim[case.claim_id][spec.source]
        actual = _resolve_field_path(root, spec.field_path)
        if not _check_passes(spec, actual):
            failures.append(
                f"{case.reference_case_id}/{finding.finding_id}: "
                f"{spec.source}.{spec.field_path} {spec.comparator} "
                f"{spec.expected_value!r} -- actual was {actual!r}"
            )
    assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# Source-fingerprint drift detection.
# ---------------------------------------------------------------------------

_FINGERPRINT_ENTRY = re.compile(r"([^\s;]+)@sha256:([0-9a-f]{12})")


def _parse_fingerprint(fingerprint: str) -> list[tuple[str, str]]:
    return _FINGERPRINT_ENTRY.findall(fingerprint)


def test_source_fingerprint_entries_parse_and_cover_at_least_one_file(
    golden_set: GoldenReferenceSet,
) -> None:
    for case in golden_set.cases:
        entries = _parse_fingerprint(case.source_fingerprint)
        assert entries, f"{case.reference_case_id}: source_fingerprint has no parseable entries"


def test_source_fingerprints_detect_drift_from_current_source_files(
    golden_set: GoldenReferenceSet,
) -> None:
    """Recomputes each cited file's sha256 and compares against the
    fingerprint recorded in the case. A mismatch means the underlying
    source data/policy has changed since this reference was drafted and
    the case needs re-review -- not that the test should be updated to
    match the new hash."""
    stale: list[str] = []
    for case in golden_set.cases:
        for relative_path, recorded_hash in _parse_fingerprint(case.source_fingerprint):
            full_path = REPO_ROOT / relative_path
            assert full_path.is_file(), f"{case.reference_case_id}: missing {relative_path}"
            actual_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()[:12]
            if actual_hash != recorded_hash:
                stale.append(
                    f"{case.reference_case_id}: {relative_path} changed "
                    f"(recorded {recorded_hash}, now {actual_hash})"
                )
    assert not stale, "\n".join(stale)
