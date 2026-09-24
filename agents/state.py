"""Typed state and product-facing result for the claim-investigation agent.

Two distinct types live here, deliberately kept close together:

- AgentState: the internal state LangGraph threads through the compiled
  graph (see agents/case_agent.py, agents/nodes.py, agents/routing.py).
  Application callers never see this.
- AgentResult: the product-facing type run_case_agent() actually returns.
  It deliberately does NOT expose LangGraph-internal bookkeeping
  (current_step, max_steps) and has no final-answer field -- this stage
  produces an investigation trace and an EvidencePackage, never a
  generated natural-language answer.
"""

from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from context.models import EvidencePackage
from tools.case_context import CaseContext


class AgentStatus(str, Enum):
    """Controlled set of agent statuses. RUNNING is the only non-terminal
    value; every completed run ends in exactly one of the other four."""

    RUNNING = "RUNNING"
    EVIDENCE_SUFFICIENT = "EVIDENCE_SUFFICIENT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ERROR = "ERROR"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"


class AgentState(TypedDict):
    """Internal LangGraph state.

    actions_taken uses an `operator.add` reducer: each node returns a
    one-element list (e.g. ["LOAD_CASE"]) that LangGraph appends to the
    running trace, rather than the default "last write wins" merge every
    other field uses (which is correct here since exactly one node
    updates each of those fields per step in this non-branching-per-field
    workflow).
    """

    original_query: str
    claim_id: str
    claim_context: Optional[CaseContext]
    evidence_package: Optional[EvidencePackage]
    missing_information: list[str]
    actions_taken: Annotated[list[str], operator.add]
    current_step: str
    status: str
    error: Optional[str]
    step_count: int
    max_steps: int
    # Internal-only: the investigate_claim skill's own SkillStatus value
    # (see skills/investigate_claim.py), set by the build_evidence node.
    # route_after_assess_evidence reads this directly rather than
    # re-deriving evidence sufficiency itself -- that rule now lives in
    # the skill, not in the agent. Never exposed on AgentResult.
    skill_status: Optional[str]


class AgentResult(BaseModel):
    """Product-facing result of one claim-investigation agent run.

    No final natural-language answer field, on purpose -- see the module
    docstring. `actions_taken` preserves the deterministic execution
    order of every node the graph actually ran.
    """

    status: AgentStatus
    actions_taken: list[str]
    claim_context: Optional[CaseContext] = None
    evidence_package: Optional[EvidencePackage] = None
    missing_information: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    step_count: int
