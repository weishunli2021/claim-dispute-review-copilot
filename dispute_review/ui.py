"""Module 4 (deterministic comparison) + Module 6C (optional AI
investigation and optional judge): Streamlit rendering + session-state
helpers for the Dispute Review tab.

This is the ONLY file in the dispute_review package that imports
Streamlit. Mirrors application/workbench.py's split: state-mutation logic
is a set of plain functions over a MutableMapping (st.session_state in
production, a plain dict in tests), so it is unit-testable without a live
Streamlit session, and the actual widget wiring in render_dispute_review_tab
is exercised separately via Streamlit's AppTest (see
tests/test_dispute_review_ui.py).

Session-state discipline: every key this module reads or writes is
prefixed `dispute_review_` (see _PREFIX below) -- a namespace wholly
disjoint from Predefined Claims' keys and Scenario Lab's SCENARIO_*_KEY
constants. Nothing here reads, writes, or clears any key outside that
prefix.

No module-level mutable state and no st.cache_data/st.cache_resource for
submissions, workflow results, or judge results -- every value lives only
in the MutableMapping the caller passes in.

Three independent actions, three independent zero-or-one-model-call
boundaries:
  1. "Compare submitted information" -- dispute_review.comparison.compare_submission.
     ZERO model calls, always available. Unchanged from Module 4/5.
  2. "Investigate dispute evidence" (Module 6C, NEW) -- calls
     application.dispute_workflow.run_dispute_workflow ONCE, only on
     explicit click, only after a valid current comparison exists. Never
     calls application.investigation_service.run_investigation (the
     ORIGINAL claim-investigation pipeline) -- that pipeline remains
     entirely separate and still does not include this submission.
  3. "Run AI semantic evaluation" (Module 6C, NEW) -- calls
     application.dispute_judge.run_dispute_judge ONCE, only on explicit
     click, only when the CURRENT workflow result has an accepted
     (DRAFTED + PASSED-validation) brief. Never runs automatically.

Does NOT reuse application/scenario_lab.py's DataStore-boundary override
anywhere -- this tab only ever performs read-only lookups and pure,
in-memory computation (comparison, evidence assembly, generation,
validation, judging). It never mutates the DataStore, claims,
authorizations, or the graph.
"""

from __future__ import annotations

from typing import MutableMapping, Optional

import streamlit as st
from pydantic import ValidationError

from application.dispute_judge import run_dispute_judge
from application.dispute_judge_models import DisputeJudgeEnvelope, DisputeJudgeStatus
from application.dispute_models import (
    DisputeGenerationStatus,
    DisputeValidationStatus,
    DisputeWorkflowResult,
    EvidenceGateStatus,
    is_accepted_dispute_draft,
)
from application.dispute_workflow import run_dispute_workflow
from application.judge_models import JudgeVerdict
from context.dispute_evidence_models import EvidenceReference, EvidenceSourceStatus
from dispute_review.comparison import build_claim_snapshot, compare_submission
from dispute_review.models import ClaimSnapshot, ComparisonStatus, DisputeComparisonResult, DisputeSubmission, SubmissionSource
from dispute_review.presets import build_demo_presets
from tools.case_context import CaseContext, get_case_context

CLAIM_ID = "CLM-1001"

_PREFIX = "dispute_review_"

SUPPLIED_BY_KEY = f"{_PREFIX}supplied_by"
AUTH_REFERENCE_KEY = f"{_PREFIX}auth_reference"
MEMBER_ID_KEY = f"{_PREFIX}member_id"
SERVICE_CODE_KEY = f"{_PREFIX}service_code"
START_DATE_KEY = f"{_PREFIX}start_date"
END_DATE_KEY = f"{_PREFIX}end_date"
SERVICING_PROVIDER_KEY = f"{_PREFIX}servicing_provider_id"
EXPLANATION_KEY = f"{_PREFIX}explanation"

RESULT_KEY = f"{_PREFIX}result"
RESULT_FINGERPRINT_KEY = f"{_PREFIX}result_fingerprint"
VALIDATION_ERRORS_KEY = f"{_PREFIX}validation_errors"

