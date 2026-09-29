"""Claim Dispute Review Copilot -- interview navigation (Module 5).

A synthetic-data prototype demonstrating the evidence-first investigation
pipeline built in H0-H2 and extended through Module 4. This file is
intentionally a thin rendering script: all decision logic (case defaults,
stale-state resets, review-decision recording, which AssembledContext to
display, Scenario Lab construction/validation/in-memory overlay, golden
reference loading/evaluation) lives in application/workbench.py,
application/scenario_lab.py, and evals/golden_reference_*.py, all
unit-tested without a running Streamlit session. This file never calls the
OpenAI adapter directly -- it calls
application.investigation_service.run_investigation exactly once per
"Investigate Claim" click, and never re-derives evidence sufficiency,
retrieval, or the agent's routing.

Module 5 navigation: exactly two interview tabs, in this order --
"Dispute Review" (dispute_review/ui.py) and "Golden Dataset & Evaluation"
(_render_golden_evaluation_tab below, replacing the old "Predefined Claims"
tab and layering golden-reference expected results + a deterministic
evaluator over the SAME run_investigation/workbench components). Each tab
uses a completely separate, non-overlapping session-state key namespace
(application/workbench.py's Predefined-Claims-derived keys, reused as-is
by the Golden Dataset & Evaluation tab, plus its own `golden_eval_`-prefixed
evaluation-result key; dispute_review/ui.py's dispute_review_*-prefixed
keys) -- no tab can read or clear another tab's state.

Scenario Lab (H7, application/scenario_lab.py + _render_scenario_lab_tab
below) is HIDDEN from this default navigation but not deleted: its
implementation and tests (tests/test_scenario_lab.py,
tests/test_app_scenario_lab_ui.py) are unchanged, and
_render_scenario_lab_tab is simply never invoked unless the
SHOW_SCENARIO_LAB_TAB=1 environment variable is set (dev/test use only --
see the module-level _SHOW_SCENARIO_LAB_TAB flag below). Neither Golden
Dataset & Evaluation nor Dispute Review ever mutates the shared
DataStore/claims/authorizations/graph.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import streamlit as st

from agents.state import AgentStatus
from application import scenario_lab, workbench
from application.investigation_service import run_investigation
from application.judge_models import JudgeStatus
from application.models import GenerationStatus, ValidationStatus
from application.review_packet import build_needs_review_packet
from application.scenario_lab import (
    AuthorizationDraft,
    MemberMode,
    ProviderMode,
    ScenarioBlockedError,
    ValidationLevel,
)
from application.semantic_judge import run_semantic_judge
from dispute_review.ui import render_dispute_review_tab
from evals.golden_reference_evaluator import (
    CheckOutcome,
    evaluate_investigation_result,
    is_evaluation_still_fresh,
)
from evals.golden_reference_models import (
    GoldenReferenceLoadError,
    load_golden_reference_set,
)
from tools.case_context import get_case_context

# Module 5: Scenario Lab is hidden from the primary interview navigation but
# its implementation (application/scenario_lab.py, _render_scenario_lab_tab
# below) and its tests (tests/test_scenario_lab.py,
# tests/test_app_scenario_lab_ui.py) are unchanged and still fully exercised
# -- tests/test_app_scenario_lab_ui.py sets this env var before instantiating
# AppTest to bring the tab back for its own regression coverage. Default
# (interview) navigation never sets it, so _render_scenario_lab_tab() and
# every application.scenario_lab.* function it calls are simply never
# invoked -- see tests/test_app_navigation.py for a direct assertion of that.
_SHOW_SCENARIO_LAB_TAB = os.environ.get("SHOW_SCENARIO_LAB_TAB") == "1"

st.set_page_config(page_title="Claim Dispute Review Copilot", layout="wide")

st.title("Claim Dispute Review Copilot")
st.caption("Evidence-first investigation support for claims operations")
st.caption(
    "Synthetic-data prototype for interview/portfolio purposes only. No real PHI, member "
    "data, or proprietary Humana data is used anywhere in this app."
)

# Cosmetic only: makes the two interview tabs visually prominent (larger,
# bold, color-coded). Static CSS, no interpolated/user-derived text --
# never affects which tab is selected, tab order, or any tab's label text
# (see tests/test_app_navigation.py's exact-label assertion, unaffected by
# this styling). Selector confirmed against the installed Streamlit
# version's actual DOM (div[data-testid="stTab"], not the older
# [data-baseweb="tab"] markup): nth-of-type(1)/(2) target Dispute Review /
# Golden Dataset & Evaluation specifically -- always the first two tabs
# regardless of whether Scenario Lab (SHOW_SCENARIO_LAB_TAB=1) adds a third.
st.markdown(
    """
    <style>
    div[data-testid="stTab"]:nth-of-type(1),
    div[data-testid="stTab"]:nth-of-type(2) {
        border-radius: 8px 8px 0 0;
        margin-right: 6px;
        padding: 10px 18px;
    }
    div[data-testid="stTab"]:nth-of-type(1) {
        background-color: #E3EDFB;
    }
    div[data-testid="stTab"]:nth-of-type(2) {
        background-color: #E1F3EA;
    }
    div[data-testid="stTab"]:nth-of-type(1) p,
    div[data-testid="stTab"]:nth-of-type(2) p {
        font-size: 20px;
        font-weight: 700;
        color: #14213D;
    }
    /* Streamlit's default theme colors hover/selected tab text with its red
       accent color -- overridden here to blue for these two tabs specifically. */
    div[data-testid="stTab"]:nth-of-type(1):hover p,
    div[data-testid="stTab"]:nth-of-type(2):hover p,
    div[data-testid="stTab"]:nth-of-type(1)[aria-selected="true"] p,
    div[data-testid="stTab"]:nth-of-type(2)[aria-selected="true"] p {
        color: #1A56DB !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =====================================================================================
# Guardrails catalog (documentation artifact only -- see docs/GUARDRAILS.md)
#
# This is a STATIC, read-only presentation structure: plain strings naming
# and locating each guardrail. It reimplements no logic and enforces
# nothing -- it exists purely so a demo/interview audience can see the
# guardrail posture in one place without moving, duplicating, or
# reimplementing a single rule. Each guardrail remains owned and enforced
# exactly where docs/GUARDRAILS.md says it lives; this table is not
# read by, and has no effect on, any decision this app makes.
# =====================================================================================

_GUARDRAIL_CATALOG: list[dict[str, str]] = [
    {"name": "Evidence sufficiency gate", "stage": "Evidence -> Gate", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Zero-model-call path for insufficient evidence", "stage": "Gate -> LLM boundary", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Bounded / budgeted context assembly", "stage": "Context Assembly", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Structured InvestigationBrief output", "stage": "LLM -> Typed Brief", "type": "Deterministic (schema)", "blocking": "Blocking"},
    {"name": "Evidence-reference existence validation", "stage": "Deterministic Validation", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Finding evidence-reference requirement", "stage": "Deterministic Validation", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Advisory action allowlist", "stage": "Deterministic Validation", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Prohibited authority-language validation", "stage": "Deterministic Validation", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Configuration/provider/timeout/parsing failure handling", "stage": "LLM call boundary", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Separation of EvidenceStatus / GenerationStatus / ValidationStatus", "stage": "Cross-cutting", "type": "Deterministic (type system)", "blocking": "Structural"},
    {"name": "Human review controls", "stage": "Human Review", "type": "Human", "blocking": "Advisory"},
    {"name": "Stale-state protection", "stage": "UI (cross-cutting)", "type": "Deterministic", "blocking": "Blocking"},
    {"name": "Optional H6/H8 semantic judge", "stage": "Optional Semantic Evaluation", "type": "LLM-based", "blocking": "Advisory only"},
    {"name": "Semantic-judge score/verdict coherence validation", "stage": "Optional Semantic Evaluation", "type": "Deterministic", "blocking": "Blocking (judge output only)"},
    {"name": "Scenario Lab temporary-data isolation", "stage": "Scenario Lab (H7)", "type": "Deterministic", "blocking": "Structural"},
    {"name": "Baseline-data non-mutation", "stage": "Scenario Lab (H7)", "type": "Deterministic + tested", "blocking": "Structural"},
]


def _render_guardrails_expander() -> None:
    # Called at the END of the page (see bottom of this file) -- this is
    # documentation-only, read-only content with no bearing on either tab's
    # behavior, so it never needs to be the first thing a demo viewer sees.
    with st.expander("Safety & Guardrails"):
        st.caption(
            "A read-only catalog of guardrails already implemented in this prototype. This "
            "table is documentation only -- it does not enforce, toggle, or affect anything; "
            "each guardrail remains owned and enforced exactly where it already lives. Full "
            "detail (implementation file, trigger, failure behavior) is in "
            "docs/GUARDRAILS.md."
        )
        st.dataframe(
            _GUARDRAIL_CATALOG,
            hide_index=True,
            width="stretch",
            column_config={
                "name": "Guardrail",
                "stage": "Pipeline Stage",
                "type": "Type",
                "blocking": "Blocking / Advisory",
            },
        )
        st.caption(
            "The semantic judge is advisory only and not calibrated as a production quality "
            "gate. The deterministic validator is not a universal hallucination detector. "
            "Policy Section Recall remains 63.33% -- retrieved policy evidence is not "
            "exhaustive. No guardrail here proves a claim denial was correct, and nothing in "
            "this system autonomously adjudicates a claim."
        )


# =====================================================================================
# Shared result-rendering helpers (reused by BOTH tabs)
# =====================================================================================


def _render_status_summary(result) -> None:
    """Scenario Lab's original three-metric display -- UNCHANGED, see
    tests/test_app_scenario_lab_ui.py's exact st.metric-label assertion.
    Golden Dataset & Evaluation no longer calls this at all (user feedback,
    2026-09-26): Evidence/Generation/Validation status was redundant with
    which branch of Investigation Result renders below it, and once
    evaluated, with the Result vs. Expected Result comparison table's own
    findings."""
    cols = st.columns(3)
    cols[0].metric("Evidence Status", result.agent_result.status.value)
    cols[1].metric("Generation Status", result.generation_status.value)
    cols[2].metric("Validation Status", result.validation_result.status.value)


def _render_brief(brief, *, findings_in_expander: bool = False, combine_summary_and_next_step: bool = False) -> None:
    """`findings_in_expander=True` (Golden Dataset & Evaluation only) also
    pulls Missing / Conflicting Evidence inside the same "Findings"
    expander -- it's evidence-quality detail about the findings, not a
    separate topic (user feedback, 2026-09-26). Scenario Lab
    (findings_in_expander=False) renders identically to before: Findings
    as a plain bullet list, then a separate "Missing / Conflicting
    Evidence" heading, both outside any expander.

    `combine_summary_and_next_step=True` (Golden Dataset & Evaluation only,
    user request 2026-09-27) renders the Summary and Suggested Next Step
    together in ONE green st.success box instead of two separate plain-text
    blocks; Scenario Lab keeps the original two separate plain blocks."""
    step = brief.suggested_next_step

    if combine_summary_and_next_step:
        st.markdown("**Summary**")
        st.success(f"{brief.summary}\n\n**Suggested Next Step: {step.action_code.value}** — {step.rationale}")
        if step.evidence_refs:
            st.caption("Supporting evidence: " + ", ".join(step.evidence_refs))
    else:
        st.markdown("**Summary**")
        st.write(brief.summary)

    def _findings_and_missing_body() -> None:
        for finding in brief.findings:
            st.write(f"- {finding.statement}")
            if finding.evidence_refs:
                st.caption("Supporting evidence: " + ", ".join(finding.evidence_refs))
        if brief.missing_or_conflicting_evidence:
            st.markdown("**Missing / Conflicting Evidence**")
            for item in brief.missing_or_conflicting_evidence:
                st.write(f"- {item}")

    if findings_in_expander:
        with st.expander("Findings"):
            _findings_and_missing_body()
    else:
        st.markdown("**Findings**")
        _findings_and_missing_body()

    if not combine_summary_and_next_step:
        st.markdown("**Suggested Next Step**")
        st.write(f"**{step.action_code.value}** — {step.rationale}")
        if step.evidence_refs:
            st.caption("Supporting evidence: " + ", ".join(step.evidence_refs))


_EVIDENCE_KIND_LABELS = {
    "claim": "Claim",
    "member": "Member",
    "plan": "Plan",
    "benefit": "Benefit",
    "authorization": "Authorization",
    "servicing_provider": "Servicing Provider",
    "ordering_provider": "Ordering Provider",
    "policy": "Policy",
    "graph_relationship": "Graph Relationships",
}


def _render_supporting_evidence(context) -> None:
    if context is None:
        st.info("No evidence package is available for this request.")
        return

    grouped: dict[str, list] = {}
    for ref in context.references:
        grouped.setdefault(ref.kind, []).append(ref)

    for kind, label in _EVIDENCE_KIND_LABELS.items():
        refs = grouped.get(kind)
        if not refs:
            continue
        st.markdown(f"**{label}**")
        if kind == "authorization" and len(refs) > 0:
            st.caption(
                "The system does not deterministically determine which authorization "
                "record, if any, applies to this claim -- every candidate is shown."
            )
        for ref in refs:
            st.write(f"`{ref.ref_id}` — {ref.detail}")

    if context.missing_information:
        st.markdown("**Missing Information (flagged by the evidence layer)**")
        for item in context.missing_information:
            st.write(f"- {item}")

    with st.expander("Raw evidence context (debug)"):
        st.json(context.model_dump(mode="json"))


def _render_execution_trace(result) -> None:
    st.markdown(
        "**Agent workflow steps executed** (fixed sequence -- the main graph always "
        "invokes the `investigate_claim` skill; nothing here was dynamically chosen):"
    )
    for step in result.agent_result.actions_taken:
        st.write(f"- {step}")

    st.markdown("**Generation**")
    if result.generation_status == GenerationStatus.NOT_ATTEMPTED:
        st.write("Not attempted — evidence status did not reach EVIDENCE_SUFFICIENT.")
    elif result.generation_status == GenerationStatus.DRAFTED:
        meta = result.generation_metadata
        st.write(
            f"Attempted and succeeded. Adapter: `{meta.adapter}`, model: `{meta.model}`, "
            f"prompt version: `{meta.prompt_version}`."
        )
    else:
        category = result.generation_failure_category.value if result.generation_failure_category else "unknown"
        st.write(f"Attempted and failed. Failure category: `{category}`.")

    st.markdown("**Validation**")
    if result.validation_result.status == ValidationStatus.NOT_RUN:
        st.write("Not run — no draft was generated to validate.")
    else:
        st.write(
            f"Run. Result: `{result.validation_result.status.value}` "
            f"({len(result.validation_result.issues)} issue(s))."
        )


def _render_human_review_actions(result, *, review_key: str) -> None:
    """`review_key` namespaces the recorded decision in st.session_state --
    Predefined Claims and Scenario Lab use different keys so neither tab's
    review decision can leak into the other."""
    if result.agent_result.status in (AgentStatus.ERROR, AgentStatus.MAX_STEPS_EXCEEDED):
        return

    show_accept = workbench.is_accepted_draft(result)

    st.markdown("**Human Review**")
    accept_clicked = False
    if show_accept:
        col1, col2 = st.columns(2)
        accept_clicked = col1.button("Accept Investigation Draft", key=f"{review_key}_accept_btn")
        mark_clicked = col2.button("Mark for Further Review", key=f"{review_key}_mark_btn")
    else:
        mark_clicked = st.button("Mark for Further Review", key=f"{review_key}_mark_btn_alt")

    if accept_clicked:
        entry = workbench.record_review_decision(
            st.session_state, result.claim_id, workbench.REVIEW_ACCEPTED, key=review_key
        )
        st.success(
            f"Recorded locally: investigation draft accepted for {entry['claim_id']} at "
            f"{entry['recorded_at']}. This is a local prototype record only — no external "
            "system was contacted, and no claim, authorization, or payment action was taken."
        )
    if mark_clicked:
        entry = workbench.record_review_decision(
            st.session_state, result.claim_id, workbench.REVIEW_MARKED_FOR_REVIEW, key=review_key
        )
        st.warning(
            f"Recorded locally: marked for further review for {entry['claim_id']} at "
            f"{entry['recorded_at']}. This is a local prototype record only — no external "
            "system was contacted."
        )

    decision = st.session_state.get(review_key)
    if decision and decision.get("claim_id") == result.claim_id:
        st.caption(f"Current local review record: {decision['decision']} at {decision['recorded_at']}.")


def _render_semantic_judge_section(
    result,
    *,
    judge_key: str,
    title: str = "Experimental Semantic Evaluation",
    run_button_label: str = "Run AI Semantic Evaluation",
) -> None:
    """H6 (OPTIONAL evaluation sidecar), extended by H8 with a 1-5 rubric
    score per dimension (never displayed or labeled as confidence). Only
    rendered for an already-accepted-eligible draft (DRAFTED + PASSED) --
    never for Case 5/NEEDS_REVIEW, never automatically invoked. `judge_key`
    namespaces the stored envelope the same way `review_key` namespaces
    review decisions. `title` and `run_button_label` default to the
    original expander title and button label (Scenario Lab keeps both
    defaults, unchanged); Golden Dataset & Evaluation passes "Evals: LLM as
    a Judge" for both, for a clearer, less "experimental-sounding" label
    that also matches Dispute Review's own "Evals: LLM-as-a-Judge" naming
    (user request, 2026-09-27)."""
    with st.expander(title):
        st.caption(
            "Experimental evaluator — not used for workflow decisions and not yet calibrated "
            "as a production quality gate."
        )
        if st.button(run_button_label, key=f"{judge_key}_run_btn"):
            with st.spinner("Running experimental semantic evaluation…"):
                envelope = run_semantic_judge(result)
            st.session_state[judge_key] = envelope

        envelope = st.session_state.get(judge_key)
        if envelope is None or envelope.run_id != result.run_id:
            return  # no judge result yet, or it belongs to a prior/stale run

        if envelope.judge_status == JudgeStatus.FAILED:
            category = envelope.judge_failure_category.value if envelope.judge_failure_category else "unknown"
            st.error(
                f"Semantic evaluation failed (category: {category}). The investigation brief "
                "above is unaffected and remains fully usable."
            )
            with st.expander("Technical detail"):
                st.write(envelope.error)
            return

        judged = envelope.result
        st.caption("Score guide: 1 = serious issue · 2 = major issue · 3 = mixed / material weakness · 4 = good · 5 = strong")
        cols = st.columns(4)
        dimensions = [
            ("Factual Grounding", judged.factual_grounding),
            ("Completeness", judged.completeness),
            ("Uncertainty Preservation", judged.uncertainty_preservation),
            ("Authority Boundary", judged.authority_boundary),
        ]
        for col, (label, dimension) in zip(cols, dimensions):
            col.markdown(f"**{label}**  \n{dimension.score} / 5 — {dimension.verdict.value}")
            col.caption(dimension.rationale)

        overall_col1, overall_col2 = st.columns(2)
        overall_col1.markdown(f"**Overall Semantic Score**  \n{judged.overall_score:.2f} / 5")
        overall_col2.markdown(f"**Overall Verdict**  \n{judged.overall_result.value}")

        st.markdown("**Rationale**")
        st.write(judged.rationale)
        if judged.unsupported_claims:
            st.markdown("**Unsupported Claims**")
            for claim_text in judged.unsupported_claims:
                st.write(f"- {claim_text}")
        if judged.missing_key_points:
            st.markdown("**Missing Key Points**")
            for point in judged.missing_key_points:
                st.write(f"- {point}")


def _render_investigation_result(
    result,
    *,
    review_key: str,
    judge_key: str,
    show_status: bool = True,
    judge_title: str = "Experimental Semantic Evaluation",
    judge_run_button_label: str = "Run AI Semantic Evaluation",
    findings_in_expander: bool = False,
    show_advisory_notice: bool = True,
    evaluation_renderer: Callable[[], None] | None = None,
    evaluation_renderer_first: bool = False,
    combine_summary_and_next_step: bool = False,
) -> None:
    """The status/brief/needs-review/failure branching shared by both
    tabs. Identical logic to what H3-H6 already established for
    Predefined Claims; Scenario Lab reuses it verbatim (every new
    parameter defaults to Scenario Lab's original, unchanged appearance --
    only Golden Dataset & Evaluation passes show_status=False, a custom
    judge_title/judge_run_button_label, findings_in_expander=True,
    show_advisory_notice=False, an evaluation_renderer,
    evaluation_renderer_first=True, and combine_summary_and_next_step=True).

    Rendering order after the branch-specific content: `evaluation_renderer`
    (if given and not already rendered first), then Human Review, then the
    judge section (accepted-draft only), then the Supporting
    Evidence/Execution Trace divider -- in that order, for every branch
    alike (user feedback, 2026-09-26).
    `_render_human_review_actions` is called exactly ONCE, unconditionally,
    relying on its own internal self-guard
    (`agent_status in (ERROR, MAX_STEPS_EXCEEDED): return`) rather than
    being called separately inside each branch -- this produces byte-for-
    byte the same output as the old per-branch calls (verified: Scenario
    Lab never passes evaluation_renderer, so for that tab this call sits in
    exactly the same place it always did). `evaluation_renderer`, when
    given, is called for EVERY branch (NEEDS_REVIEW, ERROR, generation
    FAILED, validation FAILED, and accepted-draft alike), never gated on a
    successful draft: Golden Dataset & Evaluation's "Evaluate Against
    Golden Reference" is a structured-fact comparison independent of
    whether a brief was drafted.

    `evaluation_renderer_first=True` (Golden Dataset & Evaluation only,
    user request 2026-09-27) moves that same call to right after the
    "Investigation Result" subheader, BEFORE the branch-specific content --
    so the "Result vs. Expected Result" table (now merged with qualitative
    findings) renders first, and the brief's Summary/Suggested-Next-Step
    (rendered as one combined green box when combine_summary_and_next_step
    is also True) renders directly below it. Scenario Lab passes neither
    flag, so its evaluation_renderer (always None) and rendering order are
    both unaffected."""
    if show_status:
        st.subheader("Status")
        _render_status_summary(result)

    st.subheader("Investigation Result")

    if evaluation_renderer_first and evaluation_renderer is not None:
        evaluation_renderer()

    agent_status = result.agent_result.status

    if agent_status == AgentStatus.NEEDS_REVIEW:
        st.warning("Insufficient evidence for an AI-generated investigation brief.")
        st.write("Missing / unresolved evidence:")
        for item in result.agent_result.missing_information:
            st.write(f"- {item}")
        packet = build_needs_review_packet(result)
        with st.expander("Local Human-Review Packet (synthetic)"):
            st.caption(
                "Built by reusing skills.escalate_case with the evidence already gathered "
                "above -- no external ticketing/case-management system is contacted "
                f"(external_system_contacted={packet.evidence.get('external_system_contacted')})."
            )
            st.json(packet.evidence)

    elif agent_status in (AgentStatus.ERROR, AgentStatus.MAX_STEPS_EXCEEDED):
        st.error(f"The claim could not be investigated: {result.skip_or_error_reason}")

    elif result.generation_status == GenerationStatus.FAILED:
        st.error("Evidence was assembled successfully, but an investigation draft could not be generated.")
        with st.expander("Technical detail"):
            category = (
                result.generation_failure_category.value if result.generation_failure_category else "unknown"
            )
            st.write(f"Failure category: {category}")
            st.write(result.skip_or_error_reason)

    elif result.generation_status == GenerationStatus.DRAFTED and result.validation_result.status == ValidationStatus.FAILED:
        st.error(
            "The generated draft did not pass validation and is not being presented as "
            "an accepted investigation brief."
        )
        with st.expander("Validation issues (technical detail)"):
            for issue in result.validation_result.issues:
                st.write(f"- **{issue.rule}**: {issue.detail}")

    elif workbench.is_accepted_draft(result):
        _render_brief(
            result.investigation_brief,
            findings_in_expander=findings_in_expander,
            combine_summary_and_next_step=combine_summary_and_next_step,
        )
        if show_advisory_notice:
            st.info(
                "Advisory investigation support only. This prototype does not approve, deny, "
                "reverse, or pay claims."
            )

    if not evaluation_renderer_first and evaluation_renderer is not None:
        evaluation_renderer()

    _render_human_review_actions(result, review_key=review_key)
    if workbench.is_accepted_draft(result):
        _render_semantic_judge_section(
            result, judge_key=judge_key, title=judge_title, run_button_label=judge_run_button_label
        )

    st.divider()
    with st.expander("Supporting Evidence"):
        _render_supporting_evidence(workbench.get_display_context(result))

    with st.expander("Execution Trace"):
        _render_execution_trace(result)


# =====================================================================================
# Tab: Golden Dataset & Evaluation (Module 5, replaces Predefined Claims)
# =====================================================================================

GOLDEN_EVAL_RESULT_KEY = "golden_eval_result"
_AUTO_EVALUATE_PENDING_KEY = "_golden_auto_evaluate_pending"

_CHECK_OUTCOME_LABELS = {
    CheckOutcome.PASSED: "Match",
    CheckOutcome.FAILED: "No Match",
    CheckOutcome.NOT_EVALUATED: "Not Evaluated",
}


def _render_expected_results(reference) -> None:
    """Section B: expected results from the golden reference. Purely
    display -- `reference` is never passed into run_investigation or any
    prompt-building code anywhere in this file; it is only ever read here
    and (separately) by _render_evaluation_section's AFTER-the-fact
    comparison."""
    if reference is None:
        st.warning("No golden reference case exists for this claim.")
        return

    st.caption(reference.scenario_description)

    with st.expander("Required findings"):
        for finding in reference.required_findings:
            st.write(f"- **[{finding.classification.value}]** {finding.statement}")
            supporting = finding.supporting_record_ids + finding.supporting_policy_section_ids
            if supporting:
                st.caption("Supporting: " + ", ".join(supporting))

    with st.expander("Missing information & required uncertainty"):
        if reference.missing_information:
            st.markdown("**Missing information**")
            for item in reference.missing_information:
                st.write(f"- {item}")
        if reference.required_uncertainty_or_qualifications:
            st.markdown("**Required uncertainty / qualifications**")
            for item in reference.required_uncertainty_or_qualifications:
                st.write(f"- {item}")

    with st.expander("Verification / human-review actions"):
        for item in reference.verification_or_human_review_actions:
            st.write(f"- {item}")

    with st.expander("Unsupported / prohibited conclusions"):
        for item in reference.unsupported_or_prohibited_conclusions:
            st.write(f"- {item}")

    if reference.known_ambiguities_or_questions_for_human_review:
        with st.expander("Known ambiguities / questions for human review"):
            for item in reference.known_ambiguities_or_questions_for_human_review:
                st.write(f"- {item}")


def _render_evaluation_section(result, reference) -> None:
    """Section D: evaluate the ACTUAL completed run above against the
    golden reference -- never triggers any additional model call (the
    evaluator in evals/golden_reference_evaluator.py does field comparison
    only). Called from WITHIN _render_investigation_result (as its
    `evaluation_renderer`, with `evaluation_renderer_first=True` for Golden
    Dataset & Evaluation) rather than as its own divider-separated block --
    no separate numbered subheader, no divider -- so this reads as the
    continuation of "run, then check against the golden reference," not a
    new topic (user feedback, 2026-09-26).

    The "Result vs. Expected Result" table now renders FIRST, before the
    investigation brief's own Summary/Findings/Suggested-Next-Step, and
    includes BOTH deterministically-checkable findings and
    qualitative-review findings (e.g. f5/f6) in one table -- there is no
    longer a separate "Qualitative requirements" expander -- and the
    standalone "Overall Evaluation Status: ..." line was removed entirely
    (user request, 2026-09-27) -- `evaluation.overall_status` is still
    computed by the evaluator and still drives `status_reasons` below, it
    is simply no longer rendered as its own labeled line here.

    Runs automatically once per "Investigate Claim" click (the button
    handler sets `_AUTO_EVALUATE_PENDING_KEY`; consumed here via `pop` so
    it fires exactly once), with no separate heading, caption, or
    "Evaluate Run Against Golden Reference" button any more -- that manual
    trigger became unnecessary once evaluation ran automatically on every
    investigation, and was removed entirely (user feedback, 2026-09-26).
    There is now no way to force a re-evaluation without re-running
    "Investigate Claim"."""
    # Consumed unconditionally, even when there's no reference to evaluate
    # against below -- otherwise a pending flag set for a claim with no
    # golden reference would linger in session_state and fire against
    # whatever claim/result happens to be selected on a later render.
    auto_pending = st.session_state.pop(_AUTO_EVALUATE_PENDING_KEY, False)
    if reference is None:
        st.warning("No golden reference case exists for this claim — nothing to evaluate against.")
        return

    just_evaluated = auto_pending
    if just_evaluated:
        st.session_state[GOLDEN_EVAL_RESULT_KEY] = evaluate_investigation_result(result, reference)

    evaluation = st.session_state.get(GOLDEN_EVAL_RESULT_KEY)
    if evaluation is not None and evaluation.is_stale_for(result, reference):
        st.session_state.pop(GOLDEN_EVAL_RESULT_KEY, None)
        evaluation = None
        st.caption(
            "A previous evaluation was cleared because the case, the run, or the reference "
            "itself changed since it was computed."
        )

    # Module 6: a cached evaluation's own source_fingerprint_stale flag is
    # frozen at the moment it was computed -- it does not update itself if
    # the underlying source files change on a LATER render. Skipped only on
    # the render that JUST computed `evaluation` (guaranteed fresh at that
    # instant); every other render of an existing evaluation re-checks live,
    # so this cached result can never keep showing a "fresh" fingerprint
    # after the source data it was checked against has since changed.
    if evaluation is not None and not just_evaluated and not is_evaluation_still_fresh(evaluation, reference):
        st.session_state.pop(GOLDEN_EVAL_RESULT_KEY, None)
        evaluation = None
        st.caption(
            "A previous evaluation was cleared because the underlying source data changed since "
            "it was computed. Click “Evaluate Run Against Golden Reference” again."
        )

    if evaluation is None:
        st.caption("Overall Evaluation Status: Not run.")
        return

    st.markdown("**Result vs. Expected Result**")
    st.caption(
        "A Match confirms one structured field on EvidencePackage/AgentResult — it does NOT "
        "prove the generated brief expressed that finding in its own text. Qualitative "
        "requirements (marked Needs Human Review) are not machine-checkable at all."
    )
    comparison_rows = [
        {
            "Finding": check.finding_id,
            "Statement": check.statement,
            "Expected": "—" if check.expected_value is None else str(check.expected_value),
            "Actual": "—" if check.actual_value is None else str(check.actual_value),
            "Result": _CHECK_OUTCOME_LABELS[check.outcome],
            "Reason": check.reason,
        }
        for check in evaluation.deterministic_results
    ] + [
        {
            "Finding": item.finding_id,
            "Statement": item.statement,
            "Expected": "—",
            "Actual": "—",
            "Result": "Needs Human Review",
            "Reason": f"[{item.category}] Requires human judgment — not machine-checkable.",
        }
        for item in evaluation.qualitative_items
    ]
    st.dataframe(comparison_rows, hide_index=True, width="stretch")

    for reason in evaluation.status_reasons:
        st.caption(f"- {reason}")
    st.write(evaluation.automated_summary)

    if evaluation.source_fingerprint_stale:
        st.error(
            "This reference's source fingerprint no longer matches current source files — it "
            "needs revalidation: " + "; ".join(evaluation.stale_source_files)
        )


def _render_golden_evaluation_tab() -> None:
    st.caption(
        "The five original predefined claims, each paired with an AI-drafted, pending-"
        "human-review golden reference (see docs/V2_GOLDEN_REFERENCE_REVIEW.md)."
    )

    try:
        golden_set = load_golden_reference_set()
    except GoldenReferenceLoadError as exc:
        st.error(f"Could not load the golden reference dataset: {exc}")
        return
    references_by_claim = {case.claim_id: case for case in golden_set.cases}

    if "selected_claim_id" not in st.session_state:
        workbench.on_case_selected(st.session_state, workbench.CASE_IDS[0])

    st.subheader("1. Case Selection")

    def _label(claim_id: str) -> str:
        reference = references_by_claim.get(claim_id)
        if reference is None:
            return claim_id
        return f"{claim_id} — {reference.scenario_title}"

    def _handle_case_change() -> None:
        workbench.on_case_selected(st.session_state, st.session_state["selected_claim_id"])
        st.session_state.pop(GOLDEN_EVAL_RESULT_KEY, None)

    def _handle_question_change() -> None:
        workbench.on_question_changed(st.session_state, st.session_state["question_text"])
        st.session_state.pop(GOLDEN_EVAL_RESULT_KEY, None)

    st.selectbox(
        "Select a case",
        options=workbench.CASE_IDS,
        format_func=_label,
        key="selected_claim_id",
        on_change=_handle_case_change,
    )
    st.text_area("Scoped question", key="question_text", on_change=_handle_question_change, height=80)

    selected_claim_id = st.session_state["selected_claim_id"]
    reference = references_by_claim.get(selected_claim_id)

    st.subheader("2. Recorded Claim Information")
    st.caption("RECORDED / SOURCE DATA — not AI-generated")

    case_context = get_case_context(selected_claim_id)
    if case_context.claim is None:
        st.warning(f"No claim record found for {selected_claim_id!r}.")
    else:
        claim = case_context.claim
        claim_rows = [
            {"Field": "Claim ID", "Value": claim.claim_id},
            {"Field": "Status", "Value": str(claim.status)},
            {"Field": "Service", "Value": str(claim.service_code)},
            {"Field": "Date of Service", "Value": str(claim.date_of_service)},
            {"Field": "Denial Reason Code", "Value": claim.denial_reason_code or "—"},
            {"Field": "Denial Reason", "Value": claim.denial_reason_description or "—"},
        ]
        if case_context.servicing_provider is not None:
            p = case_context.servicing_provider
            claim_rows.append({"Field": "Servicing Provider", "Value": f"{p.name} ({p.provider_id}, {p.network_status})"})
        if case_context.ordering_provider is not None:
            p = case_context.ordering_provider
            claim_rows.append({"Field": "Ordering Provider", "Value": f"{p.name} ({p.provider_id}, {p.network_status})"})
        if case_context.benefit is not None:
            b = case_context.benefit
            claim_rows.append(
                {"Field": "Benefit", "Value": f"covered={b.covered}, requires_prior_auth={b.requires_prior_auth}"}
            )
        st.dataframe(claim_rows, hide_index=True, width="stretch")

    st.subheader("3. Expected Results (Golden Reference)")
    _render_expected_results(reference)

    st.subheader("4. Run Investigation & Evaluation")
    if st.button("Investigate Claim", type="primary"):
        with st.spinner("Assembling evidence, generating an investigation brief, and validating the draft…"):
            result = run_investigation(selected_claim_id, st.session_state["question_text"])
        st.session_state["investigation_result"] = result
        st.session_state.pop("review_decision", None)
        st.session_state.pop("judge_result", None)
        st.session_state.pop(GOLDEN_EVAL_RESULT_KEY, None)
        # Flag that the golden-reference evaluation should also run on this
        # SAME render -- consumed once by _render_evaluation_section, which
        # runs evaluate_investigation_result itself (there is no longer a
        # separate "Evaluate Run Against Golden Reference" button/click) --
        # so the comparison table is already on screen right below the
        # investigation result, with no manual step at all (user feedback,
        # 2026-09-26).
        st.session_state[_AUTO_EVALUATE_PENDING_KEY] = True

    result = st.session_state.get("investigation_result")
    if result is not None and result.claim_id == selected_claim_id:
        st.divider()
        _render_investigation_result(
            result,
            review_key="review_decision",
            judge_key="judge_result",
            show_status=False,
            judge_title="Evals: LLM as a Judge",
            judge_run_button_label="Evals: LLM as a Judge",
            findings_in_expander=True,
            show_advisory_notice=False,
            evaluation_renderer=lambda: _render_evaluation_section(result, reference),
            evaluation_renderer_first=True,
            combine_summary_and_next_step=True,
        )


# =====================================================================================
# Scenario Lab (H7) -- hidden from the default interview navigation as of
# Module 5 (see _SHOW_SCENARIO_LAB_TAB above); this function and
# application/scenario_lab.py are otherwise unchanged.
# =====================================================================================


def _render_scenario_lab_tab() -> None:
    st.caption(
        "Compose temporary synthetic evidence scenarios and run them through the same "
        "investigation pipeline."
    )
    st.info("Scenario Lab data is temporary and does not modify baseline claim data.")
    st.warning(
        "Scenario Lab is a single-user prototype testing surface. It is not designed for "
        "concurrent multi-user execution. Golden Dataset & Evaluation is the primary demo path."
    )

    draft = scenario_lab.get_or_create_draft(st.session_state)

    st.subheader("Scenario Template")
    template_index = (
        scenario_lab.TEMPLATE_NAMES.index(draft.template) if draft.template in scenario_lab.TEMPLATE_NAMES else 0
    )
    selected_template = st.selectbox("Template", options=scenario_lab.TEMPLATE_NAMES, index=template_index)
    if selected_template != draft.template:
        scenario_lab.apply_template_change(st.session_state, selected_template)
        st.rerun()

    def _changed(new_value, old_value) -> bool:
        return new_value != old_value

    def _note_change() -> None:
        scenario_lab.reset_scenario_investigation_state(st.session_state)

    st.subheader("Claim")
    col1, col2, col3 = st.columns(3)
    new_status = col1.selectbox("Claim status", ["DENIED", "PAID"], index=["DENIED", "PAID"].index(draft.claim_status))
    if _changed(new_status, draft.claim_status):
        draft.claim_status = new_status
        _note_change()

    denial_options = [None, "AUTH_REQUIRED", "SERVICE_NOT_COVERED", "OUT_OF_NETWORK_PROVIDER"]
    denial_labels = {None: "(none / N/A)", "AUTH_REQUIRED": "AUTH_REQUIRED", "SERVICE_NOT_COVERED": "SERVICE_NOT_COVERED", "OUT_OF_NETWORK_PROVIDER": "OUT_OF_NETWORK_PROVIDER"}
    new_denial = col2.selectbox(
        "Denial reason code",
        options=denial_options,
        index=denial_options.index(draft.denial_reason_code) if draft.denial_reason_code in denial_options else 0,
        format_func=lambda v: denial_labels[v],
    )
    if _changed(new_denial, draft.denial_reason_code):
        draft.denial_reason_code = new_denial
        _note_change()
    if draft.claim_status == "PAID" and draft.denial_reason_code is not None:
        st.caption("Note: claim status is PAID with a denial reason code set — a structurally valid but unusual combination.")

    new_service_code = col3.text_input("Service code", value=draft.service_code)
    if _changed(new_service_code, draft.service_code):
        draft.service_code = new_service_code
        _note_change()

    col4, col5, col6 = st.columns(3)
    new_dos = col4.date_input("Date of service", value=draft.date_of_service)
    if _changed(new_dos, draft.date_of_service):
        draft.date_of_service = new_dos
        _note_change()
    new_billed = col5.number_input("Billed amount", min_value=0.0, value=float(draft.billed_amount), step=10.0)
    if _changed(new_billed, draft.billed_amount):
        draft.billed_amount = new_billed
        _note_change()
    new_allowed = col6.number_input(
        "Allowed amount (0 = none)", min_value=0.0, value=float(draft.allowed_amount or 0.0), step=10.0
    )
    new_allowed_value = new_allowed if new_allowed > 0 else None
    if _changed(new_allowed_value, draft.allowed_amount):
        draft.allowed_amount = new_allowed_value
        _note_change()

    new_denial_desc = st.text_input("Denial reason description (optional)", value=draft.denial_reason_description)
    if _changed(new_denial_desc, draft.denial_reason_description):
        draft.denial_reason_description = new_denial_desc
        _note_change()

    st.subheader("Member / Plan")
    col7, col8 = st.columns(2)
    plan_ids = scenario_lab.baseline_plan_ids()
    new_plan = col7.selectbox("Plan (baseline)", options=plan_ids, index=plan_ids.index(draft.plan_id) if draft.plan_id in plan_ids else 0)
    if _changed(new_plan, draft.plan_id):
        draft.plan_id = new_plan
        _note_change()

    member_mode_options = [MemberMode.TEMPORARY, MemberMode.EXISTING]
    new_member_mode = col8.selectbox(
        "Member",
        options=member_mode_options,
        index=member_mode_options.index(draft.member_mode),
        format_func=lambda m: "Generate temporary member" if m == MemberMode.TEMPORARY else "Use existing member",
    )
    if _changed(new_member_mode, draft.member_mode):
        draft.member_mode = new_member_mode
        _note_change()
    if draft.member_mode == MemberMode.EXISTING:
        member_ids = scenario_lab.baseline_member_ids()
        current = draft.existing_member_id if draft.existing_member_id in member_ids else member_ids[0]
        new_existing_member = st.selectbox("Existing member_id", options=member_ids, index=member_ids.index(current))
        if _changed(new_existing_member, draft.existing_member_id):
            draft.existing_member_id = new_existing_member
            _note_change()

    st.subheader("Benefit")
    new_benefit_available = st.checkbox("Benefit record available?", value=draft.benefit_available)
    if _changed(new_benefit_available, draft.benefit_available):
        draft.benefit_available = new_benefit_available
        _note_change()
    if draft.benefit_available:
        bcol1, bcol2, bcol3 = st.columns(3)
        new_covered = bcol1.checkbox("Covered", value=draft.benefit_covered)
        if _changed(new_covered, draft.benefit_covered):
            draft.benefit_covered = new_covered
            _note_change()
        new_requires_auth = bcol2.checkbox("Requires prior authorization", value=draft.benefit_requires_prior_auth)
        if _changed(new_requires_auth, draft.benefit_requires_prior_auth):
            draft.benefit_requires_prior_auth = new_requires_auth
            _note_change()
        new_network_req = bcol3.text_input("Network requirement (optional)", value=draft.benefit_network_requirement or "")
        new_network_req_value = new_network_req or None
        if _changed(new_network_req_value, draft.benefit_network_requirement):
            draft.benefit_network_requirement = new_network_req_value
            _note_change()
    else:
        st.caption("No benefit record will be created for this plan/service — resolves to None, not covered=false.")

    def _render_provider_controls(label_prefix: str, mode_field: str, existing_field: str, network_field: str, type_field: str, allow_none: bool):
        options = ([ProviderMode.NONE] if allow_none else []) + [ProviderMode.EXISTING, ProviderMode.TEMPORARY, ProviderMode.UNRESOLVED]
        current_mode = getattr(draft, mode_field)
        mode_labels = {
            ProviderMode.NONE: "None (not on the claim)",
            ProviderMode.EXISTING: "Use existing baseline provider",
            ProviderMode.TEMPORARY: "Generate temporary provider",
            ProviderMode.UNRESOLVED: "Unresolved (references a provider_id that does not exist)",
        }
        pcol1, pcol2 = st.columns(2)
        new_mode = pcol1.selectbox(
            f"{label_prefix} provider mode",
            options=options,
            index=options.index(current_mode) if current_mode in options else 0,
            format_func=lambda m: mode_labels[m],
            key=f"{label_prefix}_mode_select",
        )
        if _changed(new_mode, current_mode):
            setattr(draft, mode_field, new_mode)
            _note_change()
            current_mode = new_mode

        if current_mode == ProviderMode.EXISTING:
            provider_ids = scenario_lab.baseline_provider_ids()
            current_existing = getattr(draft, existing_field)
            current_existing = current_existing if current_existing in provider_ids else provider_ids[0]
            new_existing = pcol2.selectbox(
                f"Existing {label_prefix.lower()} provider_id", options=provider_ids,
                index=provider_ids.index(current_existing), key=f"{label_prefix}_existing_select",
            )
            if _changed(new_existing, getattr(draft, existing_field)):
                setattr(draft, existing_field, new_existing)
                _note_change()
        elif current_mode == ProviderMode.TEMPORARY:
            ncol1, ncol2 = st.columns(2)
            network_options = ["in-network", "out-of-network"]
            current_network = getattr(draft, network_field)
            new_network = ncol1.selectbox(
                f"{label_prefix} network status", options=network_options,
                index=network_options.index(current_network) if current_network in network_options else 0,
                key=f"{label_prefix}_network_select",
            )
            if _changed(new_network, current_network):
                setattr(draft, network_field, new_network)
                _note_change()
            type_options = ["facility", "physician"]
            current_type = getattr(draft, type_field)
            new_type = ncol2.selectbox(
                f"{label_prefix} provider type", options=type_options,
                index=type_options.index(current_type) if current_type in type_options else 0,
                key=f"{label_prefix}_type_select",
            )
            if _changed(new_type, current_type):
                setattr(draft, type_field, new_type)
                _note_change()

    st.subheader("Servicing Provider")
    _render_provider_controls(
        "Servicing", "servicing_provider_mode", "servicing_existing_provider_id",
        "servicing_network_status", "servicing_provider_type", allow_none=False,
    )

    st.subheader("Ordering Provider (optional)")
    _render_provider_controls(
        "Ordering", "ordering_provider_mode", "ordering_existing_provider_id",
        "ordering_network_status", "ordering_provider_type", allow_none=True,
    )

    st.subheader("Prior Authorization Evidence")
    if not draft.authorizations:
        st.caption("No prior-authorization records — represents 'no authorization on file'.")
    for index, auth in enumerate(list(draft.authorizations)):
        with st.expander(f"Authorization #{index + 1}: {auth.status}", expanded=False):
            acol1, acol2, acol3 = st.columns(3)
            status_options = ["APPROVED", "EXPIRED", "DENIED", "PENDING"]
            new_auth_status = acol1.selectbox(
                "Status", options=status_options,
                index=status_options.index(auth.status) if auth.status in status_options else 0,
                key=f"auth_{index}_status",
            )
            if _changed(new_auth_status, auth.status):
                auth.status = new_auth_status
                _note_change()
            new_effective = acol2.date_input("Effective date", value=auth.effective_date, key=f"auth_{index}_eff")
            if _changed(new_effective, auth.effective_date):
                auth.effective_date = new_effective
                _note_change()
            new_expiration = acol3.date_input("Expiration date", value=auth.expiration_date, key=f"auth_{index}_exp")
            if _changed(new_expiration, auth.expiration_date):
                auth.expiration_date = new_expiration
                _note_change()
            new_notes = st.text_input("Notes (optional)", value=auth.notes, key=f"auth_{index}_notes")
            if _changed(new_notes, auth.notes):
                auth.notes = new_notes
                _note_change()
            if st.button("Remove this authorization", key=f"auth_{index}_remove"):
                draft.authorizations.pop(index)
                _note_change()
                st.rerun()

    if st.button("Add Authorization"):
        draft.authorizations.append(AuthorizationDraft())
        _note_change()
        st.rerun()

    st.subheader("Scoped Question")
    new_question = st.text_area("Question", value=draft.question, key="scenario_question_input")
    if _changed(new_question, draft.question):
        draft.question = new_question
        _note_change()

    st.divider()
    action_col1, action_col2, action_col3 = st.columns(3)
    validate_clicked = action_col1.button("Validate Scenario")
    reset_clicked = action_col3.button("Reset Scenario")

    if reset_clicked:
        scenario_lab.reset_scenario_lab(st.session_state)
        st.rerun()

    if validate_clicked:
        st.session_state[scenario_lab.SCENARIO_VALIDATION_KEY] = scenario_lab.validate_scenario(draft)

    validation = st.session_state.get(scenario_lab.SCENARIO_VALIDATION_KEY)
    can_run = False
    if validation is not None:
        if validation.level == ValidationLevel.ERROR:
            st.error("Scenario has structural ERROR-level issues — Run Investigation is unavailable until fixed.")
        elif validation.level == ValidationLevel.WARNING:
            st.warning("Scenario has WARNING-level conflicting evidence — this is a valid, useful test scenario.")
            can_run = True
        else:
            st.success("Scenario passed structural validation.")
            can_run = True
        for issue in validation.issues:
            marker = "🔴" if issue.level == ValidationLevel.ERROR else "🟡"
            st.write(f"{marker} **{issue.level.value}**: {issue.message}")
    else:
        st.caption("Click “Validate Scenario” before running an investigation.")

    run_clicked = action_col2.button("Run Investigation", type="primary", disabled=not can_run)

    if run_clicked and can_run:
        with st.spinner("Assembling evidence, generating an investigation brief, and validating the draft…"):
            try:
                result, records, _ = scenario_lab.run_scenario_investigation(draft)
            except ScenarioBlockedError as exc:
                st.error(str(exc))
                result = None
                records = None
        if result is not None:
            st.session_state[scenario_lab.SCENARIO_RESULT_KEY] = result
            st.session_state[scenario_lab.SCENARIO_RECORDS_KEY] = records
            st.session_state.pop(scenario_lab.SCENARIO_REVIEW_DECISION_KEY, None)
            st.session_state.pop(scenario_lab.SCENARIO_JUDGE_RESULT_KEY, None)

    result = st.session_state.get(scenario_lab.SCENARIO_RESULT_KEY)
    records = st.session_state.get(scenario_lab.SCENARIO_RECORDS_KEY)
    if result is not None and records is not None:
        st.divider()
        st.subheader("Recorded Synthetic Claim Information")
        st.caption("TEMPORARY SYNTHETIC SCENARIO — not a production system record")
        claim_context = result.agent_result.claim_context
        if claim_context is not None and claim_context.claim is not None:
            c = claim_context.claim
            rcol1, rcol2, rcol3 = st.columns(3)
            rcol1.markdown(f"**Claim ID**\n\n{c.claim_id}")
            rcol2.markdown(f"**Status**\n\n{c.status}")
            rcol3.markdown(f"**Service**\n\n{c.service_code}")
            rcol4, rcol5, rcol6 = st.columns(3)
            rcol4.markdown(f"**Date of Service**\n\n{c.date_of_service}")
            rcol5.markdown(f"**Denial Reason Code**\n\n{c.denial_reason_code or '—'}")
            rcol6.markdown(f"**Denial Reason**\n\n{c.denial_reason_description or '—'}")

        _render_investigation_result(
            result,
            review_key=scenario_lab.SCENARIO_REVIEW_DECISION_KEY,
            judge_key=scenario_lab.SCENARIO_JUDGE_RESULT_KEY,
        )


# =====================================================================================
# Top-level navigation (Module 5: Dispute Review is now first/default;
# Golden Dataset & Evaluation replaces Predefined Claims; Scenario Lab is
# hidden unless SHOW_SCENARIO_LAB_TAB=1 -- see _SHOW_SCENARIO_LAB_TAB above)
# =====================================================================================

if _SHOW_SCENARIO_LAB_TAB:
    dispute_review_tab, golden_eval_tab, scenario_tab = st.tabs(
        ["Dispute Review", "Golden Dataset & Evaluation", "Scenario Lab"]
    )
else:
    dispute_review_tab, golden_eval_tab = st.tabs(["Dispute Review", "Golden Dataset & Evaluation"])

with dispute_review_tab:
    render_dispute_review_tab(st.session_state)

with golden_eval_tab:
    _render_golden_evaluation_tab()

if _SHOW_SCENARIO_LAB_TAB:
    with scenario_tab:
        _render_scenario_lab_tab()

st.divider()
_render_guardrails_expander()
