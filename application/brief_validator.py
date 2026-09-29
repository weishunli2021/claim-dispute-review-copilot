"""H2: deterministic, non-LLM validation of a DRAFTED InvestigationBrief.

This module is intentionally narrow. It checks ONLY the parts of a brief
that can be checked objectively, mechanically, without judging whether the
generated natural language is actually true or actually supported in
substance by the evidence:

  Rule A -- every evidence_ref the brief cites must exist among the
            reference ids that were actually made available to the model
            for this request (application.models.AssembledContext.references).
            Exact string match only -- no fuzzy matching.
  Rule B -- every Finding must cite at least one evidence_ref. An
            interpretation may still appear in `findings` (labeled as an
            interpretation in its own text), but it must still cite the
            evidence_ref(s) its interpretation is drawn from.
  Rule C -- suggested_next_step.action_code must be one of the small,
            already-non-executable ActionCode values. Because ActionCode is
            a closed Pydantic/JSON-schema enum, a value outside this set
            cannot even survive structured-output parsing -- this rule is
            enforced twice (by the type system AND explicitly here) so the
            prohibition stays legible and independently verifiable even if
            the schema's enforcement mechanism ever changes.
  Rule D -- a narrow regex-based check across every generated narrative
            field (summary, each finding's statement, each
            missing_or_conflicting_evidence entry, and the suggested next
            step's rationale) for an explicit claim of consequential
            authority -- the model asserting THAT IT approves, denies,
            reverses, or pays a claim, approves or denies a prior
            authorization, or determines medical necessity. This is a
            pattern-matching guardrail for a small, specific set of phrase
            shapes -- NOT a keyword blacklist and NOT a general subject/
            negation grammar. A prohibited verb+object is flagged only
            when it is imperative (sentence-initial: "Approve the
            claim.") or DIRECTLY governed, with no intervening word, by a
            small closed set of authority-claiming subjects ("we", "this
            system", "recommend", ...: "We approve the claim.", "This
            system approves the claim."). Requiring that strict adjacency
            is what lets a negation or modal in between fall through
            un-flagged without a separate negation blacklist ("This
            system does not approve the claim.", "The copilot cannot
            approve the claim." -- the intervening word breaks the
            adjacency the pattern requires) and what correctly excludes a
            third-party historical fact ("The payer denied the claim.",
            "The insurer denied the claim." -- "payer"/"insurer" are not
            recognized authority-claiming subjects, so the verb+object is
            never flagged despite appearing as a literal substring).
            Passive-voice historical facts ("The claim was denied.") are
            additionally safe by construction, since there the object
            precedes the verb. Two more shapes are covered explicitly: a
            passive modal recommendation ("The claim should be
            approved.") and a bare object with no determiner ("Approve
            claim CLM-1001."). See PROHIBITED_AUTHORITY_PATTERNS' own
            comments and tests/test_application_brief_validator.py for
            the exact boundary, including known remaining gaps (e.g. an
            intervening modal like "We must approve the claim." is not
            currently caught).

THIS IS NOT:
  - a hallucination detector;
  - a universal hallucination detector of any kind;
  - a semantic-entailment checker (whether the evidence actually SUPPORTS
    what a finding says is out of scope -- only whether a reference id
    EXISTS is checked);
  - a comprehensive prompt-injection or jailbreak defense;
  - a general semantic safety classifier -- Rule D is a small set of
    targeted, deterministic phrase-shape patterns, not a model of intent
    or meaning. False positives and false negatives remain possible for
    phrasing outside the specific shapes documented above;
  - a substitute for human review.

It exists to catch a small number of objectively-checkable defects
deterministically and cheaply, without a second LLM call.
"""

from __future__ import annotations

import re

from application.models import (
    ActionCode,
    AssembledContext,
    InvestigationBrief,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
)

# Rule C: the only advisory action codes this prototype allows a brief to
# suggest. Identical to the full ActionCode enum today -- kept as an
# explicit, separately-declared allowlist (rather than "trust the enum")
# so the prohibition is legible on its own and stays correct even if
# ActionCode is ever extended for an unrelated reason before this list is
# revisited. See docstring above for why the schema alone already makes
# this rule currently unreachable in practice.
ALLOWED_ACTION_CODES = frozenset(
    {
        ActionCode.EXPLAIN_RECORDED_STATUS,
        ActionCode.VERIFY_AUTHORIZATION_INFORMATION,
        ActionCode.REQUEST_INFORMATION,
        ActionCode.HUMAN_REVIEW,
    }
)

