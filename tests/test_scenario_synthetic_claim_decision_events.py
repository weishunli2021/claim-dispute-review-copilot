"""Module 4 review follow-up: tests for the isolated, scenario-only
synthetic claim-decision event fixture
(dispute_review/scenario_synthetic_claim_decision_events.py).

Confirms: the fixture is genuinely separate from data/claims.json, every
event is forced to be labeled synthetic, malformed/duplicate input is
rejected the same way dispute_review.authorization_source_lookup rejects
its own fixture, and an unknown claim_id returns None rather than a
guessed or default event.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from dispute_review.scenario_synthetic_claim_decision_events import (
    DEFAULT_SCENARIO_CLAIM_DECISION_EVENTS_PATH,
    ScenarioClaimDecisionEventLoadError,
    ScenarioOnlyClaimDecisionEvent,
    get_scenario_synthetic_claim_decision_event,
)


def test_default_fixture_file_exists_and_is_isolated_from_claims_json():
    assert DEFAULT_SCENARIO_CLAIM_DECISION_EVENTS_PATH.is_file()
    assert DEFAULT_SCENARIO_CLAIM_DECISION_EVENTS_PATH.name != "claims.json"
    assert DEFAULT_SCENARIO_CLAIM_DECISION_EVENTS_PATH.parent.name == "data"


def test_get_event_for_clm_1001_returns_the_seeded_scenario_a_event():
    event = get_scenario_synthetic_claim_decision_event("CLM-1001")
    assert event is not None
    assert event.claim_id == "CLM-1001"
    assert event.event_type == "claim_denial_decision"
    assert event.is_synthetic is True
    assert event.decided_at.isoformat() == "2026-02-11T12:00:00+00:00"
    assert "SCENARIO-ONLY SYNTHETIC" in event.label


def test_get_event_for_unknown_claim_returns_none():
    assert get_scenario_synthetic_claim_decision_event("CLM-9999") is None
    assert get_scenario_synthetic_claim_decision_event("CLM-1002") is None


def test_is_synthetic_false_is_rejected():
    with pytest.raises(ValidationError):
        ScenarioOnlyClaimDecisionEvent(
            claim_id="CLM-1001",
            event_type="claim_denial_decision",
            decided_at="2026-02-11T12:00:00Z",
            is_synthetic=False,
            label="not actually synthetic",
        )


def test_missing_fixture_file_raises_load_error(tmp_path):
    with pytest.raises(ScenarioClaimDecisionEventLoadError):
        get_scenario_synthetic_claim_decision_event("CLM-1001", path=tmp_path / "does_not_exist.json")


def test_malformed_json_raises_load_error(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ScenarioClaimDecisionEventLoadError):
        get_scenario_synthetic_claim_decision_event("CLM-1001", path=bad_path)


def test_non_list_json_raises_load_error(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ScenarioClaimDecisionEventLoadError):
        get_scenario_synthetic_claim_decision_event("CLM-1001", path=bad_path)


def test_duplicate_claim_id_raises_load_error(tmp_path):
    duplicate_path = tmp_path / "duplicate.json"
    event = {
        "claim_id": "CLM-1001",
        "event_type": "claim_denial_decision",
        "decided_at": "2026-02-11T12:00:00Z",
        "is_synthetic": True,
        "label": "dup",
    }
    duplicate_path.write_text(json.dumps([event, event]), encoding="utf-8")
    with pytest.raises(ScenarioClaimDecisionEventLoadError):
        get_scenario_synthetic_claim_decision_event("CLM-1001", path=duplicate_path)


def test_invalid_record_raises_load_error(tmp_path):
    bad_path = tmp_path / "bad_record.json"
    bad_path.write_text(json.dumps([{"claim_id": "CLM-1001"}]), encoding="utf-8")
    with pytest.raises(ScenarioClaimDecisionEventLoadError):
        get_scenario_synthetic_claim_decision_event("CLM-1001", path=bad_path)
