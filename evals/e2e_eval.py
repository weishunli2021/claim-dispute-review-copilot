"""H4: the 8 end-to-end product scenarios (E01-E08).

This is a PRODUCT-BEHAVIOR evaluation, distinct from and never combined
with the component evaluations in evals/skill_eval.py, evals/agent_eval.py,
evals/hybrid_*_eval.py, or evals/retrieval_eval.py, and from the safety/
failure tests in evals/e2e_safety_eval.py. Each scenario is scored as an
explicit list of individually-inspectable checks, never one opaque score.

Reproducibility: every scenario here uses a MOCKED adapter
(application.llm_adapter.FakeInvestigationBriefAdapter) so this suite never
makes a network call and always produces the same result. The mocked
briefs still go through the REAL application.investigation_service
orchestration, the REAL H2 deterministic validator
(application.brief_validator), and (for E05) the REAL
application.review_packet packaging -- nothing here bypasses H1/H2 to make
H4 simpler. Live-model behavior was already verified separately in H1
(LIVE_VERIFIED) and H3 (one live UI smoke test); this suite's job is
reproducible product-behavior evaluation, not re-proving that.

Evaluation-harness note: each generation scenario (E01-E04, E07) calls the
existing deterministic evidence workflow (agents.case_agent.run_case_agent)
ONCE to discover the real evidence reference ids for that request (so the
mocked brief this script hands to the adapter cites real, current ref ids
rather than guessed/hardcoded ones -- retrieval is deterministic per query
but its exact top-k policy chunk ids are not hand-verified here), and then
calls application.investigation_service.run_investigation (the real
product path) to produce the actual scored ApplicationResult, which
internally re-runs the same deterministic evidence workflow once more.
This two-call pattern is an EVALUATION-SCRIPT characteristic only -- it
does not affect or weaken the production single-execution guarantee, which
is independently regression-tested in
tests/test_application_investigation_service.py::
test_run_investigation_executes_evidence_workflow_exactly_once.

Run with:

    python -m evals.e2e_eval
"""

from __future__ import annotations

import sys

from agents.case_agent import run_case_agent
from application.context_assembler import assemble_context
from application.investigation_service import run_investigation
from application.llm_adapter import FakeInvestigationBriefAdapter
from application.models import ActionCode, AssembledContext, GenerationStatus
from application.review_packet import build_needs_review_packet
from evals.e2e_common import (
    ARTIFACTS_DIR,
    Check,
    ScenarioResult,
    print_scenario_report,
    well_formed_brief,
    write_artifact,
)


def _discover_context(claim_id: str, query: str) -> AssembledContext:
    """Run the real evidence workflow once, standalone, to learn the
    actual reference ids available for this claim/query -- so the mocked
    brief this eval hands to the adapter cites real ids, never guessed
    ones. See module docstring's "Evaluation-harness note."
    """
    agent_result = run_case_agent(claim_id, query)
    return assemble_context(agent_result)


def _first_ref(context: AssembledContext, prefix: str) -> str | None:
    return next((r.ref_id for r in context.references if r.ref_id.startswith(prefix)), None)


# --- E01 -----------------------------------------------------------------------------


