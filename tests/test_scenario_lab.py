"""H7 (OPTIONAL) tests for the Scenario Lab. Fully offline: every
investigation-running test uses FakeInvestigationBriefAdapter, no live
model call anywhere in this file.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from agents.case_agent import run_case_agent
from application.llm_adapter import FakeInvestigationBriefAdapter
from application.models import ActionCode, Finding, InvestigationBrief, SuggestedNextStep
from application.scenario_lab import (
    TEMPLATE_NAMES,
    AuthorizationDraft,
    MemberMode,
    ProviderMode,
    ScenarioBlockedError,
    ScenarioDraft,
    ValidationLevel,
    apply_template_change,
    build_scenario_records,
    build_template_draft,
    reset_scenario_investigation_state,
    reset_scenario_lab,
    run_scenario_investigation,
    validate_scenario,
)
from tools.case_context import get_case_context
from tools.data_store import get_data_store

ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [
    "members.json", "plans.json", "claims.json",
    "benefits.json", "prior_authorizations.json", "providers.json",
]


def _data_hashes() -> dict[str, str]:
    return {f: hashlib.sha256((ROOT / "data" / f).read_bytes()).hexdigest() for f in DATA_FILES}


def _well_formed_brief(*evidence_refs: str) -> InvestigationBrief:
    refs = list(evidence_refs)
    return InvestigationBrief(
        summary="Test summary.",
        findings=[Finding(statement="A well-formed finding.", evidence_refs=refs)],
        missing_or_conflicting_evidence=[],
        suggested_next_step=SuggestedNextStep(
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS, rationale="Test rationale.", evidence_refs=refs
        ),
    )


# --- A. Existing Predefined Claims workflow remains unchanged -------------------------


def test_a_predefined_claims_workflow_unchanged():
    for claim_id, expected_status in [
        ("CLM-1001", "EVIDENCE_SUFFICIENT"),
        ("CLM-1002", "EVIDENCE_SUFFICIENT"),
        ("CLM-1003", "EVIDENCE_SUFFICIENT"),
        ("CLM-1004", "EVIDENCE_SUFFICIENT"),
        ("CLM-1005", "NEEDS_REVIEW"),
    ]:
        result = run_case_agent(claim_id, "Why was this claim denied?")
        assert result.status.value == expected_status


# --- B. Valid custom scenario can be created and validated -----------------------------


def test_b_valid_custom_scenario_validates_as_pass():
    draft = ScenarioDraft(
        claim_status="DENIED",
        denial_reason_code="AUTH_REQUIRED",
        service_code="SCN-CUSTOM-1",
        benefit_available=True,
        benefit_requires_prior_auth=True,
        authorizations=[],
    )
    validation = validate_scenario(draft)
    assert validation.level == ValidationLevel.PASS
    assert validation.issues == []


def test_b_all_templates_are_constructible_and_never_error():
    for name in TEMPLATE_NAMES:
        draft = build_template_draft(name)
        validation = validate_scenario(draft)
        assert validation.level != ValidationLevel.ERROR, f"template {name!r} should not be structurally invalid"


# --- C. Scenario Lab does not modify data/*.json ----------------------------------------


def test_c_scenario_investigation_does_not_modify_source_data():
    before = _data_hashes()
    draft = build_template_draft("AUTH_REQUIRED — no authorization record")
    run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())
    after = _data_hashes()
    assert before == after


# --- D. Expired-auth scenario ------------------------------------------------------------


def test_d_expired_authorization_processed_without_hardcoded_answer():
    draft = build_template_draft("AUTH_REQUIRED — expired authorization")
    assert draft.authorizations[0].status == "EXPIRED"

    result, records, validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    # The pipeline (not this test) determines sufficiency -- prior_authorization
    # absence/expiry is non-blocking, exactly like the baseline Case-2 rule.
    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"
    sf = result.evidence_package.structured_facts
    assert len(sf.prior_authorizations) == 1
    assert sf.prior_authorizations[0].status == "EXPIRED"
    # Validator flags the DOS/validity mismatch as a WARNING (informational), not an error.
    assert any("validity window" in i.message for i in validation.issues)
    assert validation.level in (ValidationLevel.PASS, ValidationLevel.WARNING)


# --- E. Multiple authorization candidates -------------------------------------------------


def test_e_multiple_authorization_candidates_all_preserved():
    draft = build_template_draft("AUTH_REQUIRED — multiple authorization candidates")
    result, records, validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    sf = result.evidence_package.structured_facts
    assert len(sf.prior_authorizations) == 2
    statuses = {a.status for a in sf.prior_authorizations}
    assert statuses == {"EXPIRED", "APPROVED"}

    context = result.assembled_context
    auth_refs = [r for r in context.references if r.kind == "authorization"]
    assert len(auth_refs) == 2
    for ref in auth_refs:
        assert "applicable" not in ref.label.lower()
        assert "applicable" not in ref.detail.lower()


# --- F. covered=false --------------------------------------------------------------------


def test_f_benefit_not_covered_is_explicit_not_missing():
    draft = build_template_draft("Benefit not covered")
    result, records, validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    sf = result.evidence_package.structured_facts
    assert sf.benefit is not None
    assert sf.benefit.covered is False
    assert "benefit" not in result.agent_result.missing_information
    assert result.agent_result.status.value == "EVIDENCE_SUFFICIENT"


# --- G. Provider unresolved ----------------------------------------------------------------


def test_g_missing_provider_handled_through_existing_pipeline_no_fabrication():
    draft = build_template_draft("Missing provider")
    result, records, validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    sf = result.evidence_package.structured_facts
    assert sf.servicing_provider is None
    assert "servicing_provider" in result.agent_result.missing_information
    # Because benefit is also unavailable in this template, evidence should be insufficient.
    assert result.agent_result.status.value == "NEEDS_REVIEW"
    assert result.generation_status.value == "NOT_ATTEMPTED"


# --- H. Intentional conflicting evidence -------------------------------------------------


def test_h_conflicting_evidence_is_warning_not_error_and_pipeline_still_runs():
    draft = build_template_draft("Conflicting claim/provider evidence")
    validation = validate_scenario(draft)
    assert validation.level == ValidationLevel.WARNING
    assert any("in-network" in i.message for i in validation.issues)

    # Pipeline still runs -- a WARNING never blocks Run Investigation.
    result, records, _ = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())
    assert result.agent_result.status.value in ("EVIDENCE_SUFFICIENT", "NEEDS_REVIEW")


# --- I. Structural invalid scenario --------------------------------------------------------


def test_i_structural_error_blocks_run_investigation():
    draft = ScenarioDraft(service_code="")  # missing required field
    validation = validate_scenario(draft)
    assert validation.level == ValidationLevel.ERROR

    with pytest.raises(ScenarioBlockedError):
        run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())


def test_i_invalid_auth_dates_are_structural_error():
    draft = ScenarioDraft(
        authorizations=[AuthorizationDraft(effective_date=date(2026, 6, 30), expiration_date=date(2026, 1, 1))]
    )
    validation = validate_scenario(draft)
    assert validation.level == ValidationLevel.ERROR


# --- J/K. Field / template change clears stale result -------------------------------------


def test_j_scenario_field_change_clears_stale_result():
    state: dict = {}
    state["scenario_result"] = "stale"
    state["scenario_judge_result"] = "stale"
    reset_scenario_investigation_state(state)
    assert "scenario_result" not in state
    assert "scenario_judge_result" not in state


def test_k_template_change_clears_stale_result():
    state: dict = {}
    state["scenario_result"] = "stale"
    state["scenario_review_decision"] = "stale"
    apply_template_change(state, "Benefit not covered")
    assert "scenario_result" not in state
    assert "scenario_review_decision" not in state
    assert state["scenario_draft"].template == "Benefit not covered"


# --- L. Reset Scenario ----------------------------------------------------------------------


def test_l_reset_scenario_clears_everything_but_not_predefined_claims_state():
    state: dict = {
        "scenario_draft": ScenarioDraft(),
        "scenario_result": "stale",
        "scenario_review_decision": "stale",
        "scenario_judge_result": "stale",
        # Predefined-Claims-namespaced keys, must be untouched.
        "selected_claim_id": "CLM-1001",
        "investigation_result": "predefined-result",
        "review_decision": "predefined-decision",
    }
    reset_scenario_lab(state)

    assert "scenario_draft" not in state
    assert "scenario_result" not in state
    assert "scenario_review_decision" not in state
    assert "scenario_judge_result" not in state
    # Predefined Claims state is completely unaffected.
    assert state["selected_claim_id"] == "CLM-1001"
    assert state["investigation_result"] == "predefined-result"
    assert state["review_decision"] == "predefined-decision"


# --- M. Temporary Scenario data cannot be accessed from Predefined Claims -----------------


def test_m_scenario_claim_not_accessible_after_run_completes():
    draft = build_template_draft("AUTH_REQUIRED — no authorization record")
    result, records, _ = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    # The scenario claim must not be resolvable through the normal tools
    # layer once the investigation has completed -- it never leaked into
    # the shared DataStore permanently.
    context = get_case_context(records.claim.claim_id)
    assert context.claim is None

    # The claim_id is also absent from the raw DataStore.
    assert records.claim.claim_id not in get_data_store().claims


def test_m_scenario_run_does_not_disturb_predefined_claim_lookup():
    draft = build_template_draft("Out-of-network provider")
    run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    # A predefined claim must still resolve correctly after a scenario ran.
    predefined_context = get_case_context("CLM-1004")
    assert predefined_context.claim is not None
    assert predefined_context.claim.status == "DENIED"
    assert predefined_context.claim.denial_reason_code == "OUT_OF_NETWORK_PROVIDER"


# --- N. H6 semantic judge: optional, not auto-invoked, clears on scenario change ----------


def test_n_semantic_judge_not_automatically_invoked_for_scenario_result():
    draft = build_template_draft("Custom")
    draft.service_code = "SCN-JUDGE-TEST"
    result, records, _ = run_scenario_investigation(
        draft, adapter=FakeInvestigationBriefAdapter(response=_well_formed_brief("claim:PLACEHOLDER"))
    )
    assert result.generation_status.value == "DRAFTED"
    # No judge call is made anywhere in run_scenario_investigation -- confirmed
    # structurally: the module never imports application.semantic_judge at all.
    import application.scenario_lab as scenario_lab_module

    assert not hasattr(scenario_lab_module, "run_semantic_judge")


def test_n_scenario_judge_result_clears_on_field_and_template_change():
    state: dict = {"scenario_judge_result": "stale-judge"}
    reset_scenario_investigation_state(state)
    assert "scenario_judge_result" not in state

    state2: dict = {"scenario_draft": ScenarioDraft(), "scenario_judge_result": "stale-judge"}
    apply_template_change(state2, "Custom")
    assert "scenario_judge_result" not in state2


# --- O. Existing H0-H6 regression (spot check; full suite run separately) ----------------


def test_o_scenario_lab_module_presence_does_not_affect_baseline_evals_inputs():
    """Importing application.scenario_lab must not change any baseline
    lookup behavior -- spot check a few tool-level facts unaffected by
    this module simply being imported."""
    from tools.claim_tool import get_claim
    from tools.prior_auth_tool import get_prior_authorizations

    claim = get_claim("CLM-1002")
    assert claim is not None and claim.status == "PAID"
    auths = get_prior_authorizations("M-1002", "MRI-KNEE")
    assert {a.authorization_id for a in auths} == {"PA-1501", "PA-2001"}


# --- P. Post-audit remediation: collision restoration / scoped-replacement semantics -----
#
# Each test below exercises a defect an independent source-code audit found in
# claims-copilot-final-v1's `_temporary_data_overlay`: it treated any baseline
# record occupying the same benefit/authorization key as something to DELETE
# on cleanup rather than something to RESTORE (a benefit collision would have
# permanently deleted the baseline BEN-GOLD-MRI-KNEE benefit for the rest of
# the process), and it only hid baseline authorizations from the grouped
# index tools.prior_auth_tool reads -- never from the flat
# `prior_authorizations` dict graph/builder.py iterates directly, so the
# graph could show a different authorization set than structured facts did.
# These tests would have FAILED against claims-copilot-final-v1.

BASELINE_BENEFIT_KEY = ("PLAN-GOLD", "MRI-KNEE")
BASELINE_AUTH_MEMBER_SERVICE = ("M-1002", "MRI-KNEE")


def _snapshot_store_state() -> dict:
    """An IN-MEMORY snapshot of every DataStore structure Scenario Lab's
    overlay can touch -- stronger than comparing data/*.json file hashes
    (test_c above), since it catches a defect that corrupts the live
    process-wide singleton without ever touching a file on disk."""
    store = get_data_store()
    return {
        "claims": dict(store.claims),
        "members": dict(store.members),
        "benefits": dict(store.benefits),
        "providers": dict(store.providers),
        "prior_authorizations": dict(store.prior_authorizations),
        "prior_authorizations_by_member_service": {
            key: list(value) for key, value in store.prior_authorizations_by_member_service.items()
        },
    }


def test_p_benefit_collision_restores_exact_baseline_value():
    store = get_data_store()
    original_benefit = store.benefits.get(BASELINE_BENEFIT_KEY)
    assert original_benefit is not None, "fixture assumption: PLAN-GOLD/MRI-KNEE has a baseline benefit"
    assert original_benefit.covered is True

    draft = ScenarioDraft(
        plan_id="PLAN-GOLD",
        service_code="MRI-KNEE",
        benefit_available=True,
        benefit_covered=False,  # deliberately different from the baseline (covered=True)
    )
    result, records, validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())
    assert validation.level in (ValidationLevel.PASS, ValidationLevel.WARNING)

    # DURING (materialized in the returned evidence): the scenario's OWN
    # benefit is what the pipeline actually saw, not the baseline one.
    seen_benefit = result.evidence_package.structured_facts.benefit
    assert seen_benefit is not None
    assert seen_benefit.benefit_id == records.benefit.benefit_id
    assert seen_benefit.covered is False

    # AFTER: the exact original baseline benefit object is restored -- not
    # deleted, not replaced with a copy.
    restored = store.benefits.get(BASELINE_BENEFIT_KEY)
    assert restored is original_benefit
    assert restored.covered is True


def test_p_benefit_available_false_hides_colliding_baseline_benefit_during_run():
    store = get_data_store()
    original_benefit = store.benefits.get(BASELINE_BENEFIT_KEY)
    assert original_benefit is not None

    draft = ScenarioDraft(plan_id="PLAN-GOLD", service_code="MRI-KNEE", benefit_available=False)
    result, _records, _validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    # DURING: no benefit was visible to the pipeline at all, even though a
    # baseline benefit exists at this exact (plan_id, service_code) key.
    assert result.evidence_package.structured_facts.benefit is None

    # AFTER: baseline benefit restored, unchanged.
    restored = store.benefits.get(BASELINE_BENEFIT_KEY)
    assert restored is original_benefit
    assert restored.covered is True


def test_p_existing_member_zero_authorizations_hides_baseline_authorizations_everywhere():
    store = get_data_store()
    original_group = list(store.prior_authorizations_by_member_service.get(BASELINE_AUTH_MEMBER_SERVICE, []))
    assert {a.authorization_id for a in original_group} == {"PA-1501", "PA-2001"}

    draft = ScenarioDraft(
        service_code="MRI-KNEE",
        member_mode=MemberMode.EXISTING,
        existing_member_id="M-1002",
        authorizations=[],
    )
    result, _records, _validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    # DURING: structured facts show zero authorizations for this pair.
    assert result.evidence_package.structured_facts.prior_authorizations == []

    # DURING: neither baseline record leaked through the graph or the
    # assembled evidence references -- structured facts and the graph must
    # agree, since graph/builder.py iterates the flat prior_authorizations
    # dict directly rather than the grouped index.
    from application.context_assembler import assemble_context

    context = assemble_context(result.agent_result)
    ref_ids = {ref.ref_id for ref in context.references}
    assert "authorization:PA-1501" not in ref_ids
    assert "authorization:PA-2001" not in ref_ids

    graph_node_ids = {rel.source_id for rel in result.evidence_package.graph_relationships} | {
        rel.target_id for rel in result.evidence_package.graph_relationships
    }
    assert "authorization:PA-1501" not in graph_node_ids
    assert "authorization:PA-2001" not in graph_node_ids

    # AFTER: original baseline group and records restored exactly.
    restored_group = store.prior_authorizations_by_member_service.get(BASELINE_AUTH_MEMBER_SERVICE, [])
    assert {a.authorization_id for a in restored_group} == {"PA-1501", "PA-2001"}
    assert "PA-1501" in store.prior_authorizations
    assert "PA-2001" in store.prior_authorizations


def test_p_existing_member_scenario_authorization_replaces_baseline_authorizations():
    store = get_data_store()
    draft = ScenarioDraft(
        service_code="MRI-KNEE",
        member_mode=MemberMode.EXISTING,
        existing_member_id="M-1002",
        authorizations=[
            AuthorizationDraft(
                status="APPROVED", effective_date=date(2026, 1, 1), expiration_date=date(2026, 12, 31)
            )
        ],
    )
    result, records, _validation = run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    scenario_auth_id = records.prior_authorizations[0].authorization_id

    # DURING: exactly the scenario authorization is visible -- neither
    # baseline record is.
    seen_ids = {a.authorization_id for a in result.evidence_package.structured_facts.prior_authorizations}
    assert seen_ids == {scenario_auth_id}

    graph_node_ids = {rel.source_id for rel in result.evidence_package.graph_relationships} | {
        rel.target_id for rel in result.evidence_package.graph_relationships
    }
    assert f"authorization:{scenario_auth_id}" in graph_node_ids
    assert "authorization:PA-1501" not in graph_node_ids
    assert "authorization:PA-2001" not in graph_node_ids

    # AFTER: original baseline authorization set restored exactly; the
    # scenario's own record is gone.
    restored_group = store.prior_authorizations_by_member_service.get(BASELINE_AUTH_MEMBER_SERVICE, [])
    assert {a.authorization_id for a in restored_group} == {"PA-1501", "PA-2001"}
    assert scenario_auth_id not in store.prior_authorizations


def test_p_exception_inside_overlay_still_restores_everything():
    """Restoration must survive an exception raised INSIDE the overlay --
    after a successful investigation is not the only path that must clean
    up correctly."""
    from application.scenario_lab import _temporary_data_overlay

    store = get_data_store()
    original_benefit = store.benefits.get(BASELINE_BENEFIT_KEY)
    original_auth_ids = {
        a.authorization_id for a in store.prior_authorizations_by_member_service.get(BASELINE_AUTH_MEMBER_SERVICE, [])
    }
    before_snapshot = _snapshot_store_state()

    draft = ScenarioDraft(
        plan_id="PLAN-GOLD",
        service_code="MRI-KNEE",
        member_mode=MemberMode.EXISTING,
        existing_member_id="M-1002",
        benefit_available=True,
        authorizations=[AuthorizationDraft()],
    )
    records = build_scenario_records(draft)

    class _SimulatedFailure(RuntimeError):
        pass

    with pytest.raises(_SimulatedFailure):
        with _temporary_data_overlay(records):
            # Confirm the overlay actually applied before raising, so this
            # test would fail loudly if the collision logic silently no-ops.
            assert store.benefits.get(BASELINE_BENEFIT_KEY) is records.benefit
            assert original_auth_ids.isdisjoint(store.prior_authorizations)
            raise _SimulatedFailure("simulated failure mid-investigation")

    after_snapshot = _snapshot_store_state()
    assert after_snapshot == before_snapshot
    assert store.benefits.get(BASELINE_BENEFIT_KEY) is original_benefit

    # The graph cache was cleared in the `finally` block too -- a normal
    # Predefined Claims investigation must still resolve correctly right
    # after an exception mid-scenario.
    predefined_result = run_case_agent("CLM-1002", "Was this claim paid correctly?")
    assert predefined_result.status.value == "EVIDENCE_SUFFICIENT"


def test_p_in_memory_datastore_snapshot_equivalent_after_multiple_collision_scenarios():
    """Stronger than test_c's SHA-256 hash of data/*.json: asserts the
    actual in-memory DataStore singleton -- not just the files on disk --
    is equivalent before and after running several scenarios back to
    back, including ones that deliberately collide with baseline
    benefit/authorization keys."""
    before = _snapshot_store_state()

    scenarios = [
        ScenarioDraft(template="Custom"),
        ScenarioDraft(plan_id="PLAN-GOLD", service_code="MRI-KNEE", benefit_available=True, benefit_covered=False),
        ScenarioDraft(plan_id="PLAN-GOLD", service_code="MRI-KNEE", benefit_available=False),
        ScenarioDraft(
            service_code="MRI-KNEE", member_mode=MemberMode.EXISTING, existing_member_id="M-1002", authorizations=[]
        ),
        ScenarioDraft(
            service_code="MRI-KNEE",
            member_mode=MemberMode.EXISTING,
            existing_member_id="M-1002",
            authorizations=[AuthorizationDraft()],
        ),
    ]
    for draft in scenarios:
        run_scenario_investigation(draft, adapter=FakeInvestigationBriefAdapter())

    after = _snapshot_store_state()
    assert after == before
