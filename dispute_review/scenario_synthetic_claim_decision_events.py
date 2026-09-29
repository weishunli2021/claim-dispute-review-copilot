"""Module 4 review follow-up: scenario-only synthetic claim-decision
events for dispute-review demonstrations.

Why this module exists: `dispute_review.models.ClaimSnapshot` and
`tools.models.Claim` (data/claims.json) have NO denial-decision timestamp
field at all -- a claim record only carries `date_of_service`. Module 3's
"provider corrected after denial" demonstration (Scenario A) needs its
"after denial" chronology narrative to be backed by an actual, citable
timestamp, not prose alone -- see docs/V2_DISPUTE_SCENARIOS.md and
docs/V2_GOLDEN_REFERENCE_REVIEW.md for the review finding that led here.

This module is the ONLY source of that timestamp, and it is deliberately
ISOLATED from every baseline claim data path:
  - It is a SEPARATE fixture file (data/scenario_synthetic_claim_decision_
    events.json), never data/claims.json.
  - It is a SEPARATE typed model (ScenarioOnlyClaimDecisionEvent), never a
    field added to dispute_review.models.ClaimSnapshot or tools.models.Claim.
  - `is_synthetic` is fixed True by validation -- this module cannot
    represent a real, recorded claim-decision event, only an invented one.
  - Every consumer is expected to render `label` (which states plainly
    that the timestamp is invented and why) alongside `decided_at` --
    never the bare timestamp on its own.

Mirrors dispute_review.authorization_source_lookup's loading shape
(validate every record against a Pydantic model at load time, expose a
read-only lookup, accept an explicit alternate path for test isolation)
without reusing any of its code -- this is a distinct, single-purpose
fixture answering a distinct question (when a demo NARRATIVELY treats a
claim as denied, never anything about an authorization).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

DEFAULT_SCENARIO_CLAIM_DECISION_EVENTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "scenario_synthetic_claim_decision_events.json"
)


class ScenarioClaimDecisionEventLoadError(RuntimeError):
    """Raised when the isolated scenario-event fixture is missing,
    malformed, or fails schema validation. A distinct exception type,
    never raised alongside dispute_review.authorization_source_lookup's
    AuthorizationSourceLoadError -- the two fixtures are unrelated."""


class ScenarioOnlyClaimDecisionEvent(BaseModel):
    """One invented claim-decision timestamp for exactly one demonstration
    scenario. `is_synthetic` must be True -- this type structurally cannot
    represent a real, recorded event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    event_type: str
    decided_at: datetime
    is_synthetic: bool = True
    label: str

    @field_validator("is_synthetic")
    @classmethod
    def _must_stay_synthetic(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "ScenarioOnlyClaimDecisionEvent.is_synthetic must be True -- this type is "
                "reserved for invented, scenario-only timestamps, never a real recorded one"
            )
        return value


def _load_events(path: Path) -> dict[str, ScenarioOnlyClaimDecisionEvent]:
    if not path.is_file():
        raise ScenarioClaimDecisionEventLoadError(f"Missing scenario claim-decision fixture: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioClaimDecisionEventLoadError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ScenarioClaimDecisionEventLoadError(f"{path.name} must contain a JSON list of events")

    events: dict[str, ScenarioOnlyClaimDecisionEvent] = {}
    for index, item in enumerate(raw):
        try:
            event = ScenarioOnlyClaimDecisionEvent.model_validate(item)
        except ValidationError as exc:
            raise ScenarioClaimDecisionEventLoadError(
                f"{path.name} event #{index} failed validation: {exc}"
            ) from exc
        if event.claim_id in events:
            raise ScenarioClaimDecisionEventLoadError(
                f"{path.name} has more than one event for claim_id={event.claim_id!r}"
            )
        events[event.claim_id] = event
    return events


def get_scenario_synthetic_claim_decision_event(
    claim_id: str,
    *,
    path: Optional[Path] = None,
) -> Optional[ScenarioOnlyClaimDecisionEvent]:
    """Return the scenario-only synthetic claim-decision event for
    `claim_id`, or None if this demonstration fixture defines none for it
    (the normal case for every claim except CLM-1001's Scenario A demo --
    an absent event is never treated as "denial confirmed absent", just as
    "no invented chronology anchor exists for this claim")."""
    events = _load_events(path or DEFAULT_SCENARIO_CLAIM_DECISION_EVENTS_PATH)
    return events.get(claim_id)
