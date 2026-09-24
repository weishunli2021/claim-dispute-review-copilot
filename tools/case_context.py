"""Assembles the deterministic structured facts for a single claim.

This module composes the outputs of the deterministic tools into one
bundle for a human (or, later, an agent) to read. It performs NO
reasoning: it does not explain, summarize, or infer why a claim was
approved or denied -- it only gathers the records that already exist.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from pydantic import BaseModel, Field

from tools.benefits_tool import get_benefits
from tools.claim_tool import get_claim
from tools.member_tool import get_member
from tools.models import Benefit, Claim, Member, Plan, PriorAuthorization, Provider
from tools.plan_tool import get_plan
from tools.prior_auth_tool import get_prior_authorizations
from tools.provider_tool import get_provider


class CaseContext(BaseModel):
    """A fact bundle for one claim. Every field may be None/empty if the
    corresponding record does not exist or the claim does not reference
    it. This model carries no derived or explanatory fields on purpose.
    """

    claim: Optional[Claim] = None
    member: Optional[Member] = None
    plan: Optional[Plan] = None
    benefit: Optional[Benefit] = None
    prior_authorizations: list[PriorAuthorization] = Field(default_factory=list)
    servicing_provider: Optional[Provider] = None
    ordering_provider: Optional[Provider] = None


def get_case_context(claim_id: str) -> CaseContext:
    """Assemble every deterministic record related to claim_id.

    Composes tool outputs only (get_claim, get_member, get_plan,
    get_benefits, get_prior_authorizations, get_provider). Does not
    explain or infer why the claim was approved or denied, and does not
    determine which (if any) prior authorization applied -- that is a
    future reasoning layer's job, not this function's.

    A well-formed but unknown claim_id returns a CaseContext with every
    field empty. Raises ValueError if claim_id is None, not a string,
    blank, or contains whitespace (propagated from get_claim).
    """
    claim = get_claim(claim_id)
    if claim is None:
        return CaseContext()

    return CaseContext(
        claim=claim,
        member=get_member(claim.member_id),
        plan=get_plan(claim.plan_id),
        benefit=get_benefits(claim.plan_id, claim.service_code),
        prior_authorizations=get_prior_authorizations(claim.member_id, claim.service_code),
        servicing_provider=get_provider(claim.provider_id),
        ordering_provider=(
            get_provider(claim.ordering_provider_id) if claim.ordering_provider_id else None
        ),
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Debug utility: print the deterministic structured facts for a claim.",
    )
    parser.add_argument("claim_id", help="e.g. CLM-1001")
    args = parser.parse_args(argv)

    context = get_case_context(args.claim_id)
    print(context.model_dump_json(indent=2))


if __name__ == "__main__":
    _main(sys.argv[1:])