def run_e01() -> ScenarioResult:
    claim_id, query = "CLM-1001", "Why was this claim denied, and what evidence is available?"
    context = _discover_context(claim_id, query)
    claim_ref = _first_ref(context, "claim:")
    benefit_ref = _first_ref(context, "benefit:")
    refs = [r for r in (claim_ref, benefit_ref) if r]

    brief = well_formed_brief(
        summary="The claim is recorded as DENIED for AUTH_REQUIRED; no authorization record was found in the available evidence.",
        findings=[
            ("SOURCE FACT: claim recorded DENIED / AUTH_REQUIRED.", [claim_ref]),
            ("SOURCE FACT: benefit requires prior authorization.", [benefit_ref] if benefit_ref else []),
            (
                "INTERPRETATION: the absence of an authorization record does not by itself "
                "prove the denial was correct or valid.",
                refs,
            ),
        ],
        action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
        rationale="Verify whether a prior-authorization record exists.",
        next_step_refs=refs,
        missing=["No prior-authorization record is on file."],
    )
    adapter = FakeInvestigationBriefAdapter(response=brief)
    result = run_investigation(claim_id, query, adapter=adapter)

    sf = result.evidence_package.structured_facts if result.evidence_package else None
    claim_status = sf.claim.status if sf and sf.claim else None
    denial_reason = sf.claim.denial_reason_code if sf and sf.claim else None
    auth_count = len(sf.prior_authorizations) if sf else None

    checks = [
        Check.equals("evidence_status", result.agent_result.status.value, "EVIDENCE_SUFFICIENT"),
        Check.equals("generation_attempted", result.generation_status != GenerationStatus.NOT_ATTEMPTED, True),
        Check.equals("structured_brief_produced", result.investigation_brief is not None, True),
        Check.equals("validation_passed", result.validation_result.status.value, "PASSED"),
        Check.equals("claim_status_denied", claim_status, "DENIED"),
        Check.equals("denial_reason_auth_required", denial_reason, "AUTH_REQUIRED"),
        Check.equals("authorization_count_zero", auth_count, 0),
        Check(
            "no_conclusive_validity_claim",
            "conclusively" not in brief.summary.lower() and "proves the denial" not in brief.summary.lower(),
            brief.summary,
            "must not claim absence conclusively proves validity",
        ),
        Check(
            "no_adjudicative_authority_claim",
            brief.suggested_next_step.action_code
            in (
                ActionCode.EXPLAIN_RECORDED_STATUS,
                ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
                ActionCode.REQUEST_INFORMATION,
                ActionCode.HUMAN_REVIEW,
            ),
            brief.suggested_next_step.action_code.value,
            "one of the four approved advisory action codes",
        ),
    ]
    return ScenarioResult("E01", "CLM-1001 denial explanation with zero authorization records", checks)


# --- E02 -----------------------------------------------------------------------------


def run_e02() -> ScenarioResult:
    claim_id, query = "CLM-1002", "Was this claim paid correctly, and is there an approved authorization on file?"
    context = _discover_context(claim_id, query)
    auth_refs = {r.ref_id: r for r in context.references if r.kind == "authorization"}
    claim_ref = _first_ref(context, "claim:")

    brief = well_formed_brief(
        summary="The claim is recorded as PAID. Two prior-authorization candidates exist; the supplied evidence does not determine which one, if any, applied to this claim.",
        findings=[
            ("SOURCE FACT: claim recorded PAID.", [claim_ref]),
            (
                "SOURCE FACT: two authorization candidates exist (one EXPIRED, one APPROVED); "
                "nothing in the supplied evidence matches either to this claim's date of service.",
                list(auth_refs.keys()),
            ),
        ],
        action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
        rationale="Explain the recorded paid status and present both authorization candidates without declaring one applicable.",
        next_step_refs=[claim_ref] if claim_ref else [],
    )
    adapter = FakeInvestigationBriefAdapter(response=brief)
    result = run_investigation(claim_id, query, adapter=adapter)

    sf = result.evidence_package.structured_facts if result.evidence_package else None
    pa_by_id = {a.authorization_id: a for a in sf.prior_authorizations} if sf else {}
    pa_1501_status = pa_by_id["PA-1501"].status if "PA-1501" in pa_by_id else None
    pa_2001_status = pa_by_id["PA-2001"].status if "PA-2001" in pa_by_id else None

    checks = [
        Check.equals("evidence_status", result.agent_result.status.value, "EVIDENCE_SUFFICIENT"),
        Check.equals("claim_status_paid", sf.claim.status if sf and sf.claim else None, "PAID"),
        Check.equals("pa_1501_present_expired", pa_1501_status, "EXPIRED"),
        Check.equals("pa_2001_present_approved", pa_2001_status, "APPROVED"),
        Check.equals("both_candidates_in_assembled_context", set(auth_refs.keys()), {"auth:PA-1501", "auth:PA-2001"}),
        Check(
            "no_reference_declares_one_applicable",
            all("applicable" not in ref.label.lower() and "applicable" not in ref.detail.lower() for ref in auth_refs.values()),
            [f"{r.ref_id}: {r.label} / {r.detail}" for r in auth_refs.values()],
            "no reference text contains the word 'applicable'",
        ),
        Check.equals("validation_passed", result.validation_result.status.value, "PASSED"),
    ]
    return ScenarioResult("E02", "CLM-1002 preserves both authorization candidates, declares neither applicable", checks)


