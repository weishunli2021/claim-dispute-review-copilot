"""Module 3: isolated dispute-review submission models and deterministic
comparison logic.

This package is a self-contained, PURE business-logic layer -- no
Streamlit import, no LLM call, no prompt, no agent, no new dependency,
and no persistence. It never mutates data/*.json, the shared
tools.data_store.DataStore singleton, authorization records, claim
records, or the knowledge graph (graph/). It does not reuse Scenario Lab's
DataStore-boundary override (application/scenario_lab.py) -- see
dispute_review/comparison.py's module docstring for why.

    dispute_review.models      Pydantic contracts: DisputeSubmission,
                                ClaimSnapshot, ComparisonRow,
                                DisputeComparisonResult.
    dispute_review.comparison  build_claim_snapshot() (read-only, from an
                                existing tools.case_context.CaseContext)
                                and compare_submission() (pure function:
                                ClaimSnapshot + DisputeSubmission ->
                                DisputeComparisonResult).
    dispute_review.presets     Synthetic demo submissions derived from a
                                real claim snapshot -- never hardcoded
                                outcomes, never inserted into the DataStore.

No UI and no session-state storage live here -- see
docs/DISPUTE_REVIEW_LOGIC.md for what Module 4 still needs to add.
"""
