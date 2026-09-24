"""Module 6B: deterministic, non-LLM validation of a DRAFTED DisputeBrief.

Mirrors application/brief_validator.py's scope and discipline exactly --
this module checks ONLY what can be verified objectively and mechanically.
It is NOT a semantic-entailment checker, NOT a hallucination detector, and
NOT a comprehensive prompt-injection defense -- see each rule's docstring
for its exact, narrow boundary. Reuses application.brief_validator's own
advisory-action allowlist and prohibited-authority-language patterns
directly (AGENTS.md rule 4: do not duplicate a business rule that already
lives in exactly one place) -- both are domain-agnostic (the same closed
advisory vocabulary and the same "approve/deny/reverse/pay the
claim/authorization" phrase shapes apply identically to a dispute brief).

Rules:
  Rule A -- evidence_reference_existence: every evidence_ref cited by the
            brief must exist among the reference ids actually shown to the
            model (context.references). Exact string match only.
  Rule B -- finding_requires_evidence_ref: every Finding must cite at
            least one evidence_ref.
  Rule C -- comparison_context_integrity: the four "comparison:<field>"
            references actually rendered into the context must exist, one
            per row, and their detail text must match
            comparison_result.rows[i].explanation EXACTLY. This does NOT
            check anything the model said -- it is a structural regression
            guard confirming the CONTEXT the model saw was never corrupted
            or drifted from the authoritative comparator output before
            generation (Step 2/6's "carry those authoritative results...
            never re-derived").
  Rule D -- advisory_action_allowlist: suggested_next_step.action_code
            must be one of application.brief_validator.ALLOWED_ACTION_CODES
            (reused, not duplicated).
  Rule E -- unverified_provenance_preserved: a Finding that cites a
            SUBMITTED_UNVERIFIED or RECORDED_VIA_SUBMITTED_LOOKUP
            reference must not, in the same statement, use UNNEGATED
            language that asserts the submitted content has been
            authenticated/verified/confirmed -- a narrow, documented,
            word-boundary keyword check with a small same-clause
            negation-cue exemption (NOT a semantic checker, NOT general
            negation understanding; see _find_unnegated_verified_claim's
            docstring for the exact scope).
  Rule F -- prohibited_authority_language: reuses
            application.brief_validator's own regex patterns across every
            narrative field (summary, each finding's statement, each
            missing_or_conflicting_evidence entry, each verification
            question, and the suggested next step's rationale).

THIS IS NOT:
  - a hallucination detector, or a semantic-entailment checker (Rule A only
    checks a reference id EXISTS, never that the evidence actually
    SUPPORTS what a finding says);
  - proof that a "matching providers" rule was not implied by inference --
    whether the brief invents an unsupported policy rule (e.g. "providers
    must match") is a JUDGE-assessed concern (evidence grounding /
    authority boundary dimensions), not something regex can verify; this
    is a documented instruction-only boundary (see prompts/dispute_brief_prompt.py
    rule 5), not a deterministic check;
  - a comprehensive prompt-injection or jailbreak defense (Rule F is a
    small set of targeted phrase-shape patterns, same scope as the
    original validator);
  - a substitute for human review.
"""

from __future__ import annotations

import re
from typing import Optional

from application.brief_validator import ALLOWED_ACTION_CODES, _find_prohibited_authority_phrase
from application.dispute_models import (
    DisputeBrief,
    DisputeGenerationContext,
    DisputeValidationIssue,
    DisputeValidationResult,
    DisputeValidationStatus,
)
from context.dispute_evidence_models import EvidenceProvenanceCategory as Provenance
from dispute_review.models import DisputeComparisonResult

_UNVERIFIED_PROVENANCE = frozenset({Provenance.SUBMITTED_UNVERIFIED, Provenance.RECORDED_VIA_SUBMITTED_LOOKUP})