# Module 6C additions -- same dispute_review_ namespace, no competing prefix.
WORKFLOW_RESULT_KEY = f"{_PREFIX}workflow_result"
WORKFLOW_FINGERPRINT_KEY = f"{_PREFIX}workflow_fingerprint"
JUDGE_ENVELOPE_KEY = f"{_PREFIX}judge_envelope"
JUDGE_BINDING_KEY = f"{_PREFIX}judge_binding"

# Every widget key whose change must invalidate a previously displayed
# result -- authorization reference, member, service, either date,
# servicing provider, explanation, and supplied-by, per Module 4 Step 4.
INPUT_KEYS: list[str] = [
    SUPPLIED_BY_KEY,
    AUTH_REFERENCE_KEY,
    MEMBER_ID_KEY,
    SERVICE_CODE_KEY,
    START_DATE_KEY,
    END_DATE_KEY,
    SERVICING_PROVIDER_KEY,
    EXPLANATION_KEY,
]

_SUPPLIED_BY_PROVIDER_LABEL = "Provider"
_SUPPLIED_BY_PATIENT_LABEL = "Patient"
_SUPPLIED_BY_OPTIONS = [_SUPPLIED_BY_PROVIDER_LABEL, _SUPPLIED_BY_PATIENT_LABEL]

NOT_PROVIDED_LABEL = "Not provided"

