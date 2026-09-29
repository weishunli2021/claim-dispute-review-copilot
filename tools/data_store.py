"""Loading and validation for the synthetic JSON data fixtures.

This module owns *loading* the datasets under data/ into typed, validated,
indexed in-memory records. It contains no business-facing lookup logic --
that lives in the sibling tool modules (member_tool.py, claim_tool.py,
etc.), which read from a DataStore instance rather than parsing JSON
themselves.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Callable, Hashable, Type, TypeVar

from pydantic import BaseModel, ValidationError

from tools.models import (
    Benefit,
    Claim,
    Member,
    Plan,
    PriorAuthorization,
    Provider,
)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

T = TypeVar("T", bound=BaseModel)


class DataLoadError(RuntimeError):
    """Raised when a synthetic data file is missing, malformed, or fails schema validation."""


def _load_records(base_dir: Path, filename: str, model: Type[T]) -> list[T]:
    """Read filename from base_dir as a JSON list and validate each item against model."""
    path = base_dir / filename

    if not path.is_file():
        raise DataLoadError(f"Missing synthetic data file: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataLoadError(f"{filename} is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise DataLoadError(f"{filename} must contain a JSON list of records")

    records: list[T] = []
    for index, item in enumerate(raw):
        try:
            records.append(model.model_validate(item))
        except ValidationError as exc:
            raise DataLoadError(f"{filename} record #{index} failed validation: {exc}") from exc
    return records


def _index_by(
    records: list[T], key_fn: Callable[[T], Hashable], description: str
) -> dict[Hashable, T]:
    """Index records by a unique key, raising DataLoadError on duplicate keys."""
    index: dict[Hashable, T] = {}
    for record in records:
        key = key_fn(record)
        if key in index:
            raise DataLoadError(f"Duplicate {description} key found in synthetic data: {key!r}")
        index[key] = record
    return index


def _group_by(records: list[T], key_fn: Callable[[T], Hashable]) -> dict[Hashable, list[T]]:
    """Group records by a non-unique key. Unlike _index_by, duplicates are expected."""
    groups: dict[Hashable, list[T]] = {}
    for record in records:
        groups.setdefault(key_fn(record), []).append(record)
    return groups


class DataStore:
    """Validated, indexed, in-memory snapshot of every synthetic dataset.

    All datasets are loaded and validated once, at construction. This class
    performs no business logic and makes no inferences about missing
    records -- it only loads what is on disk and exposes it for lookup.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        base_dir = data_dir or DEFAULT_DATA_DIR

        self.members: dict[str, Member] = _index_by(
            _load_records(base_dir, "members.json", Member),
            lambda m: m.member_id,
            "member_id",
        )
        self.plans: dict[str, Plan] = _index_by(
            _load_records(base_dir, "plans.json", Plan),
            lambda p: p.plan_id,
            "plan_id",
        )
        self.providers: dict[str, Provider] = _index_by(
            _load_records(base_dir, "providers.json", Provider),
            lambda p: p.provider_id,
            "provider_id",
        )
        self.claims: dict[str, Claim] = _index_by(
            _load_records(base_dir, "claims.json", Claim),
            lambda c: c.claim_id,
            "claim_id",
        )
        self.benefits: dict[tuple[str, str], Benefit] = _index_by(
            _load_records(base_dir, "benefits.json", Benefit),
            lambda b: (b.plan_id, b.service_code),
            "(plan_id, service_code)",
        )

        prior_authorizations = _load_records(
            base_dir, "prior_authorizations.json", PriorAuthorization
        )
        # authorization_id is the unique key -- a member/service pair may
        # legitimately have several authorization records over time.
        self.prior_authorizations: dict[str, PriorAuthorization] = _index_by(
            prior_authorizations, lambda a: a.authorization_id, "authorization_id"
        )
        self.prior_authorizations_by_member_service: dict[
            tuple[str, str], list[PriorAuthorization]
        ] = _group_by(prior_authorizations, lambda a: (a.member_id, a.service_code))


@lru_cache(maxsize=1)
def get_data_store() -> DataStore:
    """Return the process-wide DataStore, loading it from disk on first call only."""
    return DataStore()
