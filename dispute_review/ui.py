"""Streamlit rendering + session-state helpers for the Dispute Review tab
-- the ONE active scenario: a provider proposes corrected billing fields
(service code, modifier, units, servicing-provider ID) for the isolated
original claim CLM-BILL-9001, and this tab determines whether each
proposed correction is supported by independently recorded service
documentation.

This is the ONLY file in the dispute_review package that imports
Streamlit. State-mutation logic is a set of plain functions over a
MutableMapping (st.session_state in production, a plain dict in tests), so
it is unit-testable without a live Streamlit session; the actual widget
wiring in render_dispute_review_tab is exercised separately via
Streamlit's AppTest (see tests/test_dispute_review_ui.py).

Session-state discipline: every key this module reads or writes is
prefixed `dispute_review_` -- disjoint from Golden Dataset & Evaluation's
keys and Scenario Lab's SCENARIO_*_KEY constants.

Four numbered sections, in this fixed order, matching the required screen
layout (the former, separately-numbered "4. Detailed Evidence, Findings &
Next Action" section has no title of its own any more -- it renders as a
direct continuation of section 3's output, one unbroken result instead of
two separately-headed ones):
  1. Original Claim & Recorded Decision (read-only).
  2. Newly Submitted Corrected Claim Details (editable, "unverified").
  3. Investigation & Comparison Results -- ONE combined "Investigate billing
     correction" button always runs the deterministic comparison first
     (ZERO model calls) and, only if the submission validated and no
     claim/member linkage issue was found, also runs the full investigation
     (retrieves independent records + policy and drafts a cited brief --
     ONE model call). Detailed evidence, workflow status (collapsed by
     default), the brief's findings, and the suggested next action all
     render directly below, under one combined Summary.
  4. Evals: LLM-as-a-Judge -- a SEPARATE second model call, its own
     explicit button click.

Never edits the original claim, never submits a corrected claim, never
reverses a denial, never approves payment, and never triggers retrieval or
a model call merely by rendering the page -- both actions above run only
inside their own button's click handler.
"""

from __future__ import annotations

import re
from typing import MutableMapping, Optional

import streamlit as st
from pydantic import ValidationError

from application.dispute_judge import run_dispute_judge
from application.dispute_judge_models import DisputeJudgeEnvelope, DisputeJudgeStatus
from application.dispute_models import (
    DisputeBrief,
    DisputeGenerationStatus,
    DisputeValidationStatus,
    DisputeWorkflowResult,
    EvidenceGateStatus,
    is_accepted_dispute_draft,
)
from application.dispute_workflow import run_dispute_workflow
from application.judge_models import JudgeVerdict
from context.dispute_evidence_models import DisputeEvidencePackage, EvidenceReference, EvidenceSourceStatus
from dispute_review.billing_fixtures import (
    BILLING_CLAIM_ID,
    get_billing_claim_record,
    get_billing_support_records,
)
from dispute_review.comparison import (
    build_billing_claim_snapshot,
    check_claim_member_linkage,
    compare_billing_correction,
)
from dispute_review.models import (
    BillingClaimSnapshot,
    BillingComparisonResult,
    BillingCorrectionSubmission,
    BillingFindingStatus,
)
from dispute_review.presets import billing_correction_example_preset

CLAIM_ID = BILLING_CLAIM_ID

_PREFIX = "dispute_review_"

SUPPLIED_BY_KEY = f"{_PREFIX}supplied_by"
ORIGINAL_CLAIM_REF_KEY = f"{_PREFIX}original_claim_reference"
MEMBER_ID_KEY = f"{_PREFIX}member_id"
SERVICE_DATE_KEY = f"{_PREFIX}service_date"
SERVICE_CODE_KEY = f"{_PREFIX}service_code"
MODIFIER_KEY = f"{_PREFIX}modifier"
UNITS_KEY = f"{_PREFIX}units"
SERVICING_PROVIDER_KEY = f"{_PREFIX}servicing_provider_id"
PROPOSED_AMOUNT_KEY = f"{_PREFIX}proposed_billed_amount"
EXPLANATION_KEY = f"{_PREFIX}correction_explanation"
SUPPORTING_REFS_KEY = f"{_PREFIX}supporting_record_references"

RESULT_KEY = f"{_PREFIX}result"
RESULT_FINGERPRINT_KEY = f"{_PREFIX}result_fingerprint"
VALIDATION_ERRORS_KEY = f"{_PREFIX}validation_errors"
LINKAGE_ISSUE_KEY = f"{_PREFIX}linkage_issue"

WORKFLOW_RESULT_KEY = f"{_PREFIX}workflow_result"
WORKFLOW_FINGERPRINT_KEY = f"{_PREFIX}workflow_fingerprint"
JUDGE_ENVELOPE_KEY = f"{_PREFIX}judge_envelope"
JUDGE_BINDING_KEY = f"{_PREFIX}judge_binding"

# Every widget key whose change must invalidate a previously displayed result.
INPUT_KEYS: list[str] = [
    SUPPLIED_BY_KEY,
    ORIGINAL_CLAIM_REF_KEY,
    MEMBER_ID_KEY,
    SERVICE_DATE_KEY,
    SERVICE_CODE_KEY,
    MODIFIER_KEY,
    UNITS_KEY,
    SERVICING_PROVIDER_KEY,
    PROPOSED_AMOUNT_KEY,
    EXPLANATION_KEY,
    SUPPORTING_REFS_KEY,
]

NOT_PROVIDED_LABEL = "Not provided"
NOT_RECORDED_LABEL = "Not recorded"

