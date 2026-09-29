"""H3: testable view-model / state-helper logic for the Streamlit Interview
Workbench (app.py). Kept out of app.py so the actual decision logic --
which case defaults to which question, when stale results get cleared, how
a review decision is recorded -- can be unit-tested without running a real
Streamlit session (see tests/test_workbench.py).

app.py is expected to stay a thin rendering script: it calls
application.investigation_service.run_investigation directly (never the
OpenAI adapter directly) and passes st.session_state into these functions
(a MutableMapping-like object in real Streamlit; a plain dict in tests).

Nothing here adds business logic beyond presentation concerns: case
descriptors and default questions are read verbatim from the existing
deterministic tools.claim_tool.get_claim lookup (the same Tool layer
agents/skills already use), never invented or hard-coded as investigation
conclusions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import MutableMapping, Optional

from agents.state import AgentResult
from application.context_assembler import assemble_context
from application.models import ApplicationResult, AssembledContext, GenerationStatus, ValidationStatus
from tools.claim_tool import get_claim

CASE_IDS: list[str] = ["CLM-1001", "CLM-1002", "CLM-1003", "CLM-1004", "CLM-1005"]

# Demo-convenience defaults only -- editable in the UI. Phrasing mirrors the
# already-vetted evals/agent_golden_set.json queries for these same claims,
# not new business language. CLM-1001's default matches the task's exact
# specified wording.
DEFAULT_QUESTIONS: dict[str, str] = {
    "CLM-1001": "Why was this claim denied, and what evidence is available?",
    "CLM-1002": "Was this claim paid correctly, and is there an approved authorization on file?",
    "CLM-1003": "Why was my lab claim denied?",
    "CLM-1004": "Why was my physical therapy claim denied?",
    "CLM-1005": "Why was my specialist visit claim denied?",
}

REVIEW_ACCEPTED = "ACCEPTED"
REVIEW_MARKED_FOR_REVIEW = "MARKED_FOR_REVIEW"


def case_descriptor(claim_id: str) -> str:
    """A short, neutral label for the case selector, built only from
    recorded claim fields (service code, status, denial reason code) --
    never an investigation conclusion. Falls back to the bare claim_id if
    the claim can't be looked up (should not happen for the 5 known cases).
    """
    claim = get_claim(claim_id)
    if claim is None:
        return claim_id
    if claim.denial_reason_code:
        return f"{claim_id} — {claim.service_code} ({claim.status} / {claim.denial_reason_code})"
    return f"{claim_id} — {claim.service_code} ({claim.status})"


def case_catalog() -> list[tuple[str, str]]:
    """[(claim_id, descriptor), ...] for the 5 known cases, in order."""
    return [(claim_id, case_descriptor(claim_id)) for claim_id in CASE_IDS]


def reset_investigation_state(state: MutableMapping) -> None:
    """Clear any previous investigation result, review decision, and
    (H6, optional) semantic-judge result. Called whenever the selected
    case or the scoped question changes, so a stale result from a
    different case/question can never remain on screen (H3 regression
    requirement -- see tests/test_workbench.py; H6 extension -- see
    tests/test_semantic_judge.py). "judge_result" is a
    application.judge_models.SemanticJudgeEnvelope when present; clearing
    it here never touches investigation_result/review_decision's own
    clearing behavior, which is unchanged."""
    state.pop("investigation_result", None)
    state.pop("review_decision", None)
    state.pop("judge_result", None)


def on_case_selected(state: MutableMapping, claim_id: str) -> None:
    """Apply the effects of the user picking a new case: reset the scoped
    question to that case's default and clear any stale result."""
    state["selected_claim_id"] = claim_id
    state["question_text"] = DEFAULT_QUESTIONS.get(claim_id, "")
    reset_investigation_state(state)


def on_question_changed(state: MutableMapping, question_text: str) -> None:
    """Apply the effects of the user editing the scoped question: store the
    new text and clear any stale result (it belongs to the old question)."""
    state["question_text"] = question_text
    reset_investigation_state(state)


def record_review_decision(state: MutableMapping, claim_id: str, decision: str, *, key: str = "review_decision") -> dict:
    """Record a LOCAL, session-only reviewer decision. Never mutates any
    claim/source data or typed result object -- this is purely a local
    prototype record, per AGENTS.md rule 7 ("human review is local and
    simulated"). Returns the recorded entry for immediate display.

    `key` defaults to "review_decision" (Predefined Claims' own key,
    unchanged from H3). H7's Scenario Lab passes its own separately
    namespaced key (application.scenario_lab.SCENARIO_REVIEW_DECISION_KEY)
    so the same entry-construction logic isn't duplicated in app.py for a
    second, differently-keyed session-state slot.
    """
    if decision not in (REVIEW_ACCEPTED, REVIEW_MARKED_FOR_REVIEW):
        raise ValueError(f"Unknown review decision: {decision!r}")
    entry = {
        "claim_id": claim_id,
        "decision": decision,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    state[key] = entry
    return entry


def is_accepted_draft(result: ApplicationResult) -> bool:
    """True exactly when app.py presents result.investigation_brief as an
    accepted investigation brief -- DRAFTED generation AND PASSED
    validation. Extracted here (rather than inlined twice in app.py) so
    this exact gating condition is unit-testable without running
    Streamlit, and so app.py's branching and its "Accept" button's
    availability can never drift apart.
    """
    return (
        result.generation_status == GenerationStatus.DRAFTED
        and result.validation_result.status == ValidationStatus.PASSED
    )


def get_display_context(result: ApplicationResult) -> Optional[AssembledContext]:
    """The AssembledContext to render in the Supporting Evidence section.

    Reuses `result.assembled_context` when the application service already
    built one (the EVIDENCE_SUFFICIENT/generation-attempted path). For
    NEEDS_REVIEW, investigation_service never calls assemble_context (there
    is nothing to generate a brief from), but the EvidencePackage the agent
    already built is still sitting on `agent_result.evidence_package` --
    assemble_context() is a pure, stateless relabeling function over data
    that already exists (see application/context_assembler.py), so calling
    it here for display purposes does NOT re-run retrieval, tools, or the
    graph. Returns None only when no EvidencePackage exists at all (e.g.
    the unknown-claim/invalid-input ERROR paths).
    """
    if result.assembled_context is not None:
        return result.assembled_context
    agent_result: AgentResult = result.agent_result
    if agent_result.evidence_package is not None:
        return assemble_context(agent_result)
    return None
