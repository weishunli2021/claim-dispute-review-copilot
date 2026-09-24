"""Reusable typed contracts for the Skills layer.

Skill = a reusable BUSINESS CAPABILITY that may coordinate multiple
tools/context sources -- as distinct from a Tool (one narrow
deterministic operation, tools/) and an Agent (which chooses/sequences
capabilities toward an objective, agents/). Every skill in skills/
implements this same lightweight contract, described by SkillMetadata and
returning a SkillResult.

No LLM anywhere in this module or in any skill built on it. No polished
final natural-language answer field on SkillResult -- skills produce
evidence and status, not a member-facing response.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    """Coarse, human-reviewable risk classification for a skill's own
    operations -- not a claim-outcome risk score. Every skill in this
    stage is read-only (no external side effects), so all four currently
    register at LOW; MEDIUM/HIGH are reserved for a future skill with
    real-world side effects (e.g. one that actually files an escalation
    ticket, rather than escalate_case's synthetic packaging)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SkillStatus(str, Enum):
    """Controlled set of skill outcomes. A skill call is a single
    synchronous operation (unlike agents.state.AgentStatus, there is no
    RUNNING state to observe)."""

    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


class SkillMetadata(BaseModel):
    """Machine-readable description of one skill, independent of any
    particular invocation. Exposed by every skill module as a
    module-level `METADATA` constant and registered alongside the skill's
    callable in skills/registry.py."""

    name: str
    version: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    risk_level: RiskLevel


class SkillInput(BaseModel):
    """Base class for a skill's typed input. Each skill defines its own
    subclass with the specific fields it needs (e.g.
    skills/investigate_claim.py: InvestigateClaimInput). `extra="forbid"`
    so a caller typo in a field name fails loudly instead of being
    silently ignored."""

    model_config = ConfigDict(extra="forbid")


class SkillResult(BaseModel):
    """The typed result every skill returns.

    `evidence` is intentionally a plain dict: different skills produce
    differently-shaped evidence (an EvidencePackage, a benefit + policy
    chunks, a list of authorization records, an escalation summary), and
    this is the one common envelope they all fit through. Each skill
    module's own docstring documents exactly which keys it populates and
    what each holds. No polished final natural-language answer field --
    see the module docstring.
    """

    skill_name: str
    skill_version: str
    status: SkillStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    next_capability: Optional[str] = None
    error: Optional[str] = None