# --- E03 -----------------------------------------------------------------------------


def run_e03() -> ScenarioResult:
    claim_id, query = "CLM-1003", "Why was my lab claim denied?"
    context = _discover_context(claim_id, query)
    claim_ref = _first_ref(context, "claim:")
    benefit_ref = _first_ref(context, "benefit:")

    brief = well_formed_brief(
        summary="The claim is recorded as DENIED because the benefit rule explicitly marks this service as not covered.",
        findings=[
            ("SOURCE FACT: claim recorded DENIED / SERVICE_NOT_COVERED.", [claim_ref]),
            ("SOURCE FACT: the benefit record exists and explicitly states covered=false.", [benefit_ref] if benefit_ref else []),
        ],
        action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
        rationale="Explain the explicit not-covered benefit rule.",
        next_step_refs=[r for r in (claim_ref, benefit_ref) if r],
    )
    adapter = FakeInvestigationBriefAdapter(response=brief)
    result = run_investigation(claim_id, query, adapter=adapter)

    sf = result.evidence_package.structured_facts if result.evidence_package else None
    checks = [
        Check.equals("claim_status_denied", sf.claim.status if sf and sf.claim else None, "DENIED"),
        Check.equals("denial_reason_service_not_covered", sf.claim.denial_reason_code if sf and sf.claim else None, "SERVICE_NOT_COVERED"),
        Check.equals("benefit_present_not_none", sf.benefit is not None if sf else False, True),
        Check.equals("benefit_covered_is_explicitly_false", sf.benefit.covered if sf and sf.benefit else None, False),
        Check.equals("benefit_not_flagged_as_missing", "benefit" not in result.agent_result.missing_information, True),
        Check.equals("evidence_status_sufficient", result.agent_result.status.value, "EVIDENCE_SUFFICIENT"),
        Check.equals("validation_passed", result.validation_result.status.value, "PASSED"),
    ]
    return ScenarioResult("E03", "CLM-1003 present-but-not-covered benefit treated as explicit negative evidence", checks)


# --- E04 -----------------------------------------------------------------------------


def run_e04() -> ScenarioResult:
    claim_id, query = "CLM-1004", "Why was my physical therapy claim denied?"
    context = _discover_context(claim_id, query)
    claim_ref = _first_ref(context, "claim:")
    provider_ref = _first_ref(context, "provider:servicing:")

    brief = well_formed_brief(
        summary="The claim is recorded as DENIED because the servicing provider is recorded as out-of-network.",
        findings=[
            ("SOURCE FACT: claim recorded DENIED / OUT_OF_NETWORK_PROVIDER.", [claim_ref]),
            ("SOURCE FACT: the servicing provider record is resolved and recorded as out-of-network.", [provider_ref] if provider_ref else []),
        ],
        action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
        rationale="Explain the recorded out-of-network provider denial.",
        next_step_refs=[r for r in (claim_ref, provider_ref) if r],
    )
    adapter = FakeInvestigationBriefAdapter(response=brief)
    result = run_investigation(claim_id, query, adapter=adapter)

    sf = result.evidence_package.structured_facts if result.evidence_package else None
    checks = [
        Check.equals("claim_status_denied", sf.claim.status if sf and sf.claim else None, "DENIED"),
        Check.equals("denial_reason_out_of_network", sf.claim.denial_reason_code if sf and sf.claim else None, "OUT_OF_NETWORK_PROVIDER"),
        Check.equals("provider_resolved", sf.servicing_provider is not None if sf else False, True),
        Check.equals("provider_network_status_out_of_network", sf.servicing_provider.network_status if sf and sf.servicing_provider else None, "out-of-network"),
        Check.equals("provider_not_flagged_as_missing", "servicing_provider" not in result.agent_result.missing_information, True),
        Check.equals("evidence_status_sufficient", result.agent_result.status.value, "EVIDENCE_SUFFICIENT"),
        Check.equals("validation_passed", result.validation_result.status.value, "PASSED"),
    ]
    return ScenarioResult("E04", "CLM-1004 resolved-but-out-of-network provider treated as explicit evidence, not a gap", checks)


