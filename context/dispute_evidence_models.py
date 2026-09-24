"""Typed structures for Module 6A's dispute-evidence assembly layer.

A DisputeEvidencePackage bundles everything investigate_dispute (skills/)
assembles for one dispute-review submission against one existing claim:

- The recorded claim snapshot and the deterministic comparison result
  (dispute_review/, reused exactly as built by Modules 3-5 -- never
  reimplemented here).
- Structured facts and candidates already on file (tools/, via
  tools.case_context.get_case_context), including any real record found by
  looking up a SUBMITTED identifier (a provider id or authorization
  reference) -- kept visibly distinct from the claim's own recorded facts.
- Retrieved policy passages (rag/, via rag.retriever.search_policy).
- Recorded graph relationships (graph/, via graph.retriever, filtered for
  relevance) -- both the claim's own relationships and, separately, the
  submitted provider's own relationships (never conflated).
- Explicit per-source outcomes, missing evidence, conflicts, and
  limitations -- never silently folded into "everything looked fine."

There is deliberately NO generation-related field anywhere in this module
(no brief, no verdict, no confidence score, no sufficiency judgment) --
this is evidence for Module 6B's future workflow/generator/judge to
consume, exactly like context/models.py's EvidencePackage is evidence for
the existing investigation pipeline, never a conclusion drawn here.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from dispute_review.models import ClaimSnapshot, DisputeComparisonResult, DisputeSubmission


class EvidenceSourceStatus(str, Enum):
    """The outcome of ONE retrieval source's attempt for THIS request --
    distinct from whether the evidence it returns, once retrieved, is
    relevant, applicable, or sufficient (a Module 6B/generation-layer
    judgment, never made here).

    SUCCESS_NO_RESULTS is not a failure: it means the source was reachable
    and ran, and legitimately found nothing (e.g. no policy chunk crossed
    the retriever's own top-k cutoff, or a submitted identifier has no
    matching record in the accessible synthetic dataset). FAILURE means
    the source itself could not be queried at all (e.g. a malformed
    identifier, an unexpected exception) and is preserved AS a failure,
    never silently downgraded into an empty success -- a caller that only
    checked "were there zero items" could otherwise mistake a broken
    retrieval for a legitimate absence.
    """

    SUCCESS_WITH_EVIDENCE = "SUCCESS_WITH_EVIDENCE"
    SUCCESS_NO_RESULTS = "SUCCESS_NO_RESULTS"
    FAILURE = "FAILURE"


class EvidenceSourceOutcome(BaseModel):
    """One retrieval source's outcome for this request (e.g.
    "structured_case_context", "submitted_provider_lookup", "policy",
    "graph_claim", "graph_submitted_provider"). `detail` is always a
    short, clean, user-facing message -- never a raw exception message or
    traceback, and never a secret (AGENTS.md rule 12)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    status: EvidenceSourceStatus
    item_count: int = 0
    detail: Optional[str] = None


class EvidenceProvenanceCategory:
    """Closed, documented vocabulary for EvidenceReference.provenance --
    plain string constants (not an Enum) so tests and callers can compare
    against them without an extra import, and so a reader scanning
    evidence JSON sees the category name directly. Never invented ad hoc
    per reference; every EvidenceReference uses exactly one of these.
    """

    RECORDED = "RECORDED"
    """Already on file in the synthetic dataset (tools.case_context) --
    the claim itself and its resolved member/plan/benefit/authorization-
    candidate/provider records."""

    SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
    """Raw content from the DisputeSubmission itself -- never authenticated,
    never treated as equivalent to a RECORDED fact."""

    RECORDED_VIA_SUBMITTED_LOOKUP = "RECORDED_VIA_SUBMITTED_LOOKUP"
    """A REAL record in the synthetic dataset, found by looking up a
    SUBMITTED identifier (e.g. a provider record found via the submitted
    servicing_provider_id, or an authorization record found via the
    submitted authorization_reference_number). The record itself is
    genuine/recorded, but its applicability to THIS claim/dispute is
    unverified -- this category exists specifically so such a record is
    never confused with the claim's own recorded servicing provider or an
    authorization candidate already tied to this member+service (see
    STEP 3's "Submission references must never resemble verified
    authorization records")."""

    RETRIEVED_POLICY = "RETRIEVED_POLICY"
    """A policy passage returned by rag.retriever.search_policy."""

    RECORDED_RELATIONSHIP = "RECORDED_RELATIONSHIP"
    """A graph edge exactly as graph.retriever produced it."""

    DETERMINISTIC_COMPARISON = "DETERMINISTIC_COMPARISON"
    """One row of dispute_review.comparison.compare_submission's output."""


class EvidenceReference(BaseModel):
    """One citable item in the dispute evidence package -- the same shape
    across every evidence category (recorded facts, submitted fields,
    policy passages, graph relationships, comparison findings), so a
    future generator/judge (Module 6B) can cite any of them uniformly.

    `ref_id` is always namespaced by category and built only from real
    fields already present on the underlying record/document/submission --
    never a fabricated id (mirrors application/context_assembler.py's own
    REF_ID convention). See context/dispute_evidence_retriever.py for
    exactly which prefix each category uses.

    `score` carries a retrieval source's own similarity score, when it
    produced one (currently only policy passages) -- a ranking signal from
    the embedding/vector search, never an accuracy or confidence
    probability, and never present for a non-retrieval reference (recorded
    facts, submitted fields, comparison findings all leave it None).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref_id: str
    source_type: str
    provenance: str
    label: str
    detail: str
    score: Optional[float] = None


class DisputeEvidencePackage(BaseModel):
    """The complete evidence bundle assembled for one dispute-review
    submission against one existing claim (investigate_dispute's return
    payload, wrapped in skills.base.SkillResult.evidence).

    Six clearly separated sections, per Module 6A Step 2:
      1. recorded_facts       -- structured facts already on file.
      2. submitted_fields      -- provider/patient-supplied UNVERIFIED
                                   fields and narrative, verbatim.
      3. comparison_result / comparison_findings -- the deterministic
                                   comparator's full result, plus one
                                   citable reference per row.
      4. policy_passages       -- retrieved policy passages.
      5. graph_relationships   -- recorded graph relationships (claim's
                                   own + the submitted provider's own,
                                   distinguished by source_type).
      6. source_outcomes / missing_evidence / conflicts / limitations --
                                   explicit availability, gaps, and caveats.

    `claim_snapshot` and `comparison_result` are the EXACT objects
    dispute_review.comparison.build_claim_snapshot/compare_submission
    already produce -- never re-derived or re-computed by a different
    code path, so this package can never disagree with the Dispute Review
    tab's own displayed result for the same submission.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str
    claim_snapshot: ClaimSnapshot
    submission: DisputeSubmission
    comparison_result: DisputeComparisonResult

    recorded_facts: list[EvidenceReference] = Field(default_factory=list)
    submitted_fields: list[EvidenceReference] = Field(default_factory=list)
    comparison_findings: list[EvidenceReference] = Field(default_factory=list)
    policy_passages: list[EvidenceReference] = Field(default_factory=list)
    graph_relationships: list[EvidenceReference] = Field(default_factory=list)

    source_outcomes: list[EvidenceSourceOutcome] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