# Documented for clarity, not used in the check itself (ActionCode's own
# closed enum already makes these unrepresentable) -- these are exactly
# the executable/adjudicative action names this prototype must never
# support, named here so the prohibition is explicit and auditable.
PROHIBITED_ACTION_NAMES = frozenset(
    {
        "APPROVE_CLAIM",
        "DENY_CLAIM",
        "REVERSE_CLAIM",
        "PAY_CLAIM",
        "APPROVE_AUTHORIZATION",
        "DENY_AUTHORIZATION",
        "DETERMINE_MEDICAL_NECESSITY",
    }
)

# Rule D (post-audit remediation). Each verb is listed in its inflected
# forms explicitly (English irregulars -- "deny"/"denied", "pay"/"paid" --
# don't concatenate cleanly from a suffix). A prohibited verb+object is
# flagged ONLY when the verb is either (a) the first word of a sentence
# (imperative: "Approve the claim.") or (b) DIRECTLY governed -- with no
# intervening word -- by a small, closed set of authority-claiming
# subjects ("we", "this system", "recommend", ...). Requiring STRICT
# adjacency between subject and verb is what lets a negation or modal in
# between fall through un-flagged without a separate negation blacklist:
# "This system does not approve the claim." has "does not" between the
# trigger subject and the verb, which breaks the adjacency the pattern
# requires, so it never matches. This also correctly excludes a
# third-party historical fact like "The payer denied the claim." --
# "payer" is not a recognized authority-claiming subject, so the verb+
# object is never flagged despite appearing as a literal substring.
#
# A passive-voice historical fact ("The claim was denied.") is additionally
# safe by construction: there the object precedes the verb, and none of
# these patterns match verb-then-object in the wrong order. A bare mention
# of the word ("denial reason", "claim status is DENIED") never matches
# either, since "denial"/"DENIED" (a status value) are not verb-form
# tokens these patterns look for ("deny"/"denies"/"denied"/"denying" as
# whole words only).
#
# Two additional narrow shapes are covered explicitly: a passive modal
# recommendation ("The claim should be approved.") and a bare object with
# no determiner ("Approve claim CLM-1001.").
_VERB_FORMS = {
    "approve": ("approve", "approves", "approved", "approving"),
    "deny": ("deny", "denies", "denied", "denying"),
    "reverse": ("reverse", "reverses", "reversed", "reversing"),
    "pay": ("pay", "pays", "paid", "paying"),
}
_ALL_VERB_FORMS = "(?:" + "|".join(form for forms in _VERB_FORMS.values() for form in forms) + ")"

# A determiner ("the"/"this") is OPTIONAL so "approve claim CLM-1001" (no
# determiner) is still caught, but the literal noun must immediately
# follow the verb either way.
_OBJECT_NOUNS = r"(?:(?:the|this)\s+)?(?:claim|prior\s+authorization|authorization)\b"

# Authority-claiming subjects: first-person, self-referential (the system/
# copilot/prototype/assistant referring to itself), or a recommendation
# verb ("recommend"/"suggest"/"advise" already implies a first-person
# recommender regardless of what precedes it). Deliberately NOT a general
# subject grammar -- a small, closed, auditable list.
_AUTHORITY_SUBJECTS = (
    r"we|i|you|this\s+system|the\s+system|this\s+copilot|the\s+copilot|"
    r"this\s+prototype|this\s+assistant|the\s+assistant|recommend|suggest|advise"
)

# Sentence-start = start of the field's text, or immediately after a
# sentence-ending punctuation mark + whitespace. Fields are not fully
# sentence-tokenized (see "THIS IS NOT" below) -- this simple anchor
# covers the single/multi-sentence field shapes these narrative fields
# actually take.
_SENTENCE_START = r"(?:^|[.!?]\s+)"

