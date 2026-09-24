"""LangGraph workflow definition and public entry point for the
claim-investigation agent.

Agent = orchestrates WHAT HAPPENS (this module + agents/nodes.py +
agents/routing.py) -- deciding which capability runs next and why. It
never retrieves evidence, performs a deterministic lookup, or judges
evidence sufficiency itself; all of that is delegated:

  Tool    = one deterministic operation (tools/) -- used directly by the
            agent for narrow, cheap checks (e.g. load_case's existence
            check via tools.case_context.get_case_context).
  Skill   = a reusable BUSINESS CAPABILITY that may coordinate multiple
            tools/context sources (skills/) -- e.g. investigate_claim,
            which owns both evidence assembly AND the evidence-
            sufficiency judgment. The agent's build_evidence node calls
            this skill; it does not reimplement the capability itself.
  GraphRAG/context = constructs an EvidencePackage from tools + Vector
            RAG + Knowledge Graph evidence (context/) -- used BY the
            investigate_claim skill, not directly by the agent.
  Evaluation = measures agent behavior independently, offline
            (evals/agent_eval.py) -- never used to influence routing.

    Agent -> Skill -> Tools / GraphRAG

This stage produces an investigation TRACE and an EvidencePackage, never
a final natural-language answer -- there is no LLM call anywhere in
agents/ or skills/.
"""

from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from typing import Optional

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents.nodes import (
    assess_case,
    assess_evidence,
    build_evidence,
    complete,
    error,
    load_case,
    max_steps_exceeded,
    needs_review,
    validate_request,
)
from agents.routing import (
    route_after_assess_case,
    route_after_assess_evidence,
    route_after_build_evidence,
    route_after_load_case,
    route_after_validate_request,
)
from agents.state import AgentResult, AgentState, AgentStatus

DEFAULT_MAX_STEPS = 8


def build_case_agent_graph() -> CompiledStateGraph:
    """Build and compile the LangGraph StateGraph for claim investigation.

    START -> validate_request -> {load_case | error | max_steps_exceeded}
    load_case -> {assess_case | error | max_steps_exceeded}
    assess_case -> {build_evidence | max_steps_exceeded}
    build_evidence -> {assess_evidence | error | max_steps_exceeded}
    assess_evidence -> {complete | needs_review | max_steps_exceeded}
    {complete, needs_review, error, max_steps_exceeded} -> END
    """
    graph = StateGraph(AgentState)

    graph.add_node("validate_request", validate_request)
    graph.add_node("load_case", load_case)
    graph.add_node("assess_case", assess_case)
    graph.add_node("build_evidence", build_evidence)
    graph.add_node("assess_evidence", assess_evidence)
    graph.add_node("complete", complete)
    graph.add_node("needs_review", needs_review)
    graph.add_node("error", error)
    graph.add_node("max_steps_exceeded", max_steps_exceeded)

    graph.set_entry_point("validate_request")

    graph.add_conditional_edges(
        "validate_request",
        route_after_validate_request,
        {
            "load_case": "load_case",
            "error": "error",
            "max_steps_exceeded": "max_steps_exceeded",
        },
    )
    graph.add_conditional_edges(
        "load_case",
        route_after_load_case,
        {
            "assess_case": "assess_case",
            "error": "error",
            "max_steps_exceeded": "max_steps_exceeded",
        },
    )
    graph.add_conditional_edges(
        "assess_case",
        route_after_assess_case,
        {
            "build_evidence": "build_evidence",
            "max_steps_exceeded": "max_steps_exceeded",
        },
    )
    graph.add_conditional_edges(
        "build_evidence",
        route_after_build_evidence,
        {
            "assess_evidence": "assess_evidence",
            "error": "error",
            "max_steps_exceeded": "max_steps_exceeded",
        },
    )
    graph.add_conditional_edges(
        "assess_evidence",
        route_after_assess_evidence,
        {
            "complete": "complete",
            "needs_review": "needs_review",
            "max_steps_exceeded": "max_steps_exceeded",
        },
    )

    graph.add_edge("complete", END)
    graph.add_edge("needs_review", END)
    graph.add_edge("error", END)
    graph.add_edge("max_steps_exceeded", END)

    return graph.compile()


