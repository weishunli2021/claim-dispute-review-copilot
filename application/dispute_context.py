"""Assembles a labeled, budgeted DisputeGenerationContext from an
already-built DisputeEvidencePackage. The dispute analogue of
application/context_assembler.py -- does NOT re-run retrieval, re-call
investigate_dispute, or re-derive the comparison. It only relabels and
budgets evidence application.dispute_workflow already assembled in one
run.

Character budget (NOT a token count, matching context_assembler.py's own
documented choice) applied only to policy-passage text -- the one field
whose length varies widely. Recorded facts, submitted fields, comparison
findings, and graph relationships are never truncated: they are small,
bounded, and considered essential. If a policy passage is dropped or
shortened to fit the budget, that fact is recorded in `truncation_notes`
(never silent), and a dropped reference is simply absent from
`references` -- so the generator can never cite a reference id it was
never shown (application/dispute_brief_validator.py's Rule A then rejects
any citation outside this exact list).
"""

from __future__ import annotations

from context.dispute_evidence_models import DisputeEvidencePackage, EvidenceReference
from application.dispute_models import DisputeGenerationContext, EvidenceGateResult

# Separate constants from context_assembler.py's own (never shared -- see
# application/dispute_models.py's module docstring on why dispute contracts
# stay a wholly separate set), but the same documented shape and reasoning.
MAX_POLICY_CONTEXT_CHARS = 6000
TRUNCATED_CHUNK_KEEP_CHARS = 400


def assemble_dispute_context(
    evidence_package: DisputeEvidencePackage, gate_result: EvidenceGateResult
) -> DisputeGenerationContext:
    """Build a DisputeGenerationContext from an already-assembled
    DisputeEvidencePackage and its EvidenceGateResult.

    Combines recorded_facts, submitted_fields, comparison_findings, and
    graph_relationships verbatim (never truncated), then policy_passages
    under the character budget above. Graph and structured facts are
    never re-labeled as independent confirmation of one another -- that
    caveat already lives in `evidence_package.limitations` and is carried
    through unchanged.
    """
    references: list[EvidenceReference] = []
    references.extend(evidence_package.recorded_facts)
    references.extend(evidence_package.submitted_fields)
    references.extend(evidence_package.comparison_findings)
    references.extend(evidence_package.graph_relationships)

    truncation_notes: list[str] = []
    policy_char_budget = MAX_POLICY_CONTEXT_CHARS
    for ref in evidence_package.policy_passages:
        text = ref.detail
        if len(text) > policy_char_budget:
            kept = max(policy_char_budget, 0)
            if kept == 0:
                truncation_notes.append(
                    f"{ref.ref_id}: omitted entirely (policy context budget "
                    f"{MAX_POLICY_CONTEXT_CHARS} already exhausted)."
                )
                continue
            truncated_text = text[:kept] + " …[truncated]"
            truncation_notes.append(
                f"{ref.ref_id}: kept {kept} of {len(text)} chars (policy context budget "
                f"{MAX_POLICY_CONTEXT_CHARS} exhausted; explicitly flagged, not silent)."
            )
            references.append(
                EvidenceReference(
                    ref_id=ref.ref_id,
                    source_type=ref.source_type,
                    provenance=ref.provenance,
                    label=ref.label,
                    detail=truncated_text,
                    score=ref.score,
                )
            )
            policy_char_budget = 0
        else:
            references.append(ref)
            if len(text) > 0:
                policy_char_budget -= len(text)

    return DisputeGenerationContext(
        claim_id=evidence_package.claim_id,
        gate_status=gate_result.status,
        comparison_summary=evidence_package.comparison_result.summary,
        references=references,
        missing_evidence=list(evidence_package.missing_evidence),
        conflicts=list(evidence_package.conflicts),
        limitations=list(evidence_package.limitations),
        context_truncated=bool(truncation_notes),
        truncation_notes=truncation_notes,
    )