# --- E05 -----------------------------------------------------------------------------


def run_e05() -> ScenarioResult:
    claim_id, query = "CLM-1005", "Why was my specialist visit claim denied?"
    adapter = FakeInvestigationBriefAdapter()  # would return a brief if called -- must never be called
    result = run_investigation(claim_id, query, adapter=adapter)

    packet = build_needs_review_packet(result)

    checks = [
        Check.equals("evidence_status_needs_review", result.agent_result.status.value, "NEEDS_REVIEW"),
        Check.equals("generation_not_attempted", result.generation_status.value, "NOT_ATTEMPTED"),
        Check.equals("validation_not_run", result.validation_result.status.value, "NOT_RUN"),
        Check.equals("zero_model_calls", len(adapter.calls), 0),
        Check.equals("no_fabricated_brief", result.investigation_brief is None, True),
        Check.equals("benefit_missing_explicit", "benefit" in result.agent_result.missing_information, True),
        Check.equals("servicing_provider_unresolved_explicit", "servicing_provider" in result.agent_result.missing_information, True),
        Check.equals("review_packet_produced", packet.status.value, "COMPLETED"),
        Check.equals("external_system_not_contacted", packet.evidence.get("external_system_contacted"), False),
    ]
    return ScenarioResult("E05", "CLM-1005 insufficient evidence routes to human review, zero model calls", checks)


# --- E06 -----------------------------------------------------------------------------


