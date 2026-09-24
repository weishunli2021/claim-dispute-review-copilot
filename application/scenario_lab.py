"""H7 (OPTIONAL evaluation/development capability): Scenario Lab.

Lets an evaluator compose a TEMPORARY synthetic claim using the exact
existing data schema (tools/models.py) and run it through the SAME,
unmodified investigation pipeline used by the Predefined Claims tab. This
is not a second investigation engine and not a production claim-entry
workflow -- see docs/SCENARIO_LAB.md.

Architecture:

    ScenarioDraft (form state)
      -> validate_scenario()              deterministic, structural only
      -> build_scenario_records()         real tools.models.* instances
      -> _temporary_data_overlay(records) in-memory-only, reversible
           -> application.investigation_service.run_investigation()  <- REAL, unmodified path
      -> (overlay torn down)

The overlay works by temporarily mutating the two process-wide singletons
every deterministic tool already reads from:

  - tools.data_store.get_data_store() -- an lru_cache(maxsize=1) DataStore
    whose .claims/.members/.benefits/.providers/.prior_authorizations are
    plain public dicts, shared by every tools/*_tool.py module regardless
    of how each imported the accessor function.
  - graph.retriever._get_graph() -- a SEPARATE lru_cache(maxsize=1) that
    builds the knowledge graph once from whatever DataStore existed at
    build time. Adding a scenario claim to the DataStore alone is not
    enough -- the graph must be told to rebuid (.cache_clear()) so
    graph.retriever.get_claim_neighborhood() can find the new claim node,
    or context.hybrid_retriever.build_evidence_package would raise
    NodeNotFoundError.

Both are restored to their exact original state in a `finally` block --
data/*.json is never read or written by this module, and nothing here
persists past the single run_investigation() call it wraps.

Scoped-replacement semantics (post-audit remediation): Scenario Lab defines
the scoped structured evidence visible to the scenario claim for the
fields it controls. Concretely, a scenario's benefit REPLACES (or, when
benefit_available=False, HIDES) whatever occupies the same
(plan_id, service_code) key for the duration of the run, and a scenario's
authorization list is the COMPLETE set visible for
(member_id, service_code) during the run -- any preexisting baseline
record at either key is temporarily hidden from every read path
(including graph/builder.py, which iterates the flat prior_authorizations
dict directly rather than the grouped index) and restored exactly
afterward, even if the scenario run raises an exception. See
_temporary_data_overlay's docstring for the exact mechanism.

This is NOT safe for concurrent multi-user access (a single mutable
process-wide singleton) -- Scenario Lab is a single-user/local
interview-development testing surface, not a shared or hosted-multi-user
feature; see docs/SCENARIO_LAB.md's known limitations. Predefined Claims
remains the primary demo path.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import MutableMapping, Optional

from application.investigation_service import run_investigation
from application.llm_adapter import BriefAdapter
from application.models import ApplicationResult
from graph.retriever import _get_graph
from tools.data_store import get_data_store
from tools.models import Benefit, Claim, Member, PriorAuthorization, Provider

# --- ID generation (never collides with baseline or already-generated ids) -----------

_SCENARIO_CLAIM_PREFIX = "CLM-SCN-"
_SCENARIO_MEMBER_PREFIX = "MEM-SCN-"
_SCENARIO_PROVIDER_PREFIX = "PRV-SCN-"
_SCENARIO_AUTH_PREFIX = "PA-SCN-"
_SCENARIO_BENEFIT_PREFIX = "BEN-SCN-"


def _next_id(prefix: str, reserved: set[str]) -> str:
    """A scenario id guaranteed not to collide with anything currently in
    the process-wide DataStore (baseline records or any other scenario
    record still resident from a run in progress) NOR with any id already
    handed out earlier in the same build_scenario_records() call --
    `reserved` is one shared set threaded through every _next_id call for
    a single scenario, updated in place. (Checking only the DataStore was
    not enough: generating several ids of the same prefix -- e.g. two
    authorizations -- before any of them is actually inserted into the
    DataStore would otherwise repeatedly return the same first-available
    candidate, silently colliding once both were written into the same
    dict key.)
    """
    store = get_data_store()
    existing = (
        set(store.claims)
        | set(store.members)
        | set(store.providers)
        | set(store.prior_authorizations)
        | {b.benefit_id for b in store.benefits.values()}
        | reserved
    )
    n = 1
    while True:
        candidate = f"{prefix}{n:03d}"
        if candidate not in existing:
            reserved.add(candidate)
            return candidate
        n += 1


def baseline_plan_ids() -> list[str]:
    return sorted(get_data_store().plans.keys())


def baseline_member_ids() -> list[str]:
    return sorted(get_data_store().members.keys())


def baseline_provider_ids() -> list[str]:
    return sorted(get_data_store().providers.keys())


# --- form state ------------------------------------------------------------------------


class MemberMode(str, Enum):
    EXISTING = "EXISTING"
    TEMPORARY = "TEMPORARY"


class ProviderMode(str, Enum):
    EXISTING = "EXISTING"
    TEMPORARY = "TEMPORARY"
    UNRESOLVED = "UNRESOLVED"
    NONE = "NONE"  # ordering provider only -- claim.ordering_provider_id stays None


@dataclass
class AuthorizationDraft:
    status: str = "APPROVED"
    effective_date: date = date(2026, 1, 1)
    expiration_date: date = date(2026, 6, 30)
    notes: str = ""


@dataclass
class ScenarioDraft:
    """Mutable, Streamlit-form-backed scenario definition. Every field
    here maps directly onto a real tools.models field -- no parallel
    schema is invented."""

    template: str = "Custom"
    question: str = "Why was this claim denied, and what evidence is available?"

    claim_status: str = "DENIED"
    denial_reason_code: Optional[str] = "AUTH_REQUIRED"
    denial_reason_description: str = ""
    service_code: str = "SCN-SERVICE"
    date_of_service: date = date(2026, 3, 1)
    billed_amount: float = 500.0
    allowed_amount: Optional[float] = None

    plan_id: str = "PLAN-GOLD"

    member_mode: MemberMode = MemberMode.TEMPORARY
    existing_member_id: Optional[str] = None

    benefit_available: bool = True
    benefit_covered: bool = True
    benefit_requires_prior_auth: bool = True
    benefit_network_requirement: Optional[str] = "in_network_only"

    servicing_provider_mode: ProviderMode = ProviderMode.TEMPORARY
    servicing_existing_provider_id: Optional[str] = None
    servicing_network_status: str = "in-network"
    servicing_provider_type: str = "facility"

    ordering_provider_mode: ProviderMode = ProviderMode.NONE
    ordering_existing_provider_id: Optional[str] = None
    ordering_network_status: str = "in-network"
    ordering_provider_type: str = "physician"

    authorizations: list[AuthorizationDraft] = field(default_factory=list)


TEMPLATE_NAMES: list[str] = [
    "Custom",
    "AUTH_REQUIRED — no authorization record",
    "AUTH_REQUIRED — expired authorization",
    "AUTH_REQUIRED — multiple authorization candidates",
    "Benefit not covered",
    "Out-of-network provider",
    "Missing provider",
    "Conflicting claim/provider evidence",
]


def build_template_draft(name: str) -> ScenarioDraft:
    """Templates ONLY prepopulate field values -- every field remains
    editable afterward, and none of these hard-codes an investigation
    answer (only recorded/structural facts, exactly like the five
    Predefined Claims cases do)."""
    if name == "AUTH_REQUIRED — no authorization record":
        return ScenarioDraft(
            template=name,
            claim_status="DENIED",
            denial_reason_code="AUTH_REQUIRED",
            benefit_available=True,
            benefit_covered=True,
            benefit_requires_prior_auth=True,
            authorizations=[],
        )
    if name == "AUTH_REQUIRED — expired authorization":
        return ScenarioDraft(
            template=name,
            claim_status="DENIED",
            denial_reason_code="AUTH_REQUIRED",
            benefit_requires_prior_auth=True,
            date_of_service=date(2026, 3, 1),
            authorizations=[
                AuthorizationDraft(
                    status="EXPIRED", effective_date=date(2025, 1, 1), expiration_date=date(2025, 6, 30)
                )
            ],
        )
    if name == "AUTH_REQUIRED — multiple authorization candidates":
        return ScenarioDraft(
            template=name,
            claim_status="PAID",
            denial_reason_code=None,
            benefit_requires_prior_auth=True,
            authorizations=[
                AuthorizationDraft(
                    status="EXPIRED", effective_date=date(2025, 1, 1), expiration_date=date(2025, 6, 30)
                ),
                AuthorizationDraft(
                    status="APPROVED", effective_date=date(2026, 1, 1), expiration_date=date(2026, 6, 30)
                ),
            ],
        )
    if name == "Benefit not covered":
        return ScenarioDraft(
            template=name,
            claim_status="DENIED",
            denial_reason_code="SERVICE_NOT_COVERED",
            benefit_available=True,
            benefit_covered=False,
            benefit_requires_prior_auth=False,
        )
    if name == "Out-of-network provider":
        return ScenarioDraft(
            template=name,
            claim_status="DENIED",
            denial_reason_code="OUT_OF_NETWORK_PROVIDER",
            servicing_provider_mode=ProviderMode.TEMPORARY,
            servicing_network_status="out-of-network",
        )
    if name == "Missing provider":
        return ScenarioDraft(
            template=name,
            claim_status="DENIED",
            denial_reason_code=None,
            servicing_provider_mode=ProviderMode.UNRESOLVED,
            benefit_available=False,
        )
    if name == "Conflicting claim/provider evidence":
        # Intentionally inconsistent -- denial reason says out-of-network,
        # but the provider's own recorded network status is in-network.
        # This must surface as a scenario-quality WARNING, never a
        # structural ERROR, and Run Investigation must remain available.
        return ScenarioDraft(
            template=name,
            claim_status="DENIED",
            denial_reason_code="OUT_OF_NETWORK_PROVIDER",
            servicing_provider_mode=ProviderMode.TEMPORARY,
            servicing_network_status="in-network",
        )
    return ScenarioDraft(template="Custom")


# --- validation ------------------------------------------------------------------------


class ValidationLevel(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class ScenarioIssue:
    level: ValidationLevel
    message: str


@dataclass
class ScenarioValidationResult:
    level: ValidationLevel
    issues: list[ScenarioIssue] = field(default_factory=list)


def _existing_provider_network_status(provider_id: Optional[str]) -> Optional[str]:
    if not provider_id:
        return None
    provider = get_data_store().providers.get(provider_id)
    return provider.network_status if provider else None


def validate_scenario(draft: ScenarioDraft) -> ScenarioValidationResult:
    """Checks SCENARIO CONSTRUCTION QUALITY only -- never a claim
    adjudication. ERROR = structurally invalid (missing required field,
    malformed dates, unresolved foreign key the scenario did not
    intentionally request). WARNING = internally inconsistent but
    structurally valid evidence (useful for testing how the pipeline
    handles conflicting evidence) -- Run Investigation stays available.
    """
    issues: list[ScenarioIssue] = []

    # --- ERROR: structural validity -----------------------------------------------
    if not draft.service_code or not draft.service_code.strip():
        issues.append(ScenarioIssue(ValidationLevel.ERROR, "Service code is required."))
    if draft.claim_status not in ("DENIED", "PAID"):
        issues.append(ScenarioIssue(ValidationLevel.ERROR, f"Unsupported claim status: {draft.claim_status!r}."))
    if draft.billed_amount is None or draft.billed_amount < 0:
        issues.append(ScenarioIssue(ValidationLevel.ERROR, "Billed amount must be a non-negative number."))
    if draft.plan_id not in baseline_plan_ids():
        issues.append(ScenarioIssue(ValidationLevel.ERROR, f"Unknown plan_id: {draft.plan_id!r}."))

    if draft.member_mode == MemberMode.EXISTING and not draft.existing_member_id:
        issues.append(ScenarioIssue(ValidationLevel.ERROR, "Select an existing member, or switch to a temporary member."))
    if draft.member_mode == MemberMode.EXISTING and draft.existing_member_id not in baseline_member_ids():
        issues.append(ScenarioIssue(ValidationLevel.ERROR, f"Unknown existing member_id: {draft.existing_member_id!r}."))

    if draft.servicing_provider_mode == ProviderMode.EXISTING and not draft.servicing_existing_provider_id:
        issues.append(ScenarioIssue(ValidationLevel.ERROR, "Select an existing servicing provider, or choose another mode."))
    if draft.ordering_provider_mode == ProviderMode.EXISTING and not draft.ordering_existing_provider_id:
        issues.append(ScenarioIssue(ValidationLevel.ERROR, "Select an existing ordering provider, or choose another mode."))

    for index, auth in enumerate(draft.authorizations, start=1):
        if auth.effective_date > auth.expiration_date:
            issues.append(
                ScenarioIssue(
                    ValidationLevel.ERROR,
                    f"Authorization #{index}: effective date ({auth.effective_date}) is after "
                    f"expiration date ({auth.expiration_date}).",
                )
            )

    # --- WARNING: intentional-conflict detection (never blocks Run Investigation) --
    if draft.claim_status == "DENIED" and draft.denial_reason_code == "OUT_OF_NETWORK_PROVIDER":
        if draft.servicing_provider_mode == ProviderMode.TEMPORARY:
            network_status = draft.servicing_network_status
        elif draft.servicing_provider_mode == ProviderMode.EXISTING:
            network_status = _existing_provider_network_status(draft.servicing_existing_provider_id)
        else:
            network_status = None
        if network_status == "in-network":
            issues.append(
                ScenarioIssue(
                    ValidationLevel.WARNING,
                    "Denial reason is OUT_OF_NETWORK_PROVIDER but the servicing provider's own "
                    "recorded network status is in-network -- conflicting evidence.",
                )
            )

    if draft.denial_reason_code == "AUTH_REQUIRED" and draft.benefit_available and not draft.benefit_requires_prior_auth:
        issues.append(
            ScenarioIssue(
                ValidationLevel.WARNING,
                "Denial reason is AUTH_REQUIRED but the benefit rule says requires_prior_auth=False -- "
                "conflicting evidence.",
            )
        )

    for index, auth in enumerate(draft.authorizations, start=1):
        if auth.effective_date <= auth.expiration_date and not (
            auth.effective_date <= draft.date_of_service <= auth.expiration_date
        ):
            issues.append(
                ScenarioIssue(
                    ValidationLevel.WARNING,
                    f"Authorization #{index}'s validity window ({auth.effective_date}..{auth.expiration_date}) "
                    f"does not include the date of service ({draft.date_of_service}).",
                )
            )

    if any(i.level == ValidationLevel.ERROR for i in issues):
        overall = ValidationLevel.ERROR
    elif any(i.level == ValidationLevel.WARNING for i in issues):
        overall = ValidationLevel.WARNING
    else:
        overall = ValidationLevel.PASS
    return ScenarioValidationResult(level=overall, issues=issues)


# --- record construction ----------------------------------------------------------------


@dataclass
class ScenarioRecords:
    claim: Claim
    member: Optional[Member]  # None when reusing an EXISTING baseline member (no injection needed)
    benefit: Optional[Benefit]
    servicing_provider: Optional[Provider]
    ordering_provider: Optional[Provider]
    prior_authorizations: list[PriorAuthorization]


def _build_provider(
    mode: ProviderMode,
    existing_id: Optional[str],
    network_status: str,
    provider_type: str,
    prefix: str,
    reserved: set[str],
) -> tuple[Optional[str], Optional[Provider]]:
    if mode == ProviderMode.EXISTING:
        return existing_id, None
    if mode == ProviderMode.TEMPORARY:
        provider_id = _next_id(prefix, reserved)
        return provider_id, Provider(
            provider_id=provider_id,
            name="Scenario Lab Provider",
            provider_type=provider_type,
            network_status=network_status,
        )
    if mode == ProviderMode.UNRESOLVED:
        # A provider_id that is never injected -- resolves to None via the
        # real tools.provider_tool.get_provider, exactly like Case 5's
        # PRV-9999. Never a fabricated/valid provider record.
        return f"{_next_id(prefix, reserved)}-UNRESOLVED", None
    return None, None  # ProviderMode.NONE -- ordering provider only


def build_scenario_records(draft: ScenarioDraft) -> ScenarioRecords:
    """Builds real tools.models instances from the draft. Does not touch
    the DataStore or graph -- see _temporary_data_overlay for that.

    One `reserved` set is threaded through every _next_id() call made
    here so that generating several ids of the same prefix in one call
    (e.g. two authorizations) can never collide with each other, only
    with the DataStore and with ids already handed out earlier in this
    same call -- see _next_id's docstring.
    """
    reserved: set[str] = set()
    claim_id = _next_id(_SCENARIO_CLAIM_PREFIX, reserved)

    if draft.member_mode == MemberMode.EXISTING:
        member_id = draft.existing_member_id
        member_record: Optional[Member] = None
    else:
        member_id = _next_id(_SCENARIO_MEMBER_PREFIX, reserved)
        member_record = Member(
            member_id=member_id,
            first_name="Scenario",
            last_name="Member",
            date_of_birth=date(1990, 1, 1),
            plan_id=draft.plan_id,
            status="ACTIVE",
        )

    benefit_record: Optional[Benefit] = None
    if draft.benefit_available:
        benefit_record = Benefit(
            benefit_id=_next_id(_SCENARIO_BENEFIT_PREFIX, reserved),
            plan_id=draft.plan_id,
            service_code=draft.service_code,
            covered=draft.benefit_covered,
            requires_prior_auth=draft.benefit_requires_prior_auth,
            network_requirement=draft.benefit_network_requirement or None,
        )

    servicing_provider_id, servicing_provider_record = _build_provider(
        draft.servicing_provider_mode,
        draft.servicing_existing_provider_id,
        draft.servicing_network_status,
        draft.servicing_provider_type,
        _SCENARIO_PROVIDER_PREFIX,
        reserved,
    )
    ordering_provider_id, ordering_provider_record = _build_provider(
        draft.ordering_provider_mode,
        draft.ordering_existing_provider_id,
        draft.ordering_network_status,
        draft.ordering_provider_type,
        _SCENARIO_PROVIDER_PREFIX,
        reserved,
    )

    auth_records: list[PriorAuthorization] = []
    for auth_draft in draft.authorizations:
        auth_records.append(
            PriorAuthorization(
                authorization_id=_next_id(_SCENARIO_AUTH_PREFIX, reserved),
                member_id=member_id,
                service_code=draft.service_code,
                plan_id=draft.plan_id,
                status=auth_draft.status,
                effective_date=auth_draft.effective_date,
                expiration_date=auth_draft.expiration_date,
                notes=auth_draft.notes or None,
            )
        )

    claim_record = Claim(
        claim_id=claim_id,
        member_id=member_id,
        plan_id=draft.plan_id,
        service_code=draft.service_code,
        provider_id=servicing_provider_id,
        date_of_service=draft.date_of_service,
        status=draft.claim_status,
        billed_amount=draft.billed_amount,
        ordering_provider_id=ordering_provider_id,
        allowed_amount=draft.allowed_amount,
        denial_reason_code=draft.denial_reason_code or None,
        denial_reason_description=draft.denial_reason_description or None,
    )

    return ScenarioRecords(
        claim=claim_record,
        member=member_record,
        benefit=benefit_record,
        servicing_provider=servicing_provider_record,
        ordering_provider=ordering_provider_record,
        prior_authorizations=auth_records,
    )


# --- in-memory overlay -------------------------------------------------------------------


@contextmanager
def _temporary_data_overlay(records: ScenarioRecords):
    """Temporarily overlay the scenario's records onto the process-wide
    DataStore/graph singletons, and restore the store to a state
    EQUIVALENT to its pre-scenario state in `finally` -- including when a
    baseline record already occupied the same (plan_id, service_code)
    benefit key or the same (member_id, service_code) authorization group.

    Scoped-replacement semantics (see docs/SCENARIO_LAB.md): Scenario Lab
    defines the scoped structured evidence visible to the scenario claim
    for the fields it controls, for the exact duration of one run --

      - Benefit: if the draft supplies a benefit (records.benefit is not
        None), it REPLACES whatever occupies (plan_id, service_code) for
        the duration of the run. If the draft supplies no benefit
        (records.benefit is None, i.e. benefit_available=False), that key
        resolves to no benefit at all during the run -- even if a
        baseline benefit already lives there. Either way, the baseline
        value (a benefit, or the absence of one) is restored exactly
        afterward.
      - Prior authorizations: the scenario's authorization list (zero,
        one, or many rows) is the COMPLETE set visible for
        (member_id, service_code) during the run. Any preexisting
        baseline authorizations for that exact pair are hidden from BOTH
        the grouped index (tools.prior_auth_tool's read path) and the
        flat `prior_authorizations` dict (graph/builder.py iterates this
        directly, not the grouped index -- see module docstring) for the
        duration of the run, so structured evidence and the graph can
        never disagree about which authorizations exist. The baseline
        group and every baseline record it referenced are restored
        exactly afterward.

    Restoration happens in `finally`, so it runs after a successful
    investigation, after a raised exception, and regardless of
    generation success/failure.
    """
    store = get_data_store()

    added_member = False
    added_provider_ids: list[str] = []

    # --- benefit: snapshot the EXACT pre-scenario state at this key, present or absent
    benefit_key = (records.claim.plan_id, records.claim.service_code)
    benefit_key_existed = benefit_key in store.benefits
    original_benefit = store.benefits.get(benefit_key)

    # --- authorizations: snapshot the EXACT pre-scenario group and every
    # record it referenced in the flat dict, so both can be restored
    # verbatim regardless of what the scenario inserts.
    auth_key = (records.claim.member_id, records.claim.service_code)
    original_auth_group = list(store.prior_authorizations_by_member_service.get(auth_key, []))
    original_auth_records: dict[str, PriorAuthorization] = {
        auth.authorization_id: store.prior_authorizations[auth.authorization_id]
        for auth in original_auth_group
        if auth.authorization_id in store.prior_authorizations
    }
    scenario_auth_ids = [auth.authorization_id for auth in records.prior_authorizations]

    try:
        store.claims[records.claim.claim_id] = records.claim

        if records.member is not None:
            store.members[records.member.member_id] = records.member
            added_member = True

        # Benefit: replace-or-hide at benefit_key regardless of whether a
        # baseline record already lived there (scoped-replacement view).
        if records.benefit is not None:
            store.benefits[benefit_key] = records.benefit
        else:
            store.benefits.pop(benefit_key, None)

        for provider in (records.servicing_provider, records.ordering_provider):
            if provider is not None:
                store.providers[provider.provider_id] = provider
                added_provider_ids.append(provider.provider_id)

        # Prior authorizations: hide every baseline record for this exact
        # (member_id, service_code) pair from BOTH stores, then insert
        # only the scenario's own records (zero, one, or many).
        for auth_id in original_auth_records:
            store.prior_authorizations.pop(auth_id, None)
        store.prior_authorizations_by_member_service.pop(auth_key, None)

        for auth in records.prior_authorizations:
            store.prior_authorizations[auth.authorization_id] = auth
        if records.prior_authorizations:
            store.prior_authorizations_by_member_service[auth_key] = list(records.prior_authorizations)

        # The graph must be rebuilt to include the scenario's nodes (and
        # exclude any hidden baseline authorizations) -- see module
        # docstring. .cache_clear() is functools.lru_cache's own public
        # API; no graph/retriever.py code is modified.
        _get_graph.cache_clear()

        yield
    finally:
        store.claims.pop(records.claim.claim_id, None)
        if added_member and records.member is not None:
            store.members.pop(records.member.member_id, None)

        if benefit_key_existed:
            store.benefits[benefit_key] = original_benefit
        else:
            store.benefits.pop(benefit_key, None)

        for provider_id in added_provider_ids:
            store.providers.pop(provider_id, None)

        for auth_id in scenario_auth_ids:
            store.prior_authorizations.pop(auth_id, None)
        store.prior_authorizations_by_member_service.pop(auth_key, None)
        for auth_id, auth in original_auth_records.items():
            store.prior_authorizations[auth_id] = auth
        if original_auth_group:
            store.prior_authorizations_by_member_service[auth_key] = original_auth_group

        # Restore the graph to baseline-only state before any other caller
        # (e.g. the Predefined Claims tab) runs again.
        _get_graph.cache_clear()


# --- entry point -------------------------------------------------------------------------


class ScenarioBlockedError(RuntimeError):
    """Raised by run_scenario_investigation when the scenario has a
    structural ERROR-level issue -- Run Investigation must not be reachable
    in the UI in that state; this is a defensive backstop."""


def run_scenario_investigation(
    draft: ScenarioDraft, *, adapter: Optional[BriefAdapter] = None
) -> tuple[ApplicationResult, ScenarioRecords, ScenarioValidationResult]:
    """Validate, build, temporarily overlay, and run ONE scenario through
    the real, unmodified application.investigation_service.run_investigation
    -- the exact same function the Predefined Claims tab calls. Returns
    the ApplicationResult (self-contained; safe to render after the
    overlay is torn down), the records actually used, and the validation
    result. Raises ScenarioBlockedError if the scenario has a structural
    ERROR (callers -- app.py -- must not offer Run Investigation in that
    state in the first place; this is defense in depth, not the primary
    gate).
    """
    validation = validate_scenario(draft)
    if validation.level == ValidationLevel.ERROR:
        raise ScenarioBlockedError(
            "Cannot run investigation: the scenario has one or more structural ERROR-level issues."
        )

    records = build_scenario_records(draft)
    with _temporary_data_overlay(records):
        result = run_investigation(records.claim.claim_id, draft.question, adapter=adapter)
    return result, records, validation


# --- Streamlit session-state helpers -----------------------------------------------------
#
# Deliberately separate keys from application/workbench.py's Predefined-
# Claims state (selected_claim_id / question_text / investigation_result /
# review_decision / judge_result) -- Scenario Lab and Predefined Claims
# must never read or clear each other's state (H7 requirement M). These
# are plain functions over a MutableMapping (st.session_state in
# production, a plain dict in tests), the same convention as
# application/workbench.py.

SCENARIO_DRAFT_KEY = "scenario_draft"
SCENARIO_RESULT_KEY = "scenario_result"
SCENARIO_RECORDS_KEY = "scenario_records"
SCENARIO_VALIDATION_KEY = "scenario_validation"
SCENARIO_REVIEW_DECISION_KEY = "scenario_review_decision"
SCENARIO_JUDGE_RESULT_KEY = "scenario_judge_result"


def get_or_create_draft(state: MutableMapping) -> ScenarioDraft:
    if SCENARIO_DRAFT_KEY not in state:
        state[SCENARIO_DRAFT_KEY] = ScenarioDraft()
    return state[SCENARIO_DRAFT_KEY]


def reset_scenario_investigation_state(state: MutableMapping) -> None:
    """Clear any previous scenario investigation result, its validation
    snapshot, review decision, and H6 judge result -- WITHOUT touching the
    draft/template itself. Called whenever any scenario-defining field
    changes (H7 requirement: stale-state protection)."""
    state.pop(SCENARIO_RESULT_KEY, None)
    state.pop(SCENARIO_RECORDS_KEY, None)
    state.pop(SCENARIO_VALIDATION_KEY, None)
    state.pop(SCENARIO_REVIEW_DECISION_KEY, None)
    state.pop(SCENARIO_JUDGE_RESULT_KEY, None)


def apply_template_change(state: MutableMapping, template_name: str) -> None:
    """Rebuild the draft from a template and clear any stale investigation
    result -- template changes are scenario-defining changes too."""
    state[SCENARIO_DRAFT_KEY] = build_template_draft(template_name)
    reset_scenario_investigation_state(state)


def reset_scenario_lab(state: MutableMapping) -> None:
    """'Reset Scenario' -- clears the draft itself plus all investigation
    state. Affects ONLY Scenario Lab's own namespaced keys; never touches
    application/workbench.py's Predefined Claims keys."""
    state.pop(SCENARIO_DRAFT_KEY, None)
    reset_scenario_investigation_state(state)
