"""Module 2 (v2): a read-only, ISOLATED lookup over a separately-seeded
synthetic authorization-source fixture.

Deliberately mirrors tools/data_store.py's loading pattern (validate every
record against a Pydantic model at load time, index once, expose read-only
lookups) WITHOUT reusing tools.data_store.DataStore or
data/prior_authorizations.json -- see this module's own AuthorizationSourceStore.
"Independent" means separate from the submitted form data and from the
baseline prior-authorization fixture in this demo; it does not mean a real
payer system is contacted, queried, or authenticated anywhere in this
module.

Nothing in this module is wired into skills/investigate_dispute.py,
application/dispute_workflow.py, or dispute_review/ui.py -- that
connection is explicitly Module 3's job (see docs/V2_AUTHORIZATION_EVIDENCE.md).
This module has zero callers in the live application as of Module 2.

No database, no external service, no network call: one JSON fixture file,
loaded once per process (mirroring tools.data_store.get_data_store's
@lru_cache singleton pattern) and re-loadable with an explicit alternate
path for test isolation, exactly like tests/test_data_store.py's own
`DataStore(data_dir=tmp_path)` pattern.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from dispute_review.authorization_source_models import (
    AuthorizationSourceLookupResult,
    AuthorizationSourceLookupStatus,
    AuthorizationSourceRecord,
)
from tools.validation import require_identifier

DEFAULT_AUTHORIZATION_SOURCE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "authorization_source_records.json"
)


class AuthorizationSourceLoadError(RuntimeError):
    """Raised when the isolated authorization-source fixture is missing,
    malformed, or fails schema/integrity validation. Mirrors
    tools.data_store.DataLoadError's role for the baseline dataset -- a
    deliberately separate exception type, never raised by or caught
    alongside DataLoadError."""


def _load_records(path: Path) -> list[AuthorizationSourceRecord]:
    if not path.is_file():
        raise AuthorizationSourceLoadError(f"Missing authorization-source fixture: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthorizationSourceLoadError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise AuthorizationSourceLoadError(f"{path.name} must contain a JSON list of records")

    records: list[AuthorizationSourceRecord] = []
    for index, item in enumerate(raw):
        try:
            records.append(AuthorizationSourceRecord.model_validate(item))
        except ValidationError as exc:
            raise AuthorizationSourceLoadError(
                f"{path.name} record #{index} failed validation: {exc}"
            ) from exc
    return records


class AuthorizationSourceStore:
    """Validated, indexed, in-memory snapshot of the isolated
    authorization-source fixture. Read-only after construction: nothing in
    this class, or in any function in this module, ever writes back to the
    fixture file or mutates a loaded record -- a caller's submitted/edited
    form values can never create or update a record here (there is no
    write path at all).

    Every source-record VERSION is indexed by its own unique
    `source_record_id` and is never overwritten by a later version -- an
    amendment is a NEW record with a NEW id that links back via
    `amends_source_record_id`, so earlier versions are always still
    present in `records_by_id` and in `records_by_reference`.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_AUTHORIZATION_SOURCE_PATH
        records = _load_records(self.path)

        self.records_by_id: dict[str, AuthorizationSourceRecord] = {}
        for record in records:
            if record.source_record_id in self.records_by_id:
                raise AuthorizationSourceLoadError(
                    f"Duplicate source_record_id in {self.path.name}: "
                    f"{record.source_record_id!r}"
                )
            self.records_by_id[record.source_record_id] = record

        # Integrity check: every amends_source_record_id must point at a
        # record that actually exists in this same fixture -- a dangling
        # link would silently break version-chain traceability.
        for record in records:
            if record.amends_source_record_id is not None and record.amends_source_record_id not in self.records_by_id:
                raise AuthorizationSourceLoadError(
                    f"{record.source_record_id!r} sets amends_source_record_id="
                    f"{record.amends_source_record_id!r}, which does not exist in "
                    f"{self.path.name}."
                )

        # Integrity check: the amended-from record's own record_version must
        # be strictly lower than this record's -- required for "amendment"
        # to be a well-founded, acyclic relation. Without this, two records
        # could each set the other as amends_source_record_id (a 2-cycle),
        # which _chain_root_id's cycle guard would silently paper over
        # rather than reject at load time, where a malformed fixture should
        # actually be caught.
        for record in records:
            if record.amends_source_record_id is not None:
                parent = self.records_by_id[record.amends_source_record_id]
                if parent.record_version >= record.record_version:
                    raise AuthorizationSourceLoadError(
                        f"{record.source_record_id!r} (record_version={record.record_version}) "
                        f"amends {parent.source_record_id!r} (record_version={parent.record_version}), "
                        "which is not a strictly lower version -- an amendment chain must be "
                        "strictly increasing in record_version to be well-founded."
                    )

        self.records_by_reference: dict[str, list[AuthorizationSourceRecord]] = {}
        for record in records:
            self.records_by_reference.setdefault(record.authorization_reference_number, []).append(record)