@lru_cache(maxsize=1)
def _get_compiled_graph() -> CompiledStateGraph:
    return build_case_agent_graph()


def run_case_agent(claim_id: str, query: str, max_steps: int = DEFAULT_MAX_STEPS) -> AgentResult:
    """Run the claim-investigation agent workflow and return a
    product-facing AgentResult.

    Never raises for expected failure modes (invalid input, unknown
    claim, insufficient evidence, an exception inside evidence
    assembly) -- all of those surface as a normal AgentResult with an
    appropriate status instead. Never exposes raw LangGraph state
    internals (current_step, max_steps bookkeeping) to callers.
    """
    initial_state: AgentState = {
        "original_query": query,
        "claim_id": claim_id,
        "claim_context": None,
        "evidence_package": None,
        "missing_information": [],
        "actions_taken": [],
        "current_step": "START",
        "status": AgentStatus.RUNNING.value,
        "error": None,
        "step_count": 0,
        "max_steps": max_steps,
        "skill_status": None,
    }

    final_state = _get_compiled_graph().invoke(initial_state)

    return AgentResult(
        status=AgentStatus(final_state["status"]),
        actions_taken=final_state["actions_taken"],
        claim_context=final_state.get("claim_context"),
        evidence_package=final_state.get("evidence_package"),
        missing_information=final_state.get("missing_information", []),
        error=final_state.get("error"),
        step_count=final_state["step_count"],
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug utility: run the claim-investigation agent. Produces an "
            "investigation trace and evidence summary -- no natural-language "
            "resolution is generated."
        ),
    )
    parser.add_argument("claim_id", help="e.g. CLM-1001")
    parser.add_argument("query", help="e.g. 'My claim was denied. What's wrong with it?'")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    args = parser.parse_args(argv)

    result = run_case_agent(args.claim_id, args.query, max_steps=args.max_steps)

    print("STATUS")
    print(f"  {result.status.value}")
    print()

    print("AGENT TRACE")
    for action in result.actions_taken:
        print(f"  {action}")
    print()

    print("STRUCTURED CASE SUMMARY")
    cc = result.claim_context
    if cc and cc.claim:
        print(f"  claim: {cc.claim.claim_id} status={cc.claim.status} denial_reason_code={cc.claim.denial_reason_code}")
        if cc.member:
            print(f"  member: {cc.member.member_id} ({cc.member.first_name} {cc.member.last_name})")
        if cc.plan:
            print(f"  plan: {cc.plan.plan_id} ({cc.plan.plan_name})")
        if cc.benefit:
            print(f"  benefit: {cc.benefit.benefit_id} covered={cc.benefit.covered} requires_prior_auth={cc.benefit.requires_prior_auth}")
        for auth in cc.prior_authorizations:
            print(f"  prior_authorization: {auth.authorization_id} status={auth.status}")
        if cc.servicing_provider:
            print(f"  servicing_provider: {cc.servicing_provider.provider_id} network_status={cc.servicing_provider.network_status}")
        if cc.ordering_provider:
            print(f"  ordering_provider: {cc.ordering_provider.provider_id} network_status={cc.ordering_provider.network_status}")
    else:
        print("  (no case context available)")
    print()

    print("EVIDENCE SUMMARY")
    ep = result.evidence_package
    if ep:
        print(f"  policy chunks retrieved ({len(ep.policy_chunks)}):")
        for chunk in ep.policy_chunks:
            print(f"    [{chunk.section_id}] {chunk.section_title}")
        print(f"  graph relationships retained ({len(ep.graph_relationships)}):")
        for rel in ep.graph_relationships:
            print(f"    {rel.source_id} --{rel.relation}--> {rel.target_id}")
    else:
        print("  (no evidence package built)")
    print()

    print("MISSING INFORMATION")
    if result.missing_information:
        for item in result.missing_information:
            print(f"  {item}")
    else:
        print("  (none)")

    if result.error:
        print()
        print("ERROR")
        print(f"  {result.error}")


if __name__ == "__main__":
    _main(sys.argv[1:])
