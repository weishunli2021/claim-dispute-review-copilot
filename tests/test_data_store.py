"""Tests for the data-loading layer: validation, indexing, and error behavior."""

from __future__ import annotations

import json

import pytest

from tools.data_store import DataLoadError, DataStore

EMPTY_FILES = {
    "members.json": [],
    "plans.json": [],
    "providers.json": [],
    "claims.json": [],
    "benefits.json": [],
}


def _write_empty_supporting_files(tmp_path):
    for filename, content in EMPTY_FILES.items():
        (tmp_path / filename).write_text(json.dumps(content), encoding="utf-8")


def test_default_data_store_loads_all_datasets():
    store = DataStore()
    assert len(store.members) == 5
    assert len(store.plans) == 3
    assert len(store.providers) == 7
    assert len(store.claims) == 5
    assert len(store.benefits) == 3
    # authorization_id is the unique key; two records share (member, service).
    assert len(store.prior_authorizations) == 2


def test_prior_authorizations_are_grouped_by_member_and_service():
    store = DataStore()

    case_2_records = store.prior_authorizations_by_member_service[("M-1002", "MRI-KNEE")]
    assert len(case_2_records) == 2
    assert {r.authorization_id for r in case_2_records} == {"PA-1501", "PA-2001"}

    # Case 1: no authorization record exists for this member/service pair.
    assert ("M-1001", "MRI-KNEE") not in store.prior_authorizations_by_member_service


def test_missing_data_file_raises_clear_error(tmp_path):
    with pytest.raises(DataLoadError, match="Missing synthetic data file"):
        DataStore(data_dir=tmp_path)


def test_invalid_json_raises_clear_error(tmp_path):
    (tmp_path / "members.json").write_text("not valid json", encoding="utf-8")

    with pytest.raises(DataLoadError, match="not valid JSON"):
        DataStore(data_dir=tmp_path)


def test_schema_violation_raises_clear_error(tmp_path):
    # Missing several required Member fields.
    (tmp_path / "members.json").write_text(
        json.dumps([{"member_id": "M-1"}]), encoding="utf-8"
    )

    with pytest.raises(DataLoadError, match="failed validation"):
        DataStore(data_dir=tmp_path)


def test_duplicate_member_id_raises_clear_error(tmp_path):
    member = {
        "member_id": "M-1",
        "first_name": "Test",
        "last_name": "Person",
        "date_of_birth": "2000-01-01",
        "plan_id": "PLAN-GOLD",
        "status": "ACTIVE",
    }
    (tmp_path / "members.json").write_text(json.dumps([member, member]), encoding="utf-8")

    with pytest.raises(DataLoadError, match="Duplicate"):
        DataStore(data_dir=tmp_path)


def test_duplicate_authorization_id_raises_clear_error(tmp_path):
    _write_empty_supporting_files(tmp_path)

    authorization = {
        "authorization_id": "PA-DUPLICATE",
        "member_id": "M-1002",
        "service_code": "MRI-KNEE",
        "plan_id": "PLAN-GOLD",
        "status": "APPROVED",
        "effective_date": "2026-01-01",
        "expiration_date": "2026-06-30",
    }
    # Same authorization_id, different member/service -- still a duplicate
    # since authorization_id (not member/service) is the unique key.
    other = {**authorization, "member_id": "M-1003", "service_code": "LAB-BASIC"}
    (tmp_path / "prior_authorizations.json").write_text(
        json.dumps([authorization, other]), encoding="utf-8"
    )

    with pytest.raises(DataLoadError, match="Duplicate authorization_id"):
        DataStore(data_dir=tmp_path)