# Rule E: a narrow, documented keyword check -- NOT a semantic checker and
# NOT general negation understanding. Two deliberately small refinements
# over a plain substring check, confirmed necessary by a live smoke check
# (see docs/DISPUTE_AI_VALIDATION.md): a live model's correct, desired hedge
# -- "unverified" -- was being flagged as if it asserted verification, on
# 3 of 3 live requests, because "verified" is a substring of "unverified".
#
#   1. Word-boundary matching ("\bverified\b" etc.) instead of substring
#      containment: "verified" no longer matches inside "unverified" (or
#      "confirmed" inside "unconfirmed", etc.) at all, because there is no
#      word boundary between "un" and the root when they form one token.
#   2. A narrow, same-clause negation-cue check: a small, closed set of
#      negation words/contractions (not, never, cannot, doesn't, ...). If
#      one appears anywhere BEFORE a claim word within the same clause,
#      that occurrence is treated as negated and not flagged -- covering
#      "has not been verified" / "has not yet been verified" / "do not
#      establish verified authorization" (the cue can be several words
#      before the claim word; this is not restricted to immediate
#      adjacency).
#
# "Clause" here is a small, explicit split on sentence punctuation
# (. ! ? ;) and on a short list of contrastive conjunctions (but, however,
# although, though). This is intentional and load-bearing: a negation cue
# in one clause must NEVER exempt an unnegated positive claim in a later
# clause of the same statement -- e.g. "The submission is unverified, but
# we have verified its authenticity." must still be flagged, because the
# positive claim ("we have verified...") is its own clause with no
# negation cue of its own. Splitting deliberately does NOT happen on the
# word "yet" (to avoid breaking "has not yet been verified" into two
# clauses and losing the cue), and deliberately does NOT scan an entire
# multi-sentence statement as one unit (to avoid an early, unrelated
# negation silently exempting a later unsafe claim).
#
# THIS IS STILL NOT a semantic-entailment checker or general negation
# handling: it does not understand double negatives, negation scope across
# subordinate clauses ("although some believe it is verified, ..."), or
# any phrasing outside this fixed word/cue list. It is a narrow, documented
# refinement of the same containment-check class as
# application/brief_validator.py's own Rule D limitation, not a claim of
# general natural-language understanding.
_VERIFIED_CLAIM_WORDS = ("confirmed", "verified", "authenticated", "validated")
_CLAIM_WORD_PATTERN = re.compile(r"\b(" + "|".join(_VERIFIED_CLAIM_WORDS) + r")\b", re.IGNORECASE)

# A small, closed, documented set of negation cues -- not a general
# negation parser. Multi-word forms ("does not", "has not", "do not", "is
# not", "was not") are all covered by the standalone "not" token; only the
# contracted forms need listing explicitly since they carry no standalone
# "not" token of their own.
_NEGATION_CUE_PATTERN = re.compile(
    r"\b(not|never|cannot|doesn't|don't|didn't|isn't|wasn't|weren't|hasn't|haven't|hadn't|"
    r"can't|couldn't|won't|wouldn't|shouldn't)\b",
    re.IGNORECASE,
)

# Clause boundaries: sentence punctuation, or a short, closed list of
# contrastive conjunctions (optionally preceded by a comma). Deliberately
# excludes "yet" -- see the module-level comment above for why.
_CLAUSE_SPLIT_PATTERN = re.compile(r"[.!?;]|,?\s*\b(?:but|however|although|though)\b\s*", re.IGNORECASE)


def _find_unnegated_verified_claim(text: str) -> Optional[str]:
    """Return the first claim word (e.g. "verified") in `text` that is NOT
    preceded, within its own clause, by a negation cue -- or None if every
    occurrence is negated (or there is no occurrence at all). See the
    module-level comment above this function's constants for the exact,
    narrow scope of "clause" and "negation cue" used here."""
    for clause in _CLAUSE_SPLIT_PATTERN.split(text):
        if not clause:
            continue
        for claim_match in _CLAIM_WORD_PATTERN.finditer(clause):
            preceding = clause[: claim_match.start()]
            if _NEGATION_CUE_PATTERN.search(preceding):
                continue  # negated within this same clause -- not an unsafe claim
            return claim_match.group(0)
    return None


def _expected_comparison_ref_ids(comparison_result: DisputeComparisonResult) -> dict[str, str]:
    """ref_id -> expected detail (row.explanation), for all four rows --
    mirrors context/dispute_evidence_retriever.py's own
    _build_comparison_findings ref_id scheme exactly (never re-derived
    from a different source)."""
    return {f"comparison:{row.field.lower().replace(' ', '_')}": row.explanation for row in comparison_result.rows}