# Readable labels -- "Ready for scoped generation" must NEVER be shown as
# "Authorization verified" / "Complete evidence" / "Approved" (Module 6C
# Step 3's explicit instruction). These are presentation-only string maps;
# they add no new business logic and never change which enum value the
# backend actually returned.
_GATE_LABELS = {
    EvidenceGateStatus.READY_FOR_SCOPED_GENERATION: "Ready for scoped generation",
    EvidenceGateStatus.READY_FOR_LIMITED_BRIEF: "Ready for a limited brief (evidence gaps present)",
    EvidenceGateStatus.BLOCKED: "Blocked — generation not attempted",
}
_GENERATION_LABELS = {
    DisputeGenerationStatus.NOT_ATTEMPTED: "Not attempted",
    DisputeGenerationStatus.DRAFTED: "Draft generated",
    DisputeGenerationStatus.FAILED: "Generation failed",
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
        SUPPLIED_BY_KEY: _SUPPLIED_BY_PROVIDER_LABEL,
        AUTH_REFERENCE_KEY: "",
        MEMBER_ID_KEY: "",
        SERVICE_CODE_KEY: "",
        START_DATE_KEY: "",
        END_DATE_KEY: "",
        SERVICING_PROVIDER_KEY: "",
        EXPLANATION_KEY: "",
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
    """Clear any previous judge envelope/binding. Touches only
    dispute_review_-prefixed keys."""
    state[JUDGE_ENVELOPE_KEY] = None
    state[JUDGE_BINDING_KEY] = None


def clear_result(state: MutableMapping) -> None:
    """Clear any previous comparison result, AI workflow result, and judge
    result -- plus their fingerprints/bindings and validation errors.
    Called whenever ANY dispute-review input changes, and whenever a
    preset is loaded or inputs are reset (Module 6C Step 6: a change that
    invalidates the comparison must also invalidate any AI brief and judge
    result derived from it). Touches only dispute_review_-prefixed keys.
    """
    state[RESULT_KEY] = None
    state[RESULT_FINGERPRINT_KEY] = None
    state[VALIDATION_ERRORS_KEY] = []
    state[WORKFLOW_RESULT_KEY] = None
    state[WORKFLOW_FINGERPRINT_KEY] = None
    clear_judge(state)


def _submission_to_widget_values(submission: DisputeSubmission) -> dict[str, str]:
    return {
        SUPPLIED_BY_KEY: (
            _SUPPLIED_BY_PROVIDER_LABEL
            if submission.supplied_by == SubmissionSource.PROVIDER
            else _SUPPLIED_BY_PATIENT_LABEL
        ),
        AUTH_REFERENCE_KEY: submission.authorization_reference_number or "",
        MEMBER_ID_KEY: submission.member_id or "",
        SERVICE_CODE_KEY: submission.service_code or "",
        START_DATE_KEY: submission.authorization_start_date.isoformat() if submission.authorization_start_date else "",
        END_DATE_KEY: submission.authorization_end_date.isoformat() if submission.authorization_end_date else "",
        SERVICING_PROVIDER_KEY: submission.servicing_provider_id or "",
        EXPLANATION_KEY: submission.dispute_explanation or "",
    }


def apply_preset(state: MutableMapping, submission: DisputeSubmission) -> None:
    """Load a preset's field values into the editable widget state and
    clear any stale result. Must be called from a button's on_click
    callback -- never after those widgets have already been instantiated
    in the current run."""
    for key, value in _submission_to_widget_values(submission).items():
        state[key] = value
    clear_result(state)


def reset_inputs(state: MutableMapping) -> None:
    """The 'Clear / reset' action: restore every dispute-review widget to
    its blank default and clear any stale result."""
    for key, value in _default_widget_values().items():
        state[key] = value
    clear_result(state)


def apply_matching_preset(state: MutableMapping, case_context: CaseContext) -> None:
    if case_context.claim is None:
        return
    presets = build_demo_presets(case_context)
    apply_preset(state, presets.matching)


def apply_different_provider_preset(state: MutableMapping, case_context: CaseContext) -> None:
    if case_context.claim is None:
        return
    presets = build_demo_presets(case_context)
    apply_preset(state, presets.different_servicing_provider)


def apply_incomplete_preset(state: MutableMapping, case_context: CaseContext) -> None:
    if case_context.claim is None:
        return
    presets = build_demo_presets(case_context)
    apply_preset(state, presets.incomplete)


def _current_fingerprint(state: MutableMapping, claim: ClaimSnapshot) -> str:
    """A snapshot of every CURRENTLY COMMITTED widget value plus the claim
    snapshot used, serialized to one comparable string. Reused (not
    duplicated) as the binding fingerprint for BOTH the comparison result
    and the AI workflow result (Module 6C Step 6: "extend them rather than
    creating competing state mechanisms") -- a stored result of either
    kind is only displayed when this fingerprint still matches the one
    captured at the moment that result was computed. Uses raw
    (pre-validation) widget strings, so even an edit that would fail
    validation still changes the fingerprint and hides a stale result.
    """
    widget_part = "\x1f".join(str(state.get(key, "")) for key in INPUT_KEYS)
    return claim.model_dump_json() + "\x1e" + widget_part


def _judge_binding_key(result: DisputeWorkflowResult) -> str:
    """ONE canonical fingerprint binding a judge result to the EXACT
    judge-input revision it was computed from: the workflow's run_id, the
    brief it evaluated, AND the generation context (evidence, deterministic
    comparison summary/findings, and limitations) actually supplied to it.

    Binding to `generation_context` (not `evidence_package`) is
    deliberate: application.dispute_judge.run_dispute_judge builds its
    prompt from exactly `result.generation_context` + `result.brief` (see
    prompts/dispute_judge_prompt.py's render_judge_user_prompt) -- that is
    the precise "evidence/context actually supplied to the judge" the
    binding must track, and it already embeds `comparison_summary`, every
    `comparison:*` finding reference, and `limitations` as its own fields,
    so one serialization covers all of "run identifier, brief,
    evidence/context, deterministic comparisons and material limitations"
    without a second, competing fingerprint for each (Module 6D Step 2A).

    run_id is freshly generated per run_dispute_workflow call and nothing
    in this codebase currently mutates a stored DisputeWorkflowResult's
    fields in place, so this binding is redundant with run_id alone under
    TODAY's call paths -- but DisputeWorkflowResult is not itself frozen
    (unlike dispute_review.models' contracts), so a hypothetical future
    bug that replaced evidence_package/generation_context in place while
    preserving run_id and brief is exactly the failure mode this guards
    against. Verified directly by
    test_judge_binding_rejects_evidence_change_with_unchanged_run_id_and_brief.
    """
    brief_json = result.brief.model_dump_json() if result.brief is not None else ""
    context_json = result.generation_context.model_dump_json() if result.generation_context is not None else ""
    return result.run_id + "\x1e" + brief_json + "\x1e" + context_json


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Concise, field-specific messages -- never a raw stack trace."""
    messages = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        label = loc if loc else "submission"
        messages.append(f"{label}: {error['msg']}")
    return messages


def _build_submission_or_errors(state: MutableMapping) -> tuple[Optional[DisputeSubmission], list[str]]:
    supplied_by = (
        SubmissionSource.PROVIDER
        if state.get(SUPPLIED_BY_KEY, _SUPPLIED_BY_PROVIDER_LABEL) == _SUPPLIED_BY_PROVIDER_LABEL
        else SubmissionSource.PATIENT
    )
    try:
        submission = DisputeSubmission(
            authorization_reference_number=state.get(AUTH_REFERENCE_KEY, ""),
            member_id=state.get(MEMBER_ID_KEY, ""),
            service_code=state.get(SERVICE_CODE_KEY, ""),
            authorization_start_date=state.get(START_DATE_KEY, ""),
            authorization_end_date=state.get(END_DATE_KEY, ""),
            servicing_provider_id=state.get(SERVICING_PROVIDER_KEY, ""),
            dispute_explanation=state.get(EXPLANATION_KEY, ""),
            supplied_by=supplied_by,
        )
    except ValidationError as exc:
        return None, _format_validation_errors(exc)
    return submission, []


def run_comparison(state: MutableMapping, claim: ClaimSnapshot) -> None:
    """Handle the 'Compare submitted information' action: build+validate a
    DisputeSubmission from current widget state, and either store a fresh
    DisputeComparisonResult (with its render-guard fingerprint) or a list
    of validation errors -- never both. Calls the real, unmodified
    dispute_review.comparison.compare_submission -- no comparison logic is
    reimplemented here. ZERO model calls -- unchanged from Module 4/5."""
    submission, errors = _build_submission_or_errors(state)
    if errors:
        state[VALIDATION_ERRORS_KEY] = errors
        state[RESULT_KEY] = None
        state[RESULT_FINGERPRINT_KEY] = None
        return
    result = compare_submission(claim, submission)
    state[VALIDATION_ERRORS_KEY] = []
    state[RESULT_KEY] = result
    state[RESULT_FINGERPRINT_KEY] = _current_fingerprint(state, claim)


def get_display_result(state: MutableMapping, claim: ClaimSnapshot) -> Optional[DisputeComparisonResult]:
    """The stored comparison result to render, or None if it is missing or
    stale."""
    result = state.get(RESULT_KEY)
    fingerprint = state.get(RESULT_FINGERPRINT_KEY)
    if result is None or fingerprint is None:
        return None
    if fingerprint != _current_fingerprint(state, claim):
        return None
    return result


def run_investigation(state: MutableMapping, claim: ClaimSnapshot, submission: DisputeSubmission) -> None:
    """Handle the 'Investigate dispute evidence' action: invoke the bounded
    dispute-review AI workflow EXACTLY ONCE, using the CURRENT validated
    submission and CLM-1001. Calls application.dispute_workflow.run_dispute_workflow
    directly -- no workflow/gate/validation logic is reimplemented here,
    and application.investigation_service.run_investigation (the ORIGINAL
    claim-investigation pipeline) is never called from this function or
    anywhere else in this module.

    Clears any previous judge result FIRST (Module 6C Step 6: "a new
    workflow run invalidates the previous judge result even when the
    inputs happen to be identical" -- a fresh run always gets a fresh
    run_id, so the old judge binding would fail its own check regardless,
    but this makes the invalidation immediate and explicit rather than
    relying solely on the render-time binding check).
    """
    clear_judge(state)
    state[WORKFLOW_RESULT_KEY] = None
    state[WORKFLOW_FINGERPRINT_KEY] = None
    result = run_dispute_workflow(claim.claim_id, submission)
    state[WORKFLOW_RESULT_KEY] = result
    state[WORKFLOW_FINGERPRINT_KEY] = _current_fingerprint(state, claim)


def get_display_workflow_result(state: MutableMapping, claim: ClaimSnapshot) -> Optional[DisputeWorkflowResult]:
    """The stored AI workflow result to render, or None if missing or
    stale (its fingerprint no longer matches the currently committed
    widget values / claim) -- same render-guard convention as
    get_display_result."""
    result = state.get(WORKFLOW_RESULT_KEY)
    fingerprint = state.get(WORKFLOW_FINGERPRINT_KEY)
    if result is None or fingerprint is None:
        return None
    if fingerprint != _current_fingerprint(state, claim):
        return None
    return result


def run_judge(state: MutableMapping, workflow_result: DisputeWorkflowResult) -> None:
    """Handle the 'Run AI semantic evaluation' action: invoke the SEPARATE,
    optional dispute judge EXACTLY ONCE against the CURRENT workflow
    result's accepted brief. Calls application.dispute_judge.run_dispute_judge
    directly -- never invoked automatically, never from anywhere else in
    this module. Never mutates workflow_result."""
    envelope = run_dispute_judge(workflow_result)
    state[JUDGE_ENVELOPE_KEY] = envelope
    state[JUDGE_BINDING_KEY] = _judge_binding_key(workflow_result)


def get_display_judge_envelope(
    state: MutableMapping, workflow_result: Optional[DisputeWorkflowResult]
) -> Optional[DisputeJudgeEnvelope]:
    """The stored judge envelope to render, or None if missing, stale
    (run_id no longer matches the current workflow result), or bound to a
    brief revision that no longer matches (see _judge_binding_key)."""
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


def _fmt(value: Optional[str]) -> str:
    return value if value else NOT_PROVIDED_LABEL


def _render_recorded_claim_section(case_context: CaseContext) -> None:
    claim = case_context.claim
    st.subheader("Recorded Claim Information")
    st.caption(
        "RECORDED / SOURCE DATA — not a complete historical adjudication package. "
        f"Claim ID: **{claim.claim_id}**."
    )

    row1 = st.columns(3)
    row1[0].markdown(f"**Claim ID**\n\n{claim.claim_id}")
    row1[1].markdown(f"**Status**\n\n{claim.status}")
    row1[2].markdown(f"**Denial Reason**\n\n{claim.denial_reason_code or '—'}")

    row2 = st.columns(3)
    row2[0].markdown(f"**Member ID**\n\n{claim.member_id}")
    row2[1].markdown(f"**Service Code**\n\n{claim.service_code}")
    row2[2].markdown(f"**Date of Service**\n\n{claim.date_of_service}")

    servicing = case_context.servicing_provider
    if servicing is not None:
        servicing_label = f"{servicing.provider_id} — {servicing.name}"
    elif claim.provider_id:
        servicing_label = f"{claim.provider_id} (not resolvable in the provider dataset)"
    else:
        servicing_label = NOT_PROVIDED_LABEL
    st.markdown(f"**Servicing provider** (used for dispute-review comparison): {servicing_label}")

    ordering = case_context.ordering_provider
    if ordering is not None:
        st.caption(
            f"Ordering provider (context only — never used as a substitute for the "
            f"servicing provider above): {ordering.provider_id} — {ordering.name}"
        )
    elif claim.ordering_provider_id:
        st.caption(
            "Ordering provider (context only — never used as a substitute for the "
            f"servicing provider above): {claim.ordering_provider_id} (not resolvable in the "
            "provider dataset)"
        )


def _render_preset_and_reset_controls(state: MutableMapping, case_context: CaseContext) -> None:
    st.subheader("Synthetic Demonstration Presets")
    st.caption(
        "Each preset below is a SYNTHETIC DEMONSTRATION derived from the recorded claim "
        "above — not a real submission. Loading a preset fills the fields below; it does "
        "not run the comparison automatically."
    )
    col1, col2, col3, col4 = st.columns(4)
    col1.button(
        "Matching fields",
        key=f"{_PREFIX}preset_matching_btn",
        on_click=apply_matching_preset,
        args=(state, case_context),
    )
    col2.button(
        "Different servicing provider",
        key=f"{_PREFIX}preset_diff_provider_btn",
        on_click=apply_different_provider_preset,
        args=(state, case_context),
    )
    col3.button(
        "Incomplete submission",
        key=f"{_PREFIX}preset_incomplete_btn",
        on_click=apply_incomplete_preset,
        args=(state, case_context),
    )
    col4.button(
        "Clear / reset",
        key=f"{_PREFIX}reset_btn",
        on_click=reset_inputs,
        args=(state,),
    )
    st.caption(
        "“Different servicing provider” demonstrates a provider-ID discrepancy "
        "only — it does not establish the alternate facility's ability to perform the "
        "service."
    )


def _render_input_fields(state: MutableMapping) -> None:
    st.subheader("New Authorization Information (Unverified)")
    st.caption(
        "Enter values or load a preset above, then select “Compare submitted "
        "information”. Changing any field below clears a previously displayed "
        "comparison result, AI investigation brief, and judge evaluation once the edit is "
        "committed (Enter or clicking away) — edits are not invalidated per keystroke while "
        "a field is still being typed into."
    )

    def _on_change() -> None:
        clear_result(state)

    # No `index=` here on purpose: SUPPLIED_BY_KEY's value is always
    # already present in session_state (ensure_initialized/apply_preset/
    # reset_inputs all assign it directly, via the Session State API, not
    # via this widget) -- passing `index` as well would conflict with that
    # assignment and trigger Streamlit's "widget created with a default
    # value but also had its value set via the Session State API" warning.
    st.radio(
        "Supplied by",
        options=_SUPPLIED_BY_OPTIONS,
        key=SUPPLIED_BY_KEY,
        horizontal=True,
        on_change=_on_change,
    )
    provenance_label = (
        "Provider-supplied—unverified"
        if state.get(SUPPLIED_BY_KEY, _SUPPLIED_BY_PROVIDER_LABEL) == _SUPPLIED_BY_PROVIDER_LABEL
        else "Patient-supplied—unverified"
    )
    st.caption(f"Current provenance label: **{provenance_label}**")

    col1, col2 = st.columns(2)
    col1.text_input("Authorization reference number", key=AUTH_REFERENCE_KEY, on_change=_on_change)
    col2.text_input("Member ID", key=MEMBER_ID_KEY, on_change=_on_change)

    col3, col4 = st.columns(2)
    col3.text_input("Service code", key=SERVICE_CODE_KEY, on_change=_on_change)
    col4.text_input("Servicing provider ID", key=SERVICING_PROVIDER_KEY, on_change=_on_change)

    col5, col6 = st.columns(2)
    col5.text_input("Authorization start date (YYYY-MM-DD)", key=START_DATE_KEY, on_change=_on_change)
    col6.text_input("Authorization end date (YYYY-MM-DD)", key=END_DATE_KEY, on_change=_on_change)

    st.text_area("Dispute explanation", key=EXPLANATION_KEY, on_change=_on_change, height=80)


def _render_comparison_table(result: DisputeComparisonResult) -> None:
    rows = [
        {
            "Field": row.field,
            "Recorded Claim": _fmt(row.claim_value),
            "Submitted Information": _fmt(row.submitted_value),
            "Status": row.status.value,
            "Explanation": row.explanation,
        }
        for row in result.rows
    ]
    st.dataframe(rows, hide_index=True, width="stretch")


def _render_comparison_result(result: DisputeComparisonResult) -> None:
    st.subheader("Comparison Result")
    st.caption(f"Submission provenance: **{result.submission_provenance}**")

    _render_comparison_table(result)

    st.markdown("**Summary**")
    st.write(result.summary)

    if result.verification_guidance:
        st.markdown("**Verification Guidance**")
        for item in result.verification_guidance:
            st.write(f"- {item}")

    st.warning(result.authenticity_disclaimer)
    st.info(
        "The recorded denial on this claim has not changed. This deterministic comparison is "
        "session-local and is not saved to any claim system. The ORIGINAL AI investigation "
        "draft (Predefined Claims tab) is a separate process and does not include this "
        "submission. Optionally investigate below to draft a NEW, separate dispute brief that "
        "DOES incorporate this unverified submission."
    )

    if result.authorization_reference_number or result.dispute_explanation:
        with st.expander("Submission context (not verified)"):
            if result.authorization_reference_number:
                st.write(f"Authorization reference number: {result.authorization_reference_number}")
            if result.dispute_explanation:
                st.write("Dispute explanation:")
                # st.write renders plain text/Markdown-escaped content, not raw HTML --
                # explanation text is untrusted display data and is never rendered via
                # st.markdown(..., unsafe_allow_html=True) or any HTML/script execution path.
                st.text(result.dispute_explanation)


# --- Module 6C: AI investigation + judge rendering ----------------------------------------


def _render_investigation_controls(state: MutableMapping, claim: ClaimSnapshot, submission: DisputeSubmission) -> None:
    st.subheader("Optional: AI-Assisted Dispute Investigation")
    st.caption(
        "Retrieve recorded facts, policy passages, and graph relationships, then draft a "
        "cited review brief."
    )
    if st.button("Investigate dispute evidence", key=f"{_PREFIX}investigate_btn"):
        with st.spinner("Retrieving recorded facts, policy passages, and graph relationships, "
                         "then drafting a cited review brief…"):
            run_investigation(state, claim, submission)


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
    st.markdown("**Workflow Status**")
    cols = st.columns(3)
    cols[0].metric("Evidence Gate", _GATE_LABELS.get(result.gate_result.status, result.gate_result.status.value))
    cols[1].metric("Generation", _GENERATION_LABELS.get(result.generation_status, result.generation_status.value))
    cols[2].metric(
        "Deterministic Validation",
        _VALIDATION_LABELS.get(result.validation_result.status, result.validation_result.status.value),
    )
    if result.gate_result.reasons:
        for reason in result.gate_result.reasons:
            st.caption(f"Gate note: {reason}")

    with st.expander("Exact status details (technical)"):
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
    """Always shown when present, regardless of gate status -- including
    the provider-change policy gap when it applies (Module 6C Step 3's
    explicit instruction: display evidence limitations prominently
    regardless of a READY gate status)."""
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
    package = result.evidence_package
    if package is None:
        return
    with st.expander("Evidence used for this investigation"):
        st.caption(
            "Grouped, human-readable evidence -- not raw JSON. Each item's reference id is "
            "shown so it can be traced back to a citation in the brief below."
        )

        def _render_group(title: str, refs: list[EvidenceReference]) -> None:
            if not refs:
                return
            st.markdown(f"**{title}**")
            for ref in refs:
                score_note = f" (score={ref.score:.4f})" if ref.score is not None else ""
                st.write(f"`{ref.ref_id}`{score_note} — {ref.label}: {ref.detail}")

        _render_group("Recorded Structured Facts", package.recorded_facts)
        _render_group("Unverified Submitted Information", package.submitted_fields)
        _render_group("Retrieved Policy Excerpts", package.policy_passages)
        _render_group("Recorded Graph Relationships", package.graph_relationships)
        _render_group("Comparison Findings", package.comparison_findings)

        if package.limitations:
            st.markdown("**Limitations**")
            for item in package.limitations:
                st.write(f"- {item}")


def _render_brief(result: DisputeWorkflowResult) -> None:
    brief = result.brief
    if brief is None:
        return
    lookup = _ref_lookup(result)

    st.markdown("### AI Dispute Review Brief")
    st.caption(
        f"Submission provenance: **{result.comparison_result.submission_provenance if result.comparison_result else 'unknown'}**"
    )

    st.markdown("**Summary**")
    st.write(brief.summary)

    st.markdown("**Findings**")
    for finding in brief.findings:
        st.write(f"- {finding.statement}")
        _render_cited_refs(finding.evidence_refs, lookup)

    if brief.missing_or_conflicting_evidence:
        st.markdown("**Missing / Conflicting Evidence**")
        for item in brief.missing_or_conflicting_evidence:
            st.write(f"- {item}")

    if brief.verification_questions:
        st.markdown("**Verification Questions**")
        for item in brief.verification_questions:
            st.write(f"- {item}")

    st.markdown("**Suggested Next Step**")
    step = brief.suggested_next_step
    st.write(f"**{step.action_code.value}** — {step.rationale}")
    _render_cited_refs(step.evidence_refs, lookup)

    if result.authenticity_disclaimer:
        st.warning(result.authenticity_disclaimer)
    st.info(result.provenance_notice)
    st.info(
        "The recorded denial on this claim has not changed. This dispute brief DOES "
        "incorporate the unverified submission above. The ORIGINAL AI investigation draft "
        "(Predefined Claims tab) remains a completely separate process and still does not "
        "include this submission. This brief is session-local and is not saved to any claim "
        "system."
    )


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
    st.divider()
    _render_workflow_status(result)
    _render_source_outcomes(result)
    _render_limitations(result)

    if result.gate_result.status == EvidenceGateStatus.BLOCKED:
        st.error(
            "Evidence was insufficient to attempt AI generation. The deterministic comparison "
            "and any recorded facts found are shown above; no model call was made."
        )
        _render_evidence_expander(result)
        return

    if is_accepted_dispute_draft(result):
        _render_evidence_expander(result)
        _render_brief(result)
        _render_judge_section(state, result)
    else:
        _render_generation_or_validation_failure(result)
        _render_evidence_expander(result)


def _render_judge_section(state: MutableMapping, workflow_result: DisputeWorkflowResult) -> None:
    with st.expander("Optional: AI Semantic Evaluation (Judge)"):
        st.caption(
            "An optional second model call assesses the draft against its evidence. Scores "
            "are advisory and uncalibrated."
        )
        if st.button("Run AI semantic evaluation", key=f"{_PREFIX}judge_btn"):
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
                ("Uncertainty & Provenance", judged.uncertainty_and_provenance),
                ("Authority Boundaries", judged.authority_boundaries),
            )
        ]
        st.dataframe(rows, hide_index=True, width="stretch")

        for label, dim in (
            ("Evidence Grounding", judged.evidence_grounding),
            ("Coverage", judged.coverage),
            ("Uncertainty & Provenance", judged.uncertainty_and_provenance),
            ("Authority Boundaries", judged.authority_boundaries),
        ):
            if dim.evidence_refs or dim.cited_draft_text:
                st.caption(f"{label} — supporting references:")
                _render_cited_refs(dim.evidence_refs, lookup)
                for text in dim.cited_draft_text:
                    st.caption(f"↳ draft text cited: “{text}”")

        overall_col1, overall_col2 = st.columns(2)
        overall_col1.metric("Overall Advisory Score", f"{judged.overall_score:.2f} / 5")
        overall_col2.metric("Overall Verdict", _VERDICT_LABELS.get(judged.overall_result, judged.overall_result.value))
        st.caption(
            "This average is computed in Python from the four dimension scores above, never by "
            "the model, and never overrides a FAIL/UNCERTAIN dimension shown above. It is an "
            "advisory, uncalibrated rubric score — never an accuracy percentage, a confidence "
            "probability, or proof the draft is verified."
        )

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


def render_dispute_review_tab(state: MutableMapping) -> None:
    """Top-level render function for the Dispute Review tab, called once
    per script run from app.py. Cheap deterministic lookups
    (get_case_context) run unconditionally on every rerun, matching the
    existing Predefined Claims tab's own convention -- but comparison
    (compare_submission), AI investigation (run_dispute_workflow), and
    judging (run_dispute_judge) only ever run inside a button's click
    handler, never merely because this function itself re-executes on
    every rerun.
    """
    ensure_initialized(state)

    st.caption(
        "Compare newly supplied authorization information with the recorded claim and "
        "identify what requires verification."
    )

    case_context = get_case_context(CLAIM_ID)
    if case_context.claim is None:
        st.error(
            f"No claim record found for {CLAIM_ID!r}. Dispute Review is unavailable for "
            "this claim."
        )
        return

    claim_snapshot = build_claim_snapshot(case_context)

    _render_recorded_claim_section(case_context)
    st.divider()
    _render_preset_and_reset_controls(state, case_context)
    st.divider()
    _render_input_fields(state)

    st.divider()
    if st.button("Compare submitted information", type="primary", key=f"{_PREFIX}compare_btn"):
        run_comparison(state, claim_snapshot)

    errors = state.get(VALIDATION_ERRORS_KEY) or []
    if errors:
        st.error("The submission could not be validated:")
        for message in errors:
            st.write(f"- {message}")

    result = get_display_result(state, claim_snapshot)
    if result is not None:
        st.divider()
        _render_comparison_result(result)

        submission, submission_errors = _build_submission_or_errors(state)
        if submission is not None and not submission_errors:
            st.divider()
            _render_investigation_controls(state, claim_snapshot, submission)

            workflow_result = get_display_workflow_result(state, claim_snapshot)
            if workflow_result is not None:
                _render_investigation_result(state, workflow_result)
