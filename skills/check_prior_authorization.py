"""Prior authorization check: the reusable capability of gathering
authorization evidence for a member/service pair.

Reuses tools.prior_auth_tool.get_prior_authorizations and
rag.retriever.search_policy exactly as-is. Never concludes that an empty
list of authorization records means an authorization was denied -- "no
matching record" and "a record exists with a denied/expired status" are
different facts, and every returned record's status, effective_date,
expiration_date, and service_code are preserved exactly as retrieved,
never summarized away. Never makes a final claim-payment determination:
whether any particular record's window actually covers a specific date
of service is left to a future reasoning layer, not decided here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date as date_type
from typing import Optional

from rag.chunking import LARGE
from rag.retriever import search_policy
from skills._debug import print_skill_result
from skills.base import RiskLevel, SkillInput, SkillMetadata, SkillResult, SkillStatus
from tools.prior_auth_tool import get_prior_authorizations

METADATA = SkillMetadata(
    name="check_prior_authorization",
    version="1.0.0",
    description=(
        "Gather prior-authorization evidence for a member/service pair: every matching "
        "authorization record (status, effective/expiration window, service) plus relevant "
        "policy evidence. Does not determine whether any record applies to a specific claim, "
        "and never treats an empty result as an assumed denial."
    ),
    capabilities=["prior_authorization_lookup", "authorization_evidence"],
    allowed_tools=[
        "tools.prior_auth_tool.get_prior_authorizations",
        "rag.retriever.search_policy",
    ],
    risk_level=RiskLevel.LOW,
)


class CheckPriorAuthorizationInput(SkillInput):
    member_id: str
    service_code: str
    date_of_service: Optional[date_type] = None


def check_prior_authorization(
    member_id: str,
    service_code: str,
    date_of_service: Optional[date_type] = None,
    top_k_policy: int = 3,
) -> SkillResult:
    """Run the prior-authorization-check capability for one member/service pair.

    date_of_service, if given, is carried through as evidence context
    only -- this function never compares it against a returned record's
    effective/expiration window to decide anything; that comparison
    belongs to a future reasoning layer.
    """
    try:
        authorizations = get_prior_authorizations(member_id, service_code)
    except ValueError as exc:
        return SkillResult(
            skill_name=METADATA.name, skill_version=METADATA.version, status=SkillStatus.ERROR, error=str(exc)
        )

    # An empty list is a complete, valid finding -- "no authorization on
    # file" -- not a gap in this skill's own work, so status stays
    # COMPLETED either way. See tools/prior_auth_tool.py's own contract.
    missing = [] if authorizations else ["prior_authorization"]

    query = (
        f"Is prior authorization required for {service_code}, and how are effective and "
        "expiration dates evaluated?"
    )
    policy_chunks = search_policy(query, top_k=top_k_policy, config=LARGE, provider_name="semantic")

    return SkillResult(
        skill_name=METADATA.name,
        skill_version=METADATA.version,
        status=SkillStatus.COMPLETED,
        evidence={
            "member_id": member_id,
            "service_code": service_code,
            "date_of_service": date_of_service.isoformat() if date_of_service else None,
            "authorizations": [auth.model_dump(mode="json") for auth in authorizations],
            "policy_chunks": [chunk.model_dump(mode="json") for chunk in policy_chunks],
        },
        missing_information=missing,
        next_capability=None,
    )


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Debug utility: run the check_prior_authorization skill. No final answer is generated."
    )
    parser.add_argument("member_id", help="e.g. M-1002")
    parser.add_argument("service_code", help="e.g. MRI-KNEE")
    parser.add_argument("--date-of-service", default=None, help="YYYY-MM-DD, optional")
    args = parser.parse_args(argv)

    date_of_service = date_type.fromisoformat(args.date_of_service) if args.date_of_service else None
    result = check_prior_authorization(args.member_id, args.service_code, date_of_service=date_of_service)
    print_skill_result(METADATA, result)


if __name__ == "__main__":
    _main(sys.argv[1:])