def validate_dispute_brief(
    brief: DisputeBrief,
    context: DisputeGenerationContext,
    comparison_result: DisputeComparisonResult,
) -> DisputeValidationResult:
    """Run every deterministic check against one DRAFTED DisputeBrief.

    `context` must be the SAME DisputeGenerationContext actually rendered
    into the prompt for this brief -- Rule A validates against exactly
    what the model was shown, never a re-fetched context.
    `comparison_result` must be the SAME comparison_result the evidence
    package this context was built from carries -- Rule C validates the
    context's own embedded comparison references against it.
    """
    issues: list[DisputeValidationIssue] = []
    known_ref_ids = {ref.ref_id for ref in context.references}
    provenance_by_ref_id = {ref.ref_id: ref.provenance for ref in context.references}

    # Rule A: every cited evidence_ref must exist.
    for index, finding in enumerate(brief.findings):
        for ref in finding.evidence_refs:
            if ref not in known_ref_ids:
                issues.append(
                    DisputeValidationIssue(
                        rule="evidence_reference_existence",
                        detail=(
                            f"findings[{index}].evidence_refs cites {ref!r}, which does not "
                            "exist in the evidence made available for this request."
                        ),
                    )
                )
    for ref in brief.suggested_next_step.evidence_refs:
        if ref not in known_ref_ids:
            issues.append(
                DisputeValidationIssue(
                    rule="evidence_reference_existence",
                    detail=(
                        f"suggested_next_step.evidence_refs cites {ref!r}, which does not exist "
                        "in the evidence made available for this request."
                    ),
                )
            )

    # Rule B: every Finding must cite at least one evidence_ref.
    for index, finding in enumerate(brief.findings):
        if not finding.evidence_refs:
            issues.append(
                DisputeValidationIssue(
                    rule="finding_requires_evidence_ref",
                    detail=f"findings[{index}] ({finding.statement!r}) cites no evidence_refs.",
                )
            )

    # Rule C: the context's own embedded comparison references must match
    # the authoritative comparison_result exactly -- a structural
    # regression guard, not a check on the model's output.
    expected = _expected_comparison_ref_ids(comparison_result)
    context_comparison_refs = {ref.ref_id: ref.detail for ref in context.references if ref.source_type == "comparison_finding"}
    for ref_id, expected_detail in expected.items():
        if ref_id not in context_comparison_refs:
            issues.append(
                DisputeValidationIssue(
                    rule="comparison_context_integrity",
                    detail=f"Expected comparison reference {ref_id!r} is missing from the generation context.",
                )
            )
        elif context_comparison_refs[ref_id] != expected_detail:
            issues.append(
                DisputeValidationIssue(
                    rule="comparison_context_integrity",
                    detail=(
                        f"Comparison reference {ref_id!r} in the generation context does not match "
                        "the deterministic comparator's own explanation for this field."
                    ),
                )
            )

    # Rule D: suggested_next_step.action_code must be an approved advisory action.
    if brief.suggested_next_step.action_code not in ALLOWED_ACTION_CODES:
        issues.append(
            DisputeValidationIssue(
                rule="advisory_action_allowlist",
                detail=(
                    f"suggested_next_step.action_code {brief.suggested_next_step.action_code!r} "
                    f"is not in the approved advisory action allowlist "
                    f"{sorted(a.value for a in ALLOWED_ACTION_CODES)}."
                ),
            )
        )

    # Rule E: unverified provenance must not be described as authenticated.
    # See _find_unnegated_verified_claim's own docstring/module comment for
    # the exact, narrow negation-awareness this adds over a plain substring
    # check.
    for index, finding in enumerate(brief.findings):
        cites_unverified = any(provenance_by_ref_id.get(ref) in _UNVERIFIED_PROVENANCE for ref in finding.evidence_refs)
        if not cites_unverified:
            continue
        matched_word = _find_unnegated_verified_claim(finding.statement)
        if matched_word is not None:
            issues.append(
                DisputeValidationIssue(
                    rule="unverified_provenance_preserved",
                    detail=(
                        f"findings[{index}] cites unverified/submitted evidence but its statement "
                        f"contains {matched_word!r}, implying it has been authenticated: "
                        f"{finding.statement!r}"
                    ),
                )
            )

    # Rule F: no generated narrative text may claim consequential authority.
    narrative_fields: list[tuple[str, str]] = [("summary", brief.summary)]
    for index, finding in enumerate(brief.findings):
        narrative_fields.append((f"findings[{index}].statement", finding.statement))
    for index, item in enumerate(brief.missing_or_conflicting_evidence):
        narrative_fields.append((f"missing_or_conflicting_evidence[{index}]", item))
    for index, item in enumerate(brief.verification_questions):
        narrative_fields.append((f"verification_questions[{index}]", item))
    narrative_fields.append(("suggested_next_step.rationale", brief.suggested_next_step.rationale))

    for field_name, text in narrative_fields:
        phrase = _find_prohibited_authority_phrase(text)
        if phrase is not None:
            issues.append(
                DisputeValidationIssue(
                    rule="prohibited_authority_language",
                    detail=f"{field_name} contains a claim of consequential authority ({phrase!r}): {text!r}",
                )
            )

    status = DisputeValidationStatus.FAILED if issues else DisputeValidationStatus.PASSED
    return DisputeValidationResult(status=status, issues=issues)