_GATE_LABELS = {
    EvidenceGateStatus.READY_FOR_SCOPED_GENERATION: "Ready for scoped generation",
    EvidenceGateStatus.READY_FOR_LIMITED_BRIEF: "Ready for a limited brief (evidence gaps present)",
    EvidenceGateStatus.BLOCKED: "Blocked — generation not attempted",
}
# "Draft generated" read as confusing/ambiguous next to "Deterministic
# Validation: Passed" -- easy to misread as "the content is good" rather
# than "the model call itself completed." Relabeled as a plain
# succeeded/failed outcome of the MODEL CALL specifically (see the
# "Model Call" metric label below); whether the resulting draft is actually
# sound is the separate "Deterministic Validation" metric right next to it.
_GENERATION_LABELS = {
    DisputeGenerationStatus.NOT_ATTEMPTED: "Not attempted",
    DisputeGenerationStatus.DRAFTED: "Succeeded",
    DisputeGenerationStatus.FAILED: "Failed",
}
_VALIDATION_LABELS = {
    DisputeValidationStatus.NOT_RUN: "Not run",
    DisputeValidationStatus.PASSED: "Passed",
    DisputeValidationStatus.FAILED: "Failed",
}
_SOURCE_OUTCOME_LABELS = {
    EvidenceSourceStatus.SUCCESS_WITH_EVIDENCE: "Evidence retrieved",
    EvidenceSourceStatus.SUCCESS_NO_RESULTS: "Retrieved successfully — no relevant result",
    EvidenceSourceStatus.FAILURE: "Retrieval failed",
}
_JUDGE_STATUS_LABELS = {
    DisputeJudgeStatus.NOT_RUN: "Not run",
    DisputeJudgeStatus.COMPLETED: "Completed",
    DisputeJudgeStatus.FAILED: "Evaluation failed",
}
_VERDICT_LABELS = {
    JudgeVerdict.PASS: "PASS",
    JudgeVerdict.FAIL: "FAIL",
    JudgeVerdict.UNCERTAIN: "UNCERTAIN",
}


# --- pure state helpers (unit-testable with a plain dict) --------------------------------


def _default_widget_values() -> dict[str, str]:
    return {
        SUPPLIED_BY_KEY: "",
        ORIGINAL_CLAIM_REF_KEY: "",
        MEMBER_ID_KEY: "",
        SERVICE_DATE_KEY: "",
        SERVICE_CODE_KEY: "",
        MODIFIER_KEY: "",
        UNITS_KEY: "",
        SERVICING_PROVIDER_KEY: "",
        PROPOSED_AMOUNT_KEY: "",
        EXPLANATION_KEY: "",
        SUPPORTING_REFS_KEY: "",
    }


def ensure_initialized(state: MutableMapping) -> None:
    """Populate every dispute-review widget/result key with its default
    the first time this tab is rendered in a session -- idempotent, and
    must run BEFORE any widget with these keys is instantiated in the same
    script run."""
    for key, default in _default_widget_values().items():
        if key not in state:
            state[key] = default
    for key in (
        RESULT_KEY,
        RESULT_FINGERPRINT_KEY,
        LINKAGE_ISSUE_KEY,
        WORKFLOW_RESULT_KEY,
        WORKFLOW_FINGERPRINT_KEY,
        JUDGE_ENVELOPE_KEY,
        JUDGE_BINDING_KEY,
    ):
        if key not in state:
            state[key] = None
    if VALIDATION_ERRORS_KEY not in state:
        state[VALIDATION_ERRORS_KEY] = []


def clear_judge(state: MutableMapping) -> None:
    state[JUDGE_ENVELOPE_KEY] = None
    state[JUDGE_BINDING_KEY] = None


def clear_result(state: MutableMapping) -> None:
    """Clear any previous comparison result, linkage issue, AI workflow
    result, and judge result -- plus their fingerprints/bindings and
    validation errors. Called whenever ANY dispute-review input changes,
    and whenever the example is loaded or inputs are reset."""
    state[RESULT_KEY] = None
    state[RESULT_FINGERPRINT_KEY] = None
    state[LINKAGE_ISSUE_KEY] = None
    state[VALIDATION_ERRORS_KEY] = []
    state[WORKFLOW_RESULT_KEY] = None
    state[WORKFLOW_FINGERPRINT_KEY] = None
    clear_judge(state)


def _submission_to_widget_values(submission: BillingCorrectionSubmission) -> dict[str, str]:
    return {
        SUPPLIED_BY_KEY: submission.supplied_by or "",
        ORIGINAL_CLAIM_REF_KEY: submission.original_claim_reference or "",
        MEMBER_ID_KEY: submission.member_id or "",
        SERVICE_DATE_KEY: submission.service_date.isoformat() if submission.service_date else "",
        SERVICE_CODE_KEY: submission.service_code or "",
        MODIFIER_KEY: submission.modifier or "",
        UNITS_KEY: str(submission.units) if submission.units is not None else "",
        SERVICING_PROVIDER_KEY: submission.servicing_provider_id or "",
        PROPOSED_AMOUNT_KEY: (
            f"{submission.proposed_billed_amount:.2f}" if submission.proposed_billed_amount is not None else ""
        ),
        EXPLANATION_KEY: submission.correction_explanation or "",
        SUPPORTING_REFS_KEY: ", ".join(submission.supporting_record_references),
    }


def apply_example(state: MutableMapping) -> None:
    """Load the "billing correction example" preset's field values into the
    editable widget state and clear any stale result. Must be called from a
    button's on_click callback -- never after those widgets have already
    been instantiated in the current run."""
    for key, value in _submission_to_widget_values(billing_correction_example_preset()).items():
        state[key] = value
    clear_result(state)


def reset_inputs(state: MutableMapping) -> None:
    """The 'Clear / reset' action: restore every dispute-review widget to
    its blank default and clear any stale result."""
    for key, value in _default_widget_values().items():
        state[key] = value
    clear_result(state)


def _current_fingerprint(state: MutableMapping, claim: BillingClaimSnapshot) -> str:
    """A snapshot of every CURRENTLY COMMITTED widget value plus the claim
    snapshot used, serialized to one comparable string -- the render-guard
    fingerprint for the comparison result, the linkage issue, AND the AI
    workflow result. Uses raw (pre-validation) widget strings, so even an
    edit that would fail validation still changes the fingerprint and hides
    a stale result."""
    widget_part = "\x1f".join(str(state.get(key, "")) for key in INPUT_KEYS)
    return claim.model_dump_json() + "\x1e" + widget_part