PROHIBITED_AUTHORITY_PATTERNS: list[re.Pattern[str]] = [
    # Imperative: the prohibited verb is the first word of a sentence.
    re.compile(rf"{_SENTENCE_START}{_ALL_VERB_FORMS}\b\s+{_OBJECT_NOUNS}", re.IGNORECASE),
    # First-person / self-referential / recommendation: an authority-claiming
    # subject DIRECTLY governs the prohibited verb.
    re.compile(rf"\b(?:{_AUTHORITY_SUBJECTS})\s+{_ALL_VERB_FORMS}\b\s+{_OBJECT_NOUNS}", re.IGNORECASE),
    # Passive modal recommendation: "the/this claim should be approved/denied/paid/reversed".
    re.compile(
        r"\b(?:the|this)\s+(?:claim|prior\s+authorization|authorization)\b[^.\n]{0,20}"
        r"\bshould\s+be\s+(?:approved|denied|paid|reversed)\b",
        re.IGNORECASE,
    ),
    # "This system determines medical necessity." / "...determine medical necessity."
    re.compile(r"\bdetermines?\b[^.\n]{0,60}\bmedical\s+necessity\b", re.IGNORECASE),
]


def _find_prohibited_authority_phrase(text: str) -> str | None:
    """Return the first matched prohibited-authority phrase in `text`, or
    None. Narrow pattern match only -- see module docstring."""
    for pattern in PROHIBITED_AUTHORITY_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def validate_investigation_brief(
    brief: InvestigationBrief, context: AssembledContext
) -> ValidationResult:
    """Run every deterministic check against one DRAFTED InvestigationBrief.

    `context` must be the SAME AssembledContext that was actually rendered
    into the prompt for this brief (application/investigation_service.py
    passes the one it just built) -- Rule A validates against exactly what
    the model was shown, never a different or re-fetched context.
    """
    issues: list[ValidationIssue] = []
    known_ref_ids = {ref.ref_id for ref in context.references}

    # Rule A: every cited evidence_ref must exist.
    for index, finding in enumerate(brief.findings):
        for ref in finding.evidence_refs:
            if ref not in known_ref_ids:
                issues.append(
                    ValidationIssue(
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
                ValidationIssue(
                    rule="evidence_reference_existence",
                    detail=(
                        f"suggested_next_step.evidence_refs cites {ref!r}, which does not "
                        "exist in the evidence made available for this request."
                    ),
                )
            )

    # Rule B: every Finding must cite at least one evidence_ref.
    for index, finding in enumerate(brief.findings):
        if not finding.evidence_refs:
            issues.append(
                ValidationIssue(
                    rule="finding_requires_evidence_ref",
                    detail=(
                        f"findings[{index}] ({finding.statement!r}) cites no evidence_refs."
                    ),
                )
            )

    # Rule C: suggested_next_step.action_code must be an approved advisory action.
    if brief.suggested_next_step.action_code not in ALLOWED_ACTION_CODES:
        issues.append(
            ValidationIssue(
                rule="advisory_action_allowlist",
                detail=(
                    f"suggested_next_step.action_code "
                    f"{brief.suggested_next_step.action_code!r} is not in the approved "
                    f"advisory action allowlist {sorted(a.value for a in ALLOWED_ACTION_CODES)}."
                ),
            )
        )

    # Rule D: no generated narrative text may claim consequential authority.
    narrative_fields: list[tuple[str, str]] = [("summary", brief.summary)]
    for index, finding in enumerate(brief.findings):
        narrative_fields.append((f"findings[{index}].statement", finding.statement))
    for index, item in enumerate(brief.missing_or_conflicting_evidence):
        narrative_fields.append((f"missing_or_conflicting_evidence[{index}]", item))
    narrative_fields.append(
        ("suggested_next_step.rationale", brief.suggested_next_step.rationale)
    )

    for field_name, text in narrative_fields:
        phrase = _find_prohibited_authority_phrase(text)
        if phrase is not None:
            issues.append(
                ValidationIssue(
                    rule="prohibited_authority_language",
                    detail=(
                        f"{field_name} contains a claim of consequential authority "
                        f"({phrase!r}): {text!r}"
                    ),
                )
            )

    status = ValidationStatus.FAILED if issues else ValidationStatus.PASSED
    return ValidationResult(status=status, issues=issues)
