"""Isolated billing-correction dispute-review models and deterministic
comparison logic.

This package is a self-contained, PURE business-logic layer -- no
Streamlit import (except dispute_review.ui, the one rendering module), no
LLM call, no prompt, no agent, no new dependency, and no persistence
beyond its own isolated fixtures/indexes. It never mutates data/*.json, the
shared tools.data_store.DataStore singleton, or the shared knowledge graph
(graph.retriever's own singleton).

    dispute_review.models             Pydantic contracts:
                                       BillingCorrectionSubmission,
                                       BillingClaimSnapshot,
                                       BillingComparisonRow,
                                       BillingComparisonResult.
    dispute_review.billing_fixtures   Isolated fixture loaders for the
                                       original claim/decision and the
                                       independent supporting records.
    dispute_review.billing_policy     A deterministic full-document read,
                                       and REAL semantic vector search over
                                       the synthetic billing policy's own
                                       isolated index (rag/index_billing/).
    dispute_review.billing_graph      An isolated provider-network graph
                                       (its own fixture, its own small
                                       nx.MultiDiGraph) -- never the shared
                                       golden-dataset graph.
    dispute_review.comparison         build_billing_claim_snapshot(),
                                       check_claim_member_linkage(), and
                                       compare_billing_correction() (pure
                                       function).
    dispute_review.presets            The single demo submission example.

Also retained, but no longer wired into the active scenario:
dispute_review.authorization_source_lookup/authorization_source_models and
dispute_review.scenario_synthetic_claim_decision_events -- reusable,
independently-tested modules from a retired scenario (see
docs/BILLING_CORRECTION_SCENARIO.md).
"""