def run_e06() -> ScenarioResult:
    """Isolated Case-2 evidence variation: PA-2001 removed via a temporary,
    reversible DataStore-boundary override -- NOT a tool-function patch
    (post-audit remediation; see docs/HUMANA_BUILD_STATUS.md's remediation
    entry for the finding this replaces).

    An earlier version of this scenario patched
    tools.case_context.get_prior_authorizations, which affects only the
    TOOL/structured-facts read path. graph/builder.py builds the knowledge
    graph directly from the process-wide DataStore's flat
    `prior_authorizations` dict, never through that tool function -- so a
    tool-boundary-only patch left PA-2001 fully present in the graph and
    in every graph-derived reference, even though structured facts
    correctly showed it removed. A brief could then cite a graph-derived
    reference to PA-2001 and pass Rule A, silently defeating the
    "PA-2001 removed" premise this scenario exists to test.

    This version removes PA-2001 from BOTH `store.prior_authorizations`
    (the flat dict graph/builder.py iterates) and its
    `prior_authorizations_by_member_service` group (the grouped index
    tools/prior_auth_tool.py reads) -- mirroring
    application/scenario_lab.py's overlay pattern -- and clears the graph
    cache, so the removal is consistent across every evidence source:
    structured facts, graph relationships, assembled context references,
    and the rendered generation prompt built from all of that. Everything
    is restored exactly in `finally`. data/prior_authorizations.json is
    never read or modified -- only the in-memory DataStore is temporarily
    overlaid for the duration of this one evaluation call.
    """
    from application.brief_validator import validate_investigation_brief
    from graph.retriever import _get_graph
    from prompts.investigation_brief_prompt import render_user_prompt
    from tools.data_store import get_data_store

    claim_id, query = "CLM-1002", "Is there an approved authorization on file for this MRI?"
    store = get_data_store()

    removed_auth_id = "PA-2001"
    group_key = ("M-1002", "MRI-KNEE")
    original_auth_record = store.prior_authorizations.get(removed_auth_id)
    original_group = list(store.prior_authorizations_by_member_service.get(group_key, []))

    try:
        store.prior_authorizations.pop(removed_auth_id, None)
        store.prior_authorizations_by_member_service[group_key] = [
            a for a in original_group if a.authorization_id != removed_auth_id
        ]
        _get_graph.cache_clear()

        agent_result = run_case_agent(claim_id, query)
        context = assemble_context(agent_result)
        auth_refs = {r.ref_id for r in context.references if r.kind == "authorization"}
        claim_ref = _first_ref(context, "claim:")

        # Cross-source consistency: PA-2001 must be absent from every
        # evidence surface, not just the structured-facts reference list.
        evidence_package = agent_result.evidence_package
        structured_auth_ids = {a.authorization_id for a in evidence_package.structured_facts.prior_authorizations}
        graph_node_ids = {rel.source_id for rel in evidence_package.graph_relationships} | {
            rel.target_id for rel in evidence_package.graph_relationships
        }
        all_reference_ids = {r.ref_id for r in context.references}
        all_reference_text = " ".join(f"{r.ref_id} {r.detail}" for r in context.references)
        rendered_prompt = render_user_prompt(context)

        brief = well_formed_brief(
            summary="One prior-authorization candidate (PA-1501, EXPIRED) is on file for this member/service.",
            findings=[("SOURCE FACT: PA-1501 (EXPIRED) is the only authorization candidate in the supplied evidence.", list(auth_refs))],
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="Explain the recorded paid status with the remaining authorization evidence.",
            next_step_refs=[claim_ref] if claim_ref else [],
        )
        adapter = FakeInvestigationBriefAdapter(response=brief)
        result = run_investigation(claim_id, query, adapter=adapter)

        # Prove the variation's evidence-existence check is genuinely
        # sensitive to the removed candidate: a brief citing the now-absent
        # PA-2001 must fail Rule A, using the SAME isolated context.
        bad_brief = well_formed_brief(
            summary="test",
            findings=[("test finding citing a removed candidate", ["auth:PA-2001"])],
            action_code=ActionCode.EXPLAIN_RECORDED_STATUS,
            rationale="test",
            next_step_refs=[],
        )
        bad_validation = validate_investigation_brief(bad_brief, context)
    finally:
        store.prior_authorizations_by_member_service.pop(group_key, None)
        if original_group:
            store.prior_authorizations_by_member_service[group_key] = original_group
        if original_auth_record is not None:
            store.prior_authorizations[removed_auth_id] = original_auth_record
        _get_graph.cache_clear()

    checks = [
        Check.equals("only_pa_1501_present_in_isolated_context", auth_refs, {"auth:PA-1501"}),
        Check.equals("pa_2001_absent_from_structured_facts", "PA-2001" not in structured_auth_ids, True),
        Check.equals(
            "pa_2001_absent_from_graph_relationships",
            "authorization:PA-2001" not in graph_node_ids,
            True,
        ),
        Check.equals(
            "pa_2001_absent_from_assembled_references",
            not any("PA-2001" in ref_id for ref_id in all_reference_ids) and "PA-2001" not in all_reference_text,
            True,
        ),
        Check.equals("pa_2001_absent_from_rendered_prompt", "PA-2001" not in rendered_prompt, True),
        Check(
            "system_did_not_auto_escalate",
            result.agent_result.status.value != "NEEDS_REVIEW",
            result.agent_result.status.value,
            "anything other than NEEDS_REVIEW (prior_authorization is non-blocking for sufficiency)",
        ),
        Check.equals("evidence_status_sufficient", result.agent_result.status.value, "EVIDENCE_SUFFICIENT"),
        Check.equals("conforming_brief_validates", result.validation_result.status.value, "PASSED"),
        Check.equals("brief_citing_removed_candidate_fails_validation", bad_validation.status.value, "FAILED"),
    ]
    return ScenarioResult(
        "E06",
        "Isolated Case-2 variation (PA-2001 removed via DataStore-boundary override) - "
        "consistently absent from structured facts, graph, assembled context, and the rendered "
        "prompt; no auto-escalation, no fabricated answer",
        checks,
        notes=(
            "Isolation was achieved via a temporary, reversible DataStore-boundary override "
            "(store.prior_authorizations and its prior_authorizations_by_member_service group, "
            "plus a graph-cache clear/rebuild) rather than a tool-function patch -- the earlier "
            "tool-boundary-only approach could leave PA-2001 visible via graph-derived evidence "
            "even though structured facts showed it removed. data/prior_authorizations.json was "
            "never read or modified for this variation; the override is fully restored in a "
            "finally block regardless of outcome."
        ),
    )