@lru_cache(maxsize=1)
def get_authorization_source_store() -> AuthorizationSourceStore:
    """Return the process-wide, isolated AuthorizationSourceStore singleton,
    loading it from the default fixture on first call only. Entirely
    separate from tools.data_store.get_data_store()'s singleton -- loading
    this store never touches, and is never touched by, the baseline
    dataset."""
    return AuthorizationSourceStore()


def _chain_root_id(record: AuthorizationSourceRecord, by_id: dict[str, AuthorizationSourceRecord]) -> str:
    """Walk amends_source_record_id backwards to the earliest ancestor
    that is ALSO present in `by_id` (the current match set) -- a record
    whose parent is outside the match set is treated as its own root
    within this lookup, since it is not, within this query, traceable back
    further. Cycle-guarded defensively; a validated fixture cannot contain
    a cycle (see AuthorizationSourceStore's own integrity checks), but this
    function must never hang if one somehow existed."""
    seen: set[str] = set()
    current = record
    while current.amends_source_record_id is not None and current.amends_source_record_id in by_id:
        if current.source_record_id in seen:
            break  # defensive only -- a validated fixture cannot cycle
        seen.add(current.source_record_id)
        current = by_id[current.amends_source_record_id]
    return current.source_record_id


def lookup_authorization_source_by_reference(
    authorization_reference_number: str, *, store: Optional[AuthorizationSourceStore] = None
) -> AuthorizationSourceLookupResult:
    """Look up every authorization-source record matching this reference
    number, in the isolated fixture only.

    A reference number is a LOOKUP KEY, never proof of applicability: a
    FOUND result means a matching version-chain exists in this synthetic
    fixture, not that it belongs to any particular claim or covers any
    particular service date -- that determination belongs to a later
    comparison layer (Module 3).

    Outcome rules (see AuthorizationSourceLookupStatus for the full
    contract):
      - NOT_FOUND: no record anywhere in the fixture has this reference
        number.
      - FOUND: every matching record traces back (via amends_source_record_id)
        to exactly ONE root record -- a single, traceable amendment
        history. `record` is that chain's current (un-amended-further)
        version; `version_history` carries every version, oldest first.
        Nothing is silently applicability-tested against any claim here.
      - AMBIGUOUS: matching records trace back to MORE THAN ONE independent
        root -- a genuine data-integrity ambiguity (not a normal amendment
        history). No record is selected; `candidates` lists all of them.
      - ERROR: the lookup could not complete due to an unexpected failure
        reading the underlying fixture -- distinct from NOT_FOUND, which
        means the lookup ran and legitimately found nothing.

    Raises ValueError if authorization_reference_number is None, not a
    string, empty, or contains whitespace -- a caller/input bug, exactly
    like tools.prior_auth_tool.get_prior_authorization_by_id's own
    convention, never folded into a NOT_FOUND/ERROR result.
    """
    require_identifier(authorization_reference_number, "authorization_reference_number")
    resolved_store = store if store is not None else get_authorization_source_store()

    try:
        matches = list(resolved_store.records_by_reference.get(authorization_reference_number, []))
        if not matches:
            return AuthorizationSourceLookupResult(
                status=AuthorizationSourceLookupStatus.NOT_FOUND,
                queried_reference_number=authorization_reference_number,
                detail="Not found in the accessible synthetic authorization-source fixture.",
            )

        by_id = {record.source_record_id: record for record in matches}
        chains: dict[str, list[AuthorizationSourceRecord]] = {}
        for record in matches:
            root_id = _chain_root_id(record, by_id)
            chains.setdefault(root_id, []).append(record)

        if len(chains) > 1:
            return AuthorizationSourceLookupResult(
                status=AuthorizationSourceLookupStatus.AMBIGUOUS,
                queried_reference_number=authorization_reference_number,
                candidates=matches,
                detail=(
                    f"{len(chains)} independent authorization-source records (not a single "
                    "amendment history) share this reference number -- no record was selected."
                ),
            )

        (chain,) = chains.values()
        amended_from_ids = {
            record.amends_source_record_id for record in chain if record.amends_source_record_id is not None
        }
        tip_candidates = [
            record for record in chain if record.source_record_id not in amended_from_ids
        ]
        version_history = sorted(chain, key=lambda r: r.record_version)

        if len(tip_candidates) != 1:
            # A single root with MORE than one un-amended tip is a branching
            # chain -- e.g. two different records both set the same
            # amends_source_record_id (a fork), or (only reachable from a
            # store built by bypassing the validated loader) zero tips at
            # all. Either way this is a conflicting/inconsistent chain, not
            # a normal linear amendment history -- it must never be
            # resolved by silently picking the highest record_version.
            # AMBIGUOUS is the correct, explicit outcome (Module 3 Step 2):
            # nothing is selected; every version in the chain is surfaced
            # as an unresolved candidate.
            return AuthorizationSourceLookupResult(
                status=AuthorizationSourceLookupStatus.AMBIGUOUS,
                queried_reference_number=authorization_reference_number,
                candidates=version_history,
                detail=(
                    f"A single amendment history was found for this reference number, but it is "
                    f"internally inconsistent: {len(tip_candidates)} un-amended version(s) exist "
                    f"among {len(chain)} total (expected exactly 1) -- this looks like a branching "
                    "or forked chain, not a normal linear amendment history. No version was selected."
                ),
            )

        current = tip_candidates[0]

        return AuthorizationSourceLookupResult(
            status=AuthorizationSourceLookupStatus.FOUND,
            queried_reference_number=authorization_reference_number,
            record=current,
            version_history=version_history,
            detail=(
                f"{len(version_history)} version(s) on file for this reference number."
                if len(version_history) > 1
                else "1 version on file for this reference number."
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- fixture-access boundary; never leak a raw
        # traceback, and never let an unexpected failure masquerade as a legitimate
        # NOT_FOUND result. Mirrors context/dispute_evidence_retriever.py's own broad
        # `except Exception` boundary for an external-subsystem-style read.
        return AuthorizationSourceLookupResult(
            status=AuthorizationSourceLookupStatus.ERROR,
            queried_reference_number=authorization_reference_number,
            detail=f"Authorization-source lookup failed unexpectedly: {exc}",
        )


def get_authorization_source_record_by_id(
    source_record_id: str, *, store: Optional[AuthorizationSourceStore] = None
) -> Optional[AuthorizationSourceRecord]:
    """Return the exact AuthorizationSourceRecord version with this
    source_record_id, or None if none exists. Exact-match only, always
    unambiguous (a unique-key lookup cannot be AMBIGUOUS) -- for citing one
    specific version, e.g. from a version_history entry, rather than
    re-resolving by reference number.

    Raises ValueError if source_record_id is None, not a string, empty, or
    contains whitespace -- a caller/input bug, never a "not found" result.
    """
    require_identifier(source_record_id, "source_record_id")
    resolved_store = store if store is not None else get_authorization_source_store()
    return resolved_store.records_by_id.get(source_record_id)