def _judge_binding_key(result: DisputeWorkflowResult) -> str:
    """ONE canonical fingerprint binding a judge result to the EXACT
    judge-input revision it was computed from: the workflow's run_id, the
    brief it evaluated, and the generation context actually supplied to
    it."""
    brief_json = result.brief.model_dump_json() if result.brief is not None else ""
    context_json = result.generation_context.model_dump_json() if result.generation_context is not None else ""
    return result.run_id + "\x1e" + brief_json + "\x1e" + context_json


def _format_validation_errors(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        label = loc if loc else "submission"
        messages.append(f"{label}: {error['msg']}")
    return messages


def _parse_supporting_refs(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _build_submission_or_errors(state: MutableMapping) -> tuple[Optional[BillingCorrectionSubmission], list[str]]:
    try:
        submission = BillingCorrectionSubmission(
            supplied_by=state.get(SUPPLIED_BY_KEY, ""),
            original_claim_reference=state.get(ORIGINAL_CLAIM_REF_KEY, ""),
            member_id=state.get(MEMBER_ID_KEY, ""),
            service_date=state.get(SERVICE_DATE_KEY, ""),
            service_code=state.get(SERVICE_CODE_KEY, ""),
            modifier=state.get(MODIFIER_KEY, ""),
            units=state.get(UNITS_KEY, ""),
            servicing_provider_id=state.get(SERVICING_PROVIDER_KEY, ""),
            proposed_billed_amount=state.get(PROPOSED_AMOUNT_KEY, ""),
            correction_explanation=state.get(EXPLANATION_KEY, ""),
            supporting_record_references=_parse_supporting_refs(state.get(SUPPORTING_REFS_KEY, "")),
        )
    except ValidationError as exc:
        return None, _format_validation_errors(exc)
    return submission, []


def run_comparison(state: MutableMapping, claim: BillingClaimSnapshot) -> None:
    """The deterministic-comparison step of the combined 'Investigate
    billing correction' action: build+validate a BillingCorrectionSubmission
    from current widget state, check claim/member linkage, and either store
    a fresh BillingComparisonResult, a linkage-block message, or a list of
    validation errors -- never more than one of the three. Calls the real,
    unmodified dispute_review.comparison.compare_billing_correction -- no
    comparison logic is reimplemented here. ZERO model calls."""
    submission, errors = _build_submission_or_errors(state)
    if errors:
        state[VALIDATION_ERRORS_KEY] = errors
        state[RESULT_KEY] = None
        state[RESULT_FINGERPRINT_KEY] = None
        state[LINKAGE_ISSUE_KEY] = None
        return

    linkage_issue = check_claim_member_linkage(claim, submission)
    state[VALIDATION_ERRORS_KEY] = []
    if linkage_issue is not None:
        state[RESULT_KEY] = None
        state[LINKAGE_ISSUE_KEY] = linkage_issue
        state[RESULT_FINGERPRINT_KEY] = _current_fingerprint(state, claim)
        return

    support_records = get_billing_support_records()
    result = compare_billing_correction(claim, submission, support_records)
    state[LINKAGE_ISSUE_KEY] = None
    state[RESULT_KEY] = result
    state[RESULT_FINGERPRINT_KEY] = _current_fingerprint(state, claim)


def get_display_result(state: MutableMapping, claim: BillingClaimSnapshot) -> Optional[BillingComparisonResult]:
    result = state.get(RESULT_KEY)
    fingerprint = state.get(RESULT_FINGERPRINT_KEY)
    if result is None or fingerprint is None:
        return None
    if fingerprint != _current_fingerprint(state, claim):
        return None
    return result


def get_display_linkage_issue(state: MutableMapping, claim: BillingClaimSnapshot) -> Optional[str]:
    issue = state.get(LINKAGE_ISSUE_KEY)
    fingerprint = state.get(RESULT_FINGERPRINT_KEY)
    if issue is None or fingerprint is None:
        return None
    if fingerprint != _current_fingerprint(state, claim):
        return None
    return issue


def run_investigation(state: MutableMapping, claim: BillingClaimSnapshot, submission: BillingCorrectionSubmission) -> None:
    """Handle the 'Investigate' action: invoke the bounded dispute-review
    AI workflow EXACTLY ONCE, using the CURRENT validated submission and
    CLM-BILL-9001. Calls application.dispute_workflow.run_dispute_workflow
    directly -- no workflow/gate/validation logic is reimplemented here."""
    clear_judge(state)
    state[WORKFLOW_RESULT_KEY] = None
    state[WORKFLOW_FINGERPRINT_KEY] = None
    result = run_dispute_workflow(claim.claim_id, submission)
    state[WORKFLOW_RESULT_KEY] = result
    state[WORKFLOW_FINGERPRINT_KEY] = _current_fingerprint(state, claim)


def get_display_workflow_result(state: MutableMapping, claim: BillingClaimSnapshot) -> Optional[DisputeWorkflowResult]:
    result = state.get(WORKFLOW_RESULT_KEY)
    fingerprint = state.get(WORKFLOW_FINGERPRINT_KEY)
    if result is None or fingerprint is None:
        return None
    if fingerprint != _current_fingerprint(state, claim):
        return None
    return result


def run_investigation_flow(state: MutableMapping, claim: BillingClaimSnapshot) -> None:
    """Handle the single, combined 'Investigate billing correction' button:
    always run the deterministic comparison first (ZERO model calls), then
    -- only if the submission validated and no claim/member linkage issue
    was found -- also run the full AI investigation workflow (ONE model
    call). Calls run_comparison and run_investigation exactly as before;
    no comparison/workflow logic is reimplemented here."""
    run_comparison(state, claim)
    if state.get(VALIDATION_ERRORS_KEY) or state.get(LINKAGE_ISSUE_KEY) is not None:
        return
    submission, submission_errors = _build_submission_or_errors(state)
    if submission is None or submission_errors:
        return
    run_investigation(state, claim, submission)


def run_judge(state: MutableMapping, workflow_result: DisputeWorkflowResult) -> None:
    envelope = run_dispute_judge(workflow_result)
    state[JUDGE_ENVELOPE_KEY] = envelope
    state[JUDGE_BINDING_KEY] = _judge_binding_key(workflow_result)


def get_display_judge_envelope(
    state: MutableMapping, workflow_result: Optional[DisputeWorkflowResult]
) -> Optional[DisputeJudgeEnvelope]:
    if workflow_result is None:
        return None
    envelope = state.get(JUDGE_ENVELOPE_KEY)
    binding = state.get(JUDGE_BINDING_KEY)
    if envelope is None or binding is None:
        return None
    if envelope.run_id != workflow_result.run_id:
        return None
    if binding != _judge_binding_key(workflow_result):
        return None
    return envelope


# --- Streamlit rendering (the only functions in this module that call st.*) --------------


def _fmt(value: object) -> str:
    return str(value) if value not in (None, "") else NOT_PROVIDED_LABEL


def _fmt_recorded_amount(value: Optional[float], recorded: bool) -> str:
    if not recorded:
        return NOT_RECORDED_LABEL
    return f"${value:,.2f}" if value is not None else "$0.00"


def _fmt_derived_amount(units: Optional[int], charge_per_unit: Optional[float]) -> Optional[str]:
    """A DERIVED proposed amount (units x recorded charge-per-unit), shown
    only when both inputs are present -- never labeled as an allowed
    amount or a payment."""
    if units is None or charge_per_unit is None:
        return None
    return f"${units * charge_per_unit:,.2f}"


def _render_original_claim_section(claim: BillingClaimSnapshot) -> None:
    """Section 1: a detailed, read-only snapshot of the original claim and
    its recorded decision. Every value here is read directly from the
    isolated fixture -- this function never invents a missing value."""
    st.subheader("1. Original Claim & Recorded Decision")
    st.caption(
        "SYNTHETIC SCENARIO — this claim, its service codes, and its modifiers are entirely "
        "fictional (not real CPT/HCPCS codes or actual payer data), created only for this "
        "prototype. Nothing below is affected by anything entered in Section 2."
    )

    st.markdown("**Claim identification**")
    identification_rows = [
        {"Field": "Claim ID", "Value": claim.claim_id},
        {"Field": "Claim type", "Value": claim.claim_type},
        {"Field": "Submission date", "Value": _fmt(claim.submission_date)},
        {"Field": "Member", "Value": f"{claim.member_id} — {_fmt(claim.member_name)}"},
        {"Field": "Plan", "Value": f"{_fmt(claim.plan_id)} — {_fmt(claim.plan_name)}"},
        {
            "Field": "Billing provider",
            "Value": f"{_fmt(claim.billing_provider_id)} — {_fmt(claim.billing_provider_name)}",
        },
        {
            "Field": "Servicing provider (as originally submitted)",
            "Value": f"{_fmt(claim.servicing_provider_id)} — {_fmt(claim.servicing_provider_name)}",
        },
    ]
    if claim.ordering_provider_id or claim.ordering_provider_name:
        identification_rows.append(
            {
                "Field": "Ordering/referring provider (context only)",
                "Value": f"{_fmt(claim.ordering_provider_id)} — {_fmt(claim.ordering_provider_name)}",
            }
        )
    st.dataframe(identification_rows, hide_index=True, width="stretch")

    st.markdown("**Fields under investigation** (the four billing fields this scenario checks)")
    investigated_rows = [
        {"Field": "Service code", "Value": claim.service_code or NOT_PROVIDED_LABEL},
        {"Field": "Modifier", "Value": claim.modifier or NOT_PROVIDED_LABEL},
        {"Field": "Units", "Value": str(claim.units) if claim.units is not None else NOT_PROVIDED_LABEL},
        {"Field": "Servicing provider ID", "Value": claim.servicing_provider_id or NOT_PROVIDED_LABEL},
    ]
    st.dataframe(investigated_rows, hide_index=True, width="stretch")
    st.caption(
        f"Service code meaning: {_fmt(claim.service_code_description)}. Modifier meaning: "
        f"{_fmt(claim.modifier_description)}."
    )

    st.markdown("**Original service-line details**")
    service_rows = [
        {"Field": "Service date", "Value": _fmt(claim.service_date)},
        {"Field": "Place of service", "Value": _fmt(claim.place_of_service)},
        {
            "Field": "Charge per unit",
            "Value": _fmt_recorded_amount(claim.charge_per_unit, claim.charge_per_unit is not None),
        },
        {
            "Field": "Total billed",
            "Value": _fmt_recorded_amount(claim.total_billed, claim.total_billed is not None),
        },
        {"Field": "Authorization reference", "Value": claim.authorization_reference or NOT_RECORDED_LABEL},
    ]
    if claim.diagnosis_code:
        service_rows.insert(
            2, {"Field": "Diagnosis code", "Value": f"{claim.diagnosis_code} — {_fmt(claim.diagnosis_description)}"}
        )
    st.dataframe(service_rows, hide_index=True, width="stretch")

    st.markdown("**Recorded decision**")
    decision_rows = [
        {"Field": "Status", "Value": _fmt(claim.decision_status)},
        {"Field": "Decision date", "Value": _fmt(claim.decision_date)},
        {"Field": "Decision code", "Value": _fmt(claim.decision_code)},
        {
            "Field": "Allowed amount",
            "Value": _fmt_recorded_amount(claim.allowed_amount, claim.allowed_amount_recorded),
        },
        {"Field": "Paid amount", "Value": _fmt_recorded_amount(claim.paid_amount, claim.paid_amount_recorded)},
    ]
    st.dataframe(decision_rows, hide_index=True, width="stretch")
    if claim.decision_reason:
        st.write(f"Denial/rejection reason: {claim.decision_reason}")
    if claim.decision_explanation:
        st.caption(f"Decision explanation (recorded): {claim.decision_explanation}")
    if claim.decision_flagged_fields:
        st.write(
            "Fields the ORIGINAL decision explicitly flagged: "
            + ", ".join(claim.decision_flagged_fields)
            + " — any other discrepancy is first identified during investigation below, never "
            "attributed to this recorded decision."
        )

    with st.expander("Additional original claim details"):
        if claim.original_supporting_references:
            st.write("Original supporting references (linked to the original submission):")
            for ref in claim.original_supporting_references:
                st.write(f"- {ref}")
        else:
            st.write(f"Original supporting references: {NOT_RECORDED_LABEL}")
        st.write(f"Original notes: {claim.original_notes or NOT_RECORDED_LABEL}")


def _render_correction_submission_section(state: MutableMapping) -> None:
    """Section 2: the editable, provider-submitted proposed correction --
    clearly labeled unverified. Never touches the original claim or the
    independent supporting records."""
    st.subheader("2. Newly Submitted Corrected Claim Details")
    st.caption("Provider-submitted proposed corrections — unverified.")
    st.caption(
        "Load the example below or enter values manually, then use Section 3 to compare and "
        "investigate. Editing any field clears a previously displayed comparison, brief, and "
        "judge result once the edit is committed."
    )

    col_a, col_b = st.columns(2)
    col_a.button("Load billing correction example", key=f"{_PREFIX}load_example_btn", on_click=apply_example, args=(state,))
    col_b.button("Clear / reset", key=f"{_PREFIX}reset_btn", on_click=reset_inputs, args=(state,))

    def _on_change() -> None:
        clear_result(state)

    col1, col2 = st.columns(2)
    col1.text_input("Supplied by", key=SUPPLIED_BY_KEY, on_change=_on_change)
    col2.text_input("Original claim reference", key=ORIGINAL_CLAIM_REF_KEY, on_change=_on_change)

    col3, col4 = st.columns(2)
    col3.text_input("Member ID", key=MEMBER_ID_KEY, on_change=_on_change)
    col4.text_input("Service date (YYYY-MM-DD)", key=SERVICE_DATE_KEY, on_change=_on_change)

    col5, col6 = st.columns(2)
    col5.text_input("Service code", key=SERVICE_CODE_KEY, on_change=_on_change)
    col6.text_input("Modifier", key=MODIFIER_KEY, on_change=_on_change)

    col7, col8 = st.columns(2)
    col7.text_input("Units", key=UNITS_KEY, on_change=_on_change)
    col8.text_input("Servicing provider ID", key=SERVICING_PROVIDER_KEY, on_change=_on_change)

    st.text_input("Proposed billed amount (optional)", key=PROPOSED_AMOUNT_KEY, on_change=_on_change)
    st.text_area("Correction explanation", key=EXPLANATION_KEY, on_change=_on_change, height=80)
    st.text_input(
        "Supporting record references (comma-separated, optional — for traceability only; the "
        "system independently retrieves the actual supporting records)",
        key=SUPPORTING_REFS_KEY,
        on_change=_on_change,
    )


def _render_comparison_table(result: BillingComparisonResult) -> None:
    table_rows = [
        {
            "Field": row.field,
            "Original Claim": _fmt(row.original_value),
            "Proposed Correction": _fmt(row.proposed_value),
            "Supporting Record Value": _fmt(row.supporting_value),
            "Finding": row.status.value,
            "Evidence Reference": ", ".join(row.evidence_refs) if row.evidence_refs else "(none)",
        }
        for row in result.rows
    ]
    st.dataframe(table_rows, hide_index=True, width="stretch")


_FINDING_VOCAB_LABELS = {
    BillingFindingStatus.SUPPORTED: "Correction supported",
    BillingFindingStatus.CONFLICTS: "Correction conflicts with record",
    BillingFindingStatus.CONSISTENT_NO_CORRECTION_NEEDED: "Already consistent / no correction needed",
    BillingFindingStatus.INSUFFICIENT_EVIDENCE: "Insufficient evidence",
}


def _render_comparison_result(result: BillingComparisonResult) -> None:
    st.markdown("**Comparison result**")
    st.caption(f"Submission provenance: **{result.submission_provenance}**")
    _render_comparison_table(result)
    st.caption(
        "Finding vocabulary: "
        + "; ".join(f"{status.value} = {label}" for status, label in _FINDING_VOCAB_LABELS.items())
    )

    if result.verification_guidance or result.correction_explanation or result.supporting_record_references:
        with st.expander("More detail: verification guidance & submission context"):
            if result.verification_guidance:
                st.markdown("**Verification Guidance**")
                for item in result.verification_guidance:
                    st.write(f"- {item}")
            if result.correction_explanation:
                st.write("Correction explanation:")
                st.text(result.correction_explanation)
            if result.supporting_record_references:
                st.write("Supporting record references cited by the submitter (for traceability only):")
                for ref in result.supporting_record_references:
                    st.write(f"- {ref}")


_NETWORK_DETAIL_RE = re.compile(r"participates_in='([^']*)' plan_requires='([^']*)' in_network=(True|False)")


def _build_vector_search_bullet(package: DisputeEvidencePackage) -> str:
    """Plain-language bullet for the policy vector search -- reformats
    package.policy_passages/source_outcomes (already computed by
    context.dispute_evidence_retriever.gather_policy_evidence), never
    re-derives the search itself."""
    outcomes = [o for o in package.source_outcomes if o.source == "billing_policy"]
    if any(o.status == EvidenceSourceStatus.SUCCESS_WITH_EVIDENCE for o in outcomes):
        sections = ", ".join(ref.label.removeprefix("Policy ") for ref in package.policy_passages)
        return (
            "- **Vector search** (semantic similarity ranking over the billing-review policy): "
            f"found relevant guidance — {sections}."
        )
    if any(o.status == EvidenceSourceStatus.SUCCESS_NO_RESULTS for o in outcomes):
        return "- **Vector search** (semantic similarity ranking over the billing-review policy): no sufficiently relevant section found."
    if outcomes:
        return f"- **Vector search**: did not complete — {outcomes[0].detail or 'see technical detail below'}."
    return "- **Vector search**: not run."


def _build_graph_search_bullet(package: DisputeEvidencePackage) -> str:
    """Plain-language bullet for the provider-network graph search,
    including the actual retrieve path (provider --PARTICIPATES_IN--> its
    network node, compared against the plan's required network node) --
    reformats package.network_relationships/source_outcomes (already
    computed by context.dispute_evidence_retriever.gather_network_evidence
    from dispute_review/billing_graph.py's isolated graph), never
    re-derives or re-queries the graph itself."""
    outcomes = [o for o in package.source_outcomes if o.source == "provider_network"]
    if any(o.status == EvidenceSourceStatus.SUCCESS_WITH_EVIDENCE for o in outcomes):
        parts = []
        for ref in package.network_relationships:
            provider_id = ref.label.rsplit("(", 1)[-1].rstrip(")")
            match = _NETWORK_DETAIL_RE.search(ref.detail)
            if match is None:
                parts.append(ref.label)
                continue
            actual_network, required_network, in_network_str = match.groups()
            if in_network_str == "True":
                parts.append(f"{provider_id} → `PARTICIPATES_IN` → {actual_network!r}, matching the plan's required network (in-network)")
            else:
                parts.append(
                    f"{provider_id} → `PARTICIPATES_IN` → {actual_network!r}, not {required_network!r} as the "
                    "plan requires (NOT in-network)"
                )
        return (
            "- **Graph search** (follows each provider's single `PARTICIPATES_IN` edge to its network node in "
            "the isolated provider-network graph, compared against the plan's required network): " + "; ".join(parts) + "."
        )
    if outcomes and not any(o.status == EvidenceSourceStatus.FAILURE for o in outcomes):
        return "- **Graph search** (provider-network graph lookup): no servicing provider id available to check."
    if outcomes:
        detail = next((o.detail for o in outcomes if o.detail), "see technical detail below")
        return f"- **Graph search**: did not complete — {detail}."
    return "- **Graph search**: not run."


def _build_evidence_bullets(
    comparison_result: BillingComparisonResult, evidence_package: Optional[DisputeEvidencePackage]
) -> list[str]:
    """The three automated-check bullets, in the fixed order: structured
    comparison, vector search, graph search. Absence is stated as absence
    (no relevant section / no provider id to check), never silently
    treated as a pass -- see _build_vector_search_bullet and
    _build_graph_search_bullet."""
    bullets = [
        "- **Structured comparison** (deterministic, four fields vs. independent records): "
        + comparison_result.summary
    ]
    if evidence_package is not None:
        bullets.append(_build_vector_search_bullet(evidence_package))
        bullets.append(_build_graph_search_bullet(evidence_package))
    return bullets


def _render_combined_summary(
    comparison_result: BillingComparisonResult,
    brief: Optional[DisputeBrief],
    evidence_package: Optional[DisputeEvidencePackage] = None,
) -> None:
    """ONE green 'Summary' box for the whole combined comparison+
    investigation output. Order (user request, 2026-09-30): the AI-drafted
    brief's own narrative summary first (when present), then the three
    automated-check bullets -- structured comparison, vector search,
    graph search -- then the Suggested Next Step LAST. Never separate
    'Summary'/'Suggested Next Step' boxes. Everything else (workflow
    status, findings, evidence) still renders as expandable content below
    this box, never inside it."""
    st.markdown("**Summary**")
    parts: list[str] = []
    if brief is not None:
        parts.append(brief.summary)
    parts.append("\n".join(_build_evidence_bullets(comparison_result, evidence_package)))
    if brief is not None:
        step = brief.suggested_next_step
        parts.append(f"**Suggested Next Step: {step.action_code.value}** — {step.rationale}")
    text = "\n\n".join(parts)
    if brief is not None:
        st.success(text)
    else:
        st.info(text)


# --- Section 3b / 4: AI investigation + judge rendering -----------------------------------


def _ref_lookup(result: DisputeWorkflowResult) -> dict[str, EvidenceReference]:
    if result.generation_context is None:
        return {}
    return {ref.ref_id: ref for ref in result.generation_context.references}


def _render_cited_refs(ref_ids: list[str], lookup: dict[str, EvidenceReference]) -> None:
    if not ref_ids:
        st.caption("Evidence references: (none)")
        return
    for ref_id in ref_ids:
        ref = lookup.get(ref_id)
        if ref is not None:
            st.caption(f"↳ `{ref_id}` ({ref.provenance}) — {ref.label}: {ref.detail}")
        else:
            st.caption(f"↳ `{ref_id}` (reference not found in current evidence)")


def _render_workflow_status(result: DisputeWorkflowResult) -> None:
    """Renders inside its own st.expander -- NEVER its own nested expander
    inside it, since Streamlit does not support nesting one expander inside
    another. All the technical status detail lives directly in this same
    expander's body instead of a second, inner one."""
    with st.expander("Workflow Status"):
        status_rows = [
            {
                "Field": "Evidence Gate",
                "Value": _GATE_LABELS.get(result.gate_result.status, result.gate_result.status.value),
            },
            {
                "Field": "Model Call",
                "Value": _GENERATION_LABELS.get(result.generation_status, result.generation_status.value),
            },
            {
                "Field": "Deterministic Validation",
                "Value": _VALIDATION_LABELS.get(result.validation_result.status, result.validation_result.status.value),
            },
        ]
        st.dataframe(status_rows, hide_index=True, width="stretch")
        if result.gate_result.reasons:
            for reason in result.gate_result.reasons:
                st.caption(f"Gate note: {reason}")

        st.markdown("**Exact status details (technical)**")
        st.write(f"skill_status: `{result.skill_status}`")
        if result.skill_error:
            st.write(f"skill_error: {result.skill_error}")
        st.write(f"gate_result.status: `{result.gate_result.status.value}`")
        st.write(f"generation_status: `{result.generation_status.value}`")
        if result.generation_failure_category:
            st.write(f"generation_failure_category: `{result.generation_failure_category.value}`")
        st.write(f"validation_result.status: `{result.validation_result.status.value}`")
        for issue in result.validation_result.issues:
            st.write(f"- **{issue.rule}**: {issue.detail}")
        st.write(f"trace: {', '.join(result.trace)}")


def _render_source_outcomes(result: DisputeWorkflowResult) -> None:
    if result.evidence_package is None:
        return
    st.markdown("**Evidence Source Outcomes**")
    rows = [
        {
            "Source": outcome.source,
            "Result": _SOURCE_OUTCOME_LABELS.get(outcome.status, outcome.status.value),
            "Items": outcome.item_count,
            "Detail": outcome.detail or "",
        }
        for outcome in result.evidence_package.source_outcomes
    ]
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_limitations(result: DisputeWorkflowResult) -> None:
    limitations: list[str] = []
    if result.generation_context is not None:
        limitations = list(result.generation_context.limitations)
    elif result.evidence_package is not None:
        limitations = list(result.evidence_package.limitations)
    if limitations:
        st.markdown("**Evidence Limitations**")
        for item in limitations:
            st.warning(item)
    conflicts = result.evidence_package.conflicts if result.evidence_package is not None else []
    if conflicts:
        st.markdown("**Conflicts**")
        for item in conflicts:
            st.warning(item)
    missing = result.evidence_package.missing_evidence if result.evidence_package is not None else []
    if missing:
        st.markdown("**Missing Evidence**")
        for item in missing:
            st.write(f"- {item}")


def _render_evidence_expander(result: DisputeWorkflowResult) -> None:
    """Renders the evidence content directly -- NEVER its own st.expander,
    since every caller already places this inside one shared expander
    (Streamlit does not support nesting one expander inside another)."""
    package = result.evidence_package
    if package is None:
        return
    st.markdown("**Evidence used for this investigation**")
    st.caption(
        "Grouped, human-readable evidence -- not raw JSON. Each item's reference id is "
        "shown so it can be traced back to a citation in the brief above."
    )

    def _render_group(title: str, refs: list[EvidenceReference]) -> None:
        if not refs:
            return
        st.markdown(f"**{title}**")
        for ref in refs:
            score_note = f" (similarity={ref.score:.4f})" if ref.score is not None else ""
            st.write(f"`{ref.ref_id}`{score_note} — {ref.label}: {ref.detail}")

    _render_group("Recorded Claim & Decision", package.recorded_facts)
    _render_group("Submitted Correction (Unverified)", package.submitted_fields)
    _render_group("Independent Supporting Records", package.support_records)
    _render_group("Synthetic Billing Policy (top-ranked, semantic vector search)", package.policy_passages)
    _render_group("Provider Network Relationships (isolated network graph)", package.network_relationships)
    _render_group("Comparison Findings", package.comparison_findings)

    if package.limitations:
        st.markdown("**Limitations**")
        for item in package.limitations:
            st.write(f"- {item}")


def _render_brief(result: DisputeWorkflowResult) -> None:
    """Renders the AI-drafted brief's remaining detail as expandable
    content only -- no title, no provenance caption, no Summary block, and
    no Suggested Next Step box of its own (its summary AND its suggested
    next step are both folded into the one green combined Summary box
    rendered by _render_combined_summary before this is called). Only its
    supporting evidence for that recommendation still needs its own
    expander here."""
    brief = result.brief
    if brief is None:
        return
    lookup = _ref_lookup(result)

    with st.expander("Findings"):
        for finding in brief.findings:
            st.write(f"- {finding.statement}")
            _render_cited_refs(finding.evidence_refs, lookup)

    step = brief.suggested_next_step
    with st.expander("Evidence for this recommendation"):
        _render_cited_refs(step.evidence_refs, lookup)

    if brief.missing_or_conflicting_evidence or brief.verification_questions:
        with st.expander("More detail: missing/conflicting evidence & verification questions"):
            if brief.missing_or_conflicting_evidence:
                st.markdown("**Missing / Conflicting Evidence**")
                for item in brief.missing_or_conflicting_evidence:
                    st.write(f"- {item}")
            if brief.verification_questions:
                st.markdown("**Verification Questions**")
                st.caption("Case-specific only -- standard checklist items are already covered above.")
                for item in brief.verification_questions:
                    st.write(f"- {item}")


def _render_generation_or_validation_failure(result: DisputeWorkflowResult) -> None:
    if result.generation_status == DisputeGenerationStatus.FAILED:
        category = result.generation_failure_category.value if result.generation_failure_category else "unknown"
        if result.generation_failure_category and result.generation_failure_category.value == "CONFIGURATION":
            st.error(
                "AI investigation requires model configuration that is not currently set "
                "(OPENAI_API_KEY and LLM_MODEL — see .env.example). No live model call was "
                "attempted, and no credentials are displayed here. The deterministic comparison "
                "above is unaffected."
            )
        else:
            st.error(f"AI investigation could not generate a brief (failure category: {category}).")
        with st.expander("Technical detail"):
            st.write(result.skip_or_error_reason or "(no further detail)")
        return

    if result.generation_status == DisputeGenerationStatus.DRAFTED and result.validation_result.status == DisputeValidationStatus.FAILED:
        st.error(
            "A draft was generated but did not pass deterministic validation, so it is not "
            "shown as an accepted brief."
        )
        with st.expander("Validation issues (technical detail)"):
            for issue in result.validation_result.issues:
                st.write(f"- **{issue.rule}**: {issue.detail}")


def _render_investigation_result(state: MutableMapping, result: DisputeWorkflowResult) -> None:
    """Continues directly under Section 3's comparison table and combined
    Summary -- no section-4 title of its own, so the comparison and
    investigation output reads as one continuous result rather than two
    separately-headed sections."""
    _render_workflow_status(result)

    if result.gate_result.status == EvidenceGateStatus.BLOCKED:
        st.error(
            "Evidence was insufficient (or blocked) to attempt AI generation. The deterministic "
            "comparison and any recorded facts found are shown above; no model call was made."
        )
        with st.expander("Evidence source outcomes, limitations & evidence used (technical)"):
            _render_source_outcomes(result)
            _render_limitations(result)
            _render_evidence_expander(result)
        return

    if is_accepted_dispute_draft(result):
        _render_brief(result)
        with st.expander("Evidence source outcomes, limitations & evidence used (technical)"):
            _render_source_outcomes(result)
            _render_limitations(result)
            _render_evidence_expander(result)
        st.divider()
        st.subheader("4. Evals: LLM-as-a-Judge")
        _render_judge_section(state, result)
    else:
        _render_generation_or_validation_failure(result)
        with st.expander("Evidence source outcomes, limitations & evidence used (technical)"):
            _render_source_outcomes(result)
            _render_limitations(result)
            _render_evidence_expander(result)


def _render_judge_section(state: MutableMapping, workflow_result: DisputeWorkflowResult) -> None:
    st.caption(
        "A second, separate LLM-as-a-Judge call assesses the already-drafted brief against its "
        "own evidence. Scores are advisory and uncalibrated; the judge cannot modify the claim or "
        "the brief."
    )
    if st.button("Run LLM-as-a-Judge evaluation", key=f"{_PREFIX}judge_btn"):
        with st.spinner("Evaluating the draft against its evidence…"):
            run_judge(state, workflow_result)

    envelope = get_display_judge_envelope(state, workflow_result)
    if envelope is None:
        return

    if envelope.judge_status == DisputeJudgeStatus.FAILED:
        category = envelope.judge_failure_category.value if envelope.judge_failure_category else "unknown"
        if envelope.judge_failure_category and envelope.judge_failure_category.value == "CONFIGURATION":
            st.error(
                "Evaluation requires model configuration that is not currently set — no "
                "credentials are displayed here. The dispute brief above is unaffected."
            )
        else:
            st.error(f"Evaluation failed (category: {category}). The dispute brief above is unaffected and remains fully usable.")
        with st.expander("Technical detail"):
            st.write(envelope.error or "(no further detail)")
        return

    judged = envelope.result
    lookup = _ref_lookup(workflow_result)

    overall_col1, overall_col2 = st.columns(2)
    overall_col1.markdown(f"**Overall Advisory Score**  \n{judged.overall_score:.2f} / 5")
    overall_col2.markdown(
        f"**Overall Verdict**  \n{_VERDICT_LABELS.get(judged.overall_result, judged.overall_result.value)}"
    )
    st.caption(
        "This average is computed in Python from the four dimension scores above, never by the "
        "model, and never overrides a FAIL/UNCERTAIN dimension shown above. It is an advisory, "
        "uncalibrated rubric score — never an accuracy percentage, a confidence probability, or "
        "proof the draft is verified."
    )

    st.caption("Score guide: 1 = serious issue · 2 = major issue · 3 = mixed/material weakness · 4 = good · 5 = strong (rare)")
    rows = [
        {
            "Dimension": label,
            "Score (1-5)": dim.score,
            "Verdict": _VERDICT_LABELS.get(dim.verdict, dim.verdict.value),
            "Rationale": dim.rationale,
        }
        for label, dim in (
            ("Evidence Grounding", judged.evidence_grounding),
            ("Coverage", judged.coverage),
            ("Original/Proposed & Uncertainty", judged.uncertainty_and_provenance),
            ("Authority Boundaries", judged.authority_boundaries),
        )
    ]
    st.dataframe(rows, hide_index=True, width="stretch")

    with st.expander("More detail: judge rationale & supporting citations"):
        st.markdown("**Rationale**")
        st.write(judged.rationale)
        if judged.unsupported_claims:
            st.markdown("**Unsupported Claims**")
            for item in judged.unsupported_claims:
                st.write(f"- {item}")
        if judged.missing_key_points:
            st.markdown("**Missing Key Points**")
            for item in judged.missing_key_points:
                st.write(f"- {item}")
        for label, dim in (
            ("Evidence Grounding", judged.evidence_grounding),
            ("Coverage", judged.coverage),
            ("Original/Proposed & Uncertainty", judged.uncertainty_and_provenance),
            ("Authority Boundaries", judged.authority_boundaries),
        ):
            if dim.evidence_refs or dim.cited_draft_text:
                st.caption(f"{label} — supporting references:")
                _render_cited_refs(dim.evidence_refs, lookup)
                for text in dim.cited_draft_text:
                    st.caption(f"↳ draft text cited: “{text}”")


def render_dispute_review_tab(state: MutableMapping) -> None:
    """Top-level render function for the Dispute Review tab, called once
    per script run from app.py. Fixture loads run unconditionally on every
    rerun (cheap, deterministic reads of a small local fixture) -- but
    comparison, AI investigation, and judging only ever run inside a
    button's click handler, never merely because this function re-executes
    on every rerun.
    """
    ensure_initialized(state)

    st.caption(
        "Review a provider's proposed corrections to four billing fields on an existing claim, "
        "and determine whether they are supported by independently recorded service "
        "documentation."
    )

    claim_record = get_billing_claim_record()
    claim = build_billing_claim_snapshot(claim_record)

    _render_original_claim_section(claim)
    st.divider()
    _render_correction_submission_section(state)

    st.divider()
    st.subheader("3. Investigation & Comparison Results")
    st.caption(
        "Compares the submission against independently recorded documentation (ZERO model "
        "calls) and, if the submission is valid, also retrieves supporting records and policy "
        "passages, checks provider network participation, and drafts a cited review brief (ONE "
        "model call) -- all from this one button. No model call is made until you click it."
    )
    if st.button("Investigate billing correction", type="primary", key=f"{_PREFIX}investigate_btn"):
        with st.spinner(
            "Comparing against independent records and, if valid, retrieving supporting "
            "evidence and drafting a cited review brief…"
        ):
            run_investigation_flow(state, claim)

    errors = state.get(VALIDATION_ERRORS_KEY) or []
    if errors:
        st.error("The submission could not be validated:")
        for message in errors:
            st.write(f"- {message}")

    linkage_issue = get_display_linkage_issue(state, claim)
    if linkage_issue is not None:
        st.error(linkage_issue)
        return

    result = get_display_result(state, claim)
    if result is not None:
        _render_comparison_result(result)

        workflow_result = get_display_workflow_result(state, claim)
        accepted_brief = None
        if workflow_result is not None and is_accepted_dispute_draft(workflow_result) and workflow_result.brief is not None:
            accepted_brief = workflow_result.brief
        evidence_package = workflow_result.evidence_package if workflow_result is not None else None
        _render_combined_summary(result, accepted_brief, evidence_package)

        if workflow_result is not None:
            _render_investigation_result(state, workflow_result)