# --- E07 -----------------------------------------------------------------------------


def run_e07() -> ScenarioResult:
    claim_id = "CLM-1001"
    rephrased_query = "What information in the available records helps explain the denial of CLM-1001?"
    context = _discover_context(claim_id, rephrased_query)
    claim_ref = _first_ref(context, "claim:")
    benefit_ref = _first_ref(context, "benefit:")

    brief = well_formed_brief(
        summary="The available records show a DENIED status with reason AUTH_REQUIRED; no authorization record is present.",
        findings=[
            ("SOURCE FACT: claim recorded DENIED / AUTH_REQUIRED.", [claim_ref]),
            ("SOURCE FACT: benefit requires prior authorization.", [benefit_ref] if benefit_ref else []),
        ],
        action_code=ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
        rationale="Verify whether a prior-authorization record exists.",
        next_step_refs=[r for r in (claim_ref, benefit_ref) if r],
    )
    adapter = FakeInvestigationBriefAdapter(response=brief)
    result = run_investigation(claim_id, rephrased_query, adapter=adapter)

    # Compare structured facts against E01's canonical run -- NOT lexical
    # text equality of any generated wording (deliberately not compared).
    canonical = run_case_agent(claim_id, "Why was this claim denied, and what evidence is available?")
    sf = result.evidence_package.structured_facts if result.evidence_package else None
    csf = canonical.evidence_package.structured_facts if canonical.evidence_package else None

    checks = [
        Check.equals("evidence_status_sufficient", result.agent_result.status.value, "EVIDENCE_SUFFICIENT"),
        Check.equals("generation_drafted", result.generation_status.value, "DRAFTED"),
        Check.equals("validation_passed", result.validation_result.status.value, "PASSED"),
        Check.equals(
            "same_claim_status_as_canonical_e01_wording",
            sf.claim.status if sf and sf.claim else None,
            csf.claim.status if csf and csf.claim else None,
        ),
        Check.equals(
            "same_denial_reason_as_canonical_e01_wording",
            sf.claim.denial_reason_code if sf and sf.claim else None,
            csf.claim.denial_reason_code if csf and csf.claim else None,
        ),
    ]
    return ScenarioResult(
        "E07",
        "Rephrased CLM-1001 question yields the same authoritative facts (no lexical-match requirement)",
        checks,
    )


# --- E08 -----------------------------------------------------------------------------


def run_e08() -> ScenarioResult:
    claim_id, query = "CLM-9999", "What happened with this claim?"
    adapter = FakeInvestigationBriefAdapter()
    result = run_investigation(claim_id, query, adapter=adapter)

    checks = [
        Check.equals("evidence_status_error", result.agent_result.status.value, "ERROR"),
        Check.equals("generation_not_attempted", result.generation_status.value, "NOT_ATTEMPTED"),
        Check.equals("validation_not_run", result.validation_result.status.value, "NOT_RUN"),
        Check.equals("zero_model_calls", len(adapter.calls), 0),
        Check.equals("no_fabricated_brief", result.investigation_brief is None, True),
        Check.equals("no_evidence_package_fabricated", result.evidence_package is None, True),
        Check(
            "distinct_from_case5_needs_review",
            result.agent_result.status.value != "NEEDS_REVIEW",
            result.agent_result.status.value,
            "anything other than NEEDS_REVIEW",
        ),
    ]
    return ScenarioResult("E08", "Unknown claim CLM-9999 - clean not-found error, distinct from Case 5", checks)


ALL_SCENARIOS = [run_e01, run_e02, run_e03, run_e04, run_e05, run_e06, run_e07, run_e08]


def run_all() -> list[ScenarioResult]:
    return [scenario() for scenario in ALL_SCENARIOS]


def _main(argv: list[str] | None = None) -> None:
    results = run_all()
    print_scenario_report("H4 End-to-End Product Scenarios (E01-E08)", results)
    write_artifact(results, ARTIFACTS_DIR / "e2e_eval_results.json")


if __name__ == "__main__":
    _main(sys.argv[1:])
