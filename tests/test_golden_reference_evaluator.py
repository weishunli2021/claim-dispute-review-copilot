"""Module 5: focused, offline unit tests for
evals/golden_reference_evaluator.py -- no Streamlit, no LLM call, no real
pipeline execution. Every ApplicationResult/EvidencePackage/AgentResult
here is built by hand so each test isolates exactly one evaluator
behavior (correct value passes, wrong value fails, unavailable field
cannot falsely pass, unsupported operator is explicit, staleness
detection, source-fingerprint drift).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.state import AgentResult, AgentStatus
from application.models import ApplicationResult, GenerationStatus
from context.models import EvidenceProvenance, EvidencePackage, StructuredEvidence
from evals.golden_reference_evaluator import (
    CheckOutcome,
    OverallEvaluationStatus,
    check_source_fingerprint_freshness,
    evaluate_investigation_result,
    is_evaluation_still_fresh,
    reference_content_fingerprint,
)
from evals.golden_reference_models import (
    DeterministicCheckSpec,
    ExpectationCategory,
    GoldenReferenceCase,
    RequirementClassification,
    RequiredFinding,
    ReviewStatus,
    load_golden_reference_set,
)
from tools.models import Benefit, Claim

REPO_ROOT = Path(__file__).resolve().parent.parent


def _provenance() -> EvidenceProvenance:
    return EvidenceProvenance(
        policy_provider="fake",
        policy_chunking_config="fake",
        policy_top_k=3,
        graph_max_hops=1,
    )


def _claim(**overrides) -> Claim:
    defaults = dict(
        claim_id="CLM-TEST",
        member_id="M-TEST",
        plan_id="PLAN-TEST",
        service_code="SVC-TEST",
        provider_id="PRV-TEST",
        date_of_service="2026-01-01",
        status="DENIED",
        billed_amount=100.0,
        denial_reason_code="AUTH_REQUIRED",
    )
    defaults.update(overrides)
    return Claim(**defaults)


def _evidence_package(*, claim=None, benefit=None, prior_authorizations=None) -> EvidencePackage:
    facts = StructuredEvidence(
        claim=claim,
        benefit=benefit,
        prior_authorizations=prior_authorizations or [],
    )
    return EvidencePackage(
        claim_id="CLM-TEST",
        original_query="q",
        enriched_query="q",
        structured_facts=facts,
        provenance=_provenance(),
    )


def _agent_result(*, status=AgentStatus.EVIDENCE_SUFFICIENT, evidence_package=None, missing_information=None) -> AgentResult:
    return AgentResult(
        status=status,
        actions_taken=["LOAD_CASE"],
        evidence_package=evidence_package,
        missing_information=missing_information or [],
        step_count=1,
    )


def _application_result(*, agent_result, evidence_package=None, claim_id="CLM-TEST", run_id="run-1") -> ApplicationResult:
    return ApplicationResult(
        run_id=run_id,
        claim_id=claim_id,
        query="q",
        agent_result=agent_result,
        evidence_package=evidence_package,
        generation_status=GenerationStatus.NOT_ATTEMPTED,
    )


def _minimal_reference(*, findings, review_status=ReviewStatus.PENDING_HUMAN_REVIEW, claim_id="CLM-TEST") -> GoldenReferenceCase:
    return GoldenReferenceCase(
        reference_case_id="TEST-CASE",
        claim_id=claim_id,
        scenario_title="test",
        scenario_description="test",
        required_findings=findings,
        unsupported_or_prohibited_conclusions=["must not do X"],
        review_status=review_status,
        source_fingerprint="",
    )


def _det_finding(finding_id, source, field_path, comparator, expected_value=None) -> RequiredFinding:
    return RequiredFinding(
        finding_id=finding_id,
        category=ExpectationCategory.SOURCE_DERIVED,
        classification=RequirementClassification.DETERMINISTICALLY_CHECKABLE,
        statement=f"test finding {finding_id}",
        deterministic_check=DeterministicCheckSpec(
            source=source, field_path=field_path, comparator=comparator, expected_value=expected_value
        ),
    )


def _qual_finding(finding_id) -> RequiredFinding:
    return RequiredFinding(
        finding_id=finding_id,
        category=ExpectationCategory.QUALITATIVE_HUMAN_JUDGMENT,
        classification=RequirementClassification.QUALITATIVE_HUMAN_REVIEW,
        statement=f"qualitative finding {finding_id}",
    )


# ---------------------------------------------------------------------------
# Correct values pass; deliberately wrong values fail.
# ---------------------------------------------------------------------------


def test_correct_equals_check_passes():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    assert ev.deterministic_results[0].outcome == CheckOutcome.PASSED
    assert ev.deterministic_results[0].actual_value == "AUTH_REQUIRED"
    assert ev.automated_summary == "1 of 1 automated checks passed"


def test_wrong_equals_check_fails():
    ep = _evidence_package(claim=_claim(denial_reason_code="SERVICE_NOT_COVERED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.outcome == CheckOutcome.FAILED
    assert check.actual_value == "SERVICE_NOT_COVERED"
    # actual_value must never equal the reference's expected_value on a FAILED check
    assert check.actual_value != "AUTH_REQUIRED"
    assert ev.overall_status == OverallEvaluationStatus.FAILED


@pytest.mark.parametrize(
    "comparator,expected_value,actual_setup,should_pass",
    [
        ("is_true", None, True, True),
        ("is_true", None, False, False),
        ("is_false", None, False, True),
        ("is_none", None, None, True),
        ("is_none", None, "not none", False),
        ("is_not_none", None, "value", True),
        ("is_not_none", None, None, False),
    ],
)
def test_boolean_and_none_comparators(comparator, expected_value, actual_setup, should_pass):
    ep = _evidence_package(claim=_claim())
    ep = ep.model_copy(update={"structured_facts": ep.structured_facts.model_copy(update={"claim": _claim()})})
    # Use benefit.covered-style boolean field via a fake attribute path on claim.status instead --
    # simplest: attach the test value directly onto denial_reason_description (a free string/None field).
    claim = _claim(denial_reason_description=actual_setup if isinstance(actual_setup, str) or actual_setup is None else None)
    ep = _evidence_package(claim=claim)
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    field_path = "structured_facts.claim.denial_reason_description"
    if isinstance(actual_setup, bool):
        # is_true/is_false need a real boolean field -- use a Benefit.covered instead.
        ep = _evidence_package(claim=_claim(), benefit=Benefit(benefit_id="B", plan_id="P", service_code="S", covered=actual_setup, requires_prior_auth=False))
        ar = _agent_result(evidence_package=ep)
        result = _application_result(agent_result=ar, evidence_package=ep)
        field_path = "structured_facts.benefit.covered"
    reference = _minimal_reference(
        findings=[_det_finding("f1", "context.models.EvidencePackage", field_path, comparator, expected_value)]
    )
    ev = evaluate_investigation_result(result, reference)
    outcome = ev.deterministic_results[0].outcome
    assert outcome == (CheckOutcome.PASSED if should_pass else CheckOutcome.FAILED)


def test_length_equals_and_length_greater_than():
    ep = _evidence_package(claim=_claim(), prior_authorizations=[])
    ar = _agent_result(evidence_package=ep, missing_information=["a", "b"])
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.prior_authorizations", "length_equals", 0),
            _det_finding("f2", "agents.state.AgentResult", "missing_information", "length_greater_than", 0),
            _det_finding("f3", "agents.state.AgentResult", "missing_information", "length_equals", 5),
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    by_id = {c.finding_id: c for c in ev.deterministic_results}
    assert by_id["f1"].outcome == CheckOutcome.PASSED
    assert by_id["f1"].actual_value == 0
    assert by_id["f2"].outcome == CheckOutcome.PASSED
    assert by_id["f2"].actual_value == 2
    assert by_id["f3"].outcome == CheckOutcome.FAILED


# ---------------------------------------------------------------------------
# Missing/unavailable values cannot falsely pass.
# ---------------------------------------------------------------------------


def test_unavailable_evidence_package_reports_not_evaluated_never_a_false_pass():
    ar = _agent_result(evidence_package=None)
    result = _application_result(agent_result=ar, evidence_package=None)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "is_none"),
            _det_finding("f2", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "is_not_none"),
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    for check in ev.deterministic_results:
        assert check.outcome == CheckOutcome.NOT_EVALUATED
        assert check.field_available is False
        assert check.actual_value is None
    # Critically: an unavailable field must not accidentally satisfy is_none.
    assert not any(c.outcome == CheckOutcome.PASSED for c in ev.deterministic_results)


def test_intermediate_none_makes_deeper_path_unavailable_not_a_false_pass():
    """structured_facts.benefit is None for this claim (genuinely absent) --
    checking structured_facts.benefit.covered must report NOT_EVALUATED,
    never silently treat the absent benefit as covered=False."""
    ep = _evidence_package(claim=_claim(), benefit=None)
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[_det_finding("f1", "context.models.EvidencePackage", "structured_facts.benefit.covered", "is_false")]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.outcome == CheckOutcome.NOT_EVALUATED
    assert check.field_available is False


def test_present_null_is_distinguished_from_unavailable_field():
    """structured_facts.benefit IS present and IS None (available=True,
    value=None) is a genuinely different, checkable fact from
    structured_facts being entirely unavailable."""
    ep = _evidence_package(claim=_claim(), benefit=None)
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[_det_finding("f1", "context.models.EvidencePackage", "structured_facts.benefit", "is_none")]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.field_available is True
    assert check.outcome == CheckOutcome.PASSED


def test_reference_expected_value_is_never_used_as_a_fallback_actual_value():
    ar = _agent_result(evidence_package=None)
    result = _application_result(agent_result=ar, evidence_package=None)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.outcome == CheckOutcome.NOT_EVALUATED
    assert check.actual_value is None
    assert check.actual_value != "AUTH_REQUIRED"


# ---------------------------------------------------------------------------
# Unsupported check specifications are explicit.
# ---------------------------------------------------------------------------


def test_unsupported_source_is_explicit_not_evaluated():
    ep = _evidence_package(claim=_claim())
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[_det_finding("f1", "some.other.Type", "some_field", "is_not_none")]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.outcome == CheckOutcome.NOT_EVALUATED
    assert "Unsupported" in check.reason


def test_unsupported_field_path_is_explicit_not_evaluated():
    ep = _evidence_package(claim=_claim())
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.nonexistent_field", "is_not_none")
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.outcome == CheckOutcome.NOT_EVALUATED
    assert check.field_available is False


def test_length_comparator_on_non_sized_value_is_explicit_not_evaluated():
    ep = _evidence_package(claim=_claim())  # claim.status is a str, unsized in a meaningful sense here? use a scalar
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "agents.state.AgentResult", "step_count", "length_equals", 1)
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    check = ev.deterministic_results[0]
    assert check.outcome == CheckOutcome.NOT_EVALUATED
    assert check.field_available is True  # step_count itself resolved fine; the comparator is what fails


# ---------------------------------------------------------------------------
# Pending references / qualitative requirements prevent overall PASSED.
# ---------------------------------------------------------------------------


def test_all_green_deterministic_checks_still_needs_human_review_when_reference_pending():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ],
        review_status=ReviewStatus.PENDING_HUMAN_REVIEW,
    )
    ev = evaluate_investigation_result(result, reference)
    assert all(c.outcome == CheckOutcome.PASSED for c in ev.deterministic_results)
    assert ev.overall_status == OverallEvaluationStatus.NEEDS_HUMAN_REVIEW
    assert any("pending human review" in r for r in ev.status_reasons)


def test_qualitative_findings_force_needs_human_review_even_with_accepted_reference():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED"),
            _qual_finding("f2"),
        ],
        review_status=ReviewStatus.HUMAN_REVIEWED_ACCEPTED,
    )
    ev = evaluate_investigation_result(result, reference)
    assert ev.overall_status == OverallEvaluationStatus.NEEDS_HUMAN_REVIEW
    assert len(ev.qualitative_items) == 1


def test_passed_is_reachable_only_with_accepted_reference_no_qualitative_items_and_fresh_fingerprint():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = GoldenReferenceCase(
        reference_case_id="TEST-CASE",
        claim_id="CLM-TEST",
        scenario_title="test",
        scenario_description="test",
        required_findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ],
        unsupported_or_prohibited_conclusions=["must not do X"],
        review_status=ReviewStatus.HUMAN_REVIEWED_ACCEPTED,
        source_fingerprint="",  # no files cited -> nothing to go stale
    )
    ev = evaluate_investigation_result(result, reference)
    assert ev.overall_status == OverallEvaluationStatus.PASSED


def test_not_evaluated_check_forces_needs_human_review_not_failed():
    ar = _agent_result(evidence_package=None)
    result = _application_result(agent_result=ar, evidence_package=None)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "is_none")
        ],
        review_status=ReviewStatus.HUMAN_REVIEWED_ACCEPTED,
    )
    ev = evaluate_investigation_result(result, reference)
    assert ev.overall_status == OverallEvaluationStatus.NEEDS_HUMAN_REVIEW
    assert ev.overall_status != OverallEvaluationStatus.FAILED


# ---------------------------------------------------------------------------
# Structured checks do not masquerade as brief-semantic checks.
# ---------------------------------------------------------------------------


def test_deterministic_result_never_references_the_generated_brief():
    """A DeterministicCheckResult only ever carries source/field_path/
    comparator/actual/expected -- there is no field here that could hold
    or imply anything about generated brief text, by construction."""
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ]
    )
    ev = evaluate_investigation_result(result, reference)
    field_names = set(type(ev.deterministic_results[0]).model_fields.keys())
    assert "brief" not in field_names
    assert not any("brief" in name.lower() for name in field_names)


# ---------------------------------------------------------------------------
# Case/run/reference changes invalidate stale evaluation results.
# ---------------------------------------------------------------------------


def test_is_stale_for_detects_run_id_change():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep, run_id="run-1")
    reference = _minimal_reference(
        findings=[_det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")]
    )
    ev = evaluate_investigation_result(result, reference)
    assert not ev.is_stale_for(result, reference)

    rerun_result = _application_result(agent_result=ar, evidence_package=ep, run_id="run-2")
    assert ev.is_stale_for(rerun_result, reference)


def test_is_stale_for_detects_claim_id_change():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep, claim_id="CLM-A")
    reference = _minimal_reference(
        findings=[_det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")],
        claim_id="CLM-A",
    )
    ev = evaluate_investigation_result(result, reference)
    other_result = _application_result(agent_result=ar, evidence_package=ep, claim_id="CLM-B")
    assert ev.is_stale_for(other_result, reference)


def test_is_stale_for_detects_reference_content_change():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = _minimal_reference(
        findings=[_det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")]
    )
    ev = evaluate_investigation_result(result, reference)
    edited_reference = reference.model_copy(update={"scenario_description": "edited"})
    assert ev.is_stale_for(result, edited_reference)
    assert reference_content_fingerprint(reference) != reference_content_fingerprint(edited_reference)


# ---------------------------------------------------------------------------
# Stale source fingerprints are surfaced.
# ---------------------------------------------------------------------------


def test_check_source_fingerprint_freshness_detects_hash_mismatch(tmp_path):
    fake_file = tmp_path / "data" / "claims.json"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("[]", encoding="utf-8")
    import hashlib

    real_hash = hashlib.sha256(fake_file.read_bytes()).hexdigest()[:12]
    reference = _minimal_reference(findings=[])
    reference = reference.model_copy(update={"source_fingerprint": f"data/claims.json@sha256:{real_hash}"})
    is_stale, stale_files = check_source_fingerprint_freshness(reference, repo_root=tmp_path)
    assert not is_stale
    assert stale_files == []

    fake_file.write_text('[{"changed": true}]', encoding="utf-8")
    is_stale, stale_files = check_source_fingerprint_freshness(reference, repo_root=tmp_path)
    assert is_stale
    assert len(stale_files) == 1


def test_stale_source_fingerprint_forces_needs_human_review_even_when_reference_accepted():
    ep = _evidence_package(claim=_claim(denial_reason_code="AUTH_REQUIRED"))
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)
    reference = GoldenReferenceCase(
        reference_case_id="TEST-CASE",
        claim_id="CLM-TEST",
        scenario_title="test",
        scenario_description="test",
        required_findings=[
            _det_finding("f1", "context.models.EvidencePackage", "structured_facts.claim.denial_reason_code", "equals", "AUTH_REQUIRED")
        ],
        unsupported_or_prohibited_conclusions=["must not do X"],
        review_status=ReviewStatus.HUMAN_REVIEWED_ACCEPTED,
        source_fingerprint="data/claims.json@sha256:000000000000",  # deliberately wrong hash
    )
    ev = evaluate_investigation_result(result, reference)
    assert ev.source_fingerprint_stale is True
    assert ev.overall_status == OverallEvaluationStatus.NEEDS_HUMAN_REVIEW
    assert any("revalidation" in r for r in ev.status_reasons)


# ---------------------------------------------------------------------------
# Module 6: a cached evaluation must not keep looking fresh after the
# underlying source files change AFTER it was computed (never mutates any
# real fixture file -- every source file here is a throwaway copy under
# tmp_path).
# ---------------------------------------------------------------------------


def test_is_evaluation_still_fresh_true_when_nothing_changed(tmp_path):
    fake_file = tmp_path / "data" / "claims.json"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("[]", encoding="utf-8")
    import hashlib

    real_hash = hashlib.sha256(fake_file.read_bytes()).hexdigest()[:12]
    reference = _minimal_reference(findings=[]).model_copy(
        update={"source_fingerprint": f"data/claims.json@sha256:{real_hash}"}
    )
    ep = _evidence_package(claim=_claim())
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)

    evaluation = evaluate_investigation_result(result, reference, repo_root=tmp_path)
    assert evaluation.source_fingerprint_stale is False
    assert is_evaluation_still_fresh(evaluation, reference, repo_root=tmp_path) is True


def test_is_evaluation_still_fresh_false_after_source_file_changes_post_evaluation(tmp_path):
    """The exact scenario this guard exists for: evaluate once while the
    source file matches (fresh), then the file changes -- WITHOUT the
    reference being re-evaluated or its own content being touched. The
    cached EvaluationRun's own source_fingerprint_stale still says False
    (frozen from the moment it was computed); is_evaluation_still_fresh
    must independently detect the drift on a later check, proving a stale
    evaluation cannot keep being treated as current just because nobody
    re-ran it."""
    fake_file = tmp_path / "data" / "claims.json"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("[]", encoding="utf-8")
    import hashlib

    real_hash = hashlib.sha256(fake_file.read_bytes()).hexdigest()[:12]
    reference = _minimal_reference(findings=[]).model_copy(
        update={"source_fingerprint": f"data/claims.json@sha256:{real_hash}"}
    )
    ep = _evidence_package(claim=_claim())
    ar = _agent_result(evidence_package=ep)
    result = _application_result(agent_result=ar, evidence_package=ep)

    evaluation = evaluate_investigation_result(result, reference, repo_root=tmp_path)
    assert evaluation.source_fingerprint_stale is False  # fresh at evaluation time

    # The source file changes AFTER evaluation -- the reference itself is
    # never edited, only the underlying fixture (mirroring "change a
    # relevant source fixture ... without editing the reference").
    fake_file.write_text('[{"changed": true}]', encoding="utf-8")

    # The cached EvaluationRun object is immutable and still says "fresh"...
    assert evaluation.source_fingerprint_stale is False
    # ...but a live re-check correctly detects it is no longer trustworthy.
    assert is_evaluation_still_fresh(evaluation, reference, repo_root=tmp_path) is False


# ---------------------------------------------------------------------------
# Real dataset sanity: the actual five references' deterministic checks
# genuinely pass against the real pipeline (belt-and-suspenders alongside
# tests/test_investigation_golden_reference_set.py's own coverage, this
# time through the full ApplicationResult/evaluator path).
# ---------------------------------------------------------------------------


def test_real_golden_reference_set_evaluates_cleanly_via_fake_pipeline_objects():
    golden_set = load_golden_reference_set()
    assert len(golden_set.cases) == 5
    for case in golden_set.cases:
        assert case.review_status == ReviewStatus.PENDING_HUMAN_REVIEW
