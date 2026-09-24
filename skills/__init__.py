"""Reusable Skills: business capabilities that may coordinate multiple
tools/context sources, invoked by the agent orchestrator (agents/).

See skills/base.py for the shared contract (SkillMetadata, SkillInput,
SkillResult) and skills/registry.py for the deterministic SkillRegistry.
Call build_default_registry() to get a SkillRegistry with all four skills
built in this stage registered. This package's own import does NOT do
that automatically: eagerly importing every skill submodule from
__init__.py would make `python -m skills.<name>` re-execute that same
submodule as `__main__` after it was already imported once as a package
member, which Python warns about. Deferring registration to an explicit
call avoids that entirely.
"""

from __future__ import annotations

from skills.registry import SkillRegistry


def build_default_registry() -> SkillRegistry:
    """Construct a fresh SkillRegistry with investigate_claim,
    explain_benefit, check_prior_authorization, and escalate_case (all
    version 1.0.0) registered."""
    from skills.check_prior_authorization import METADATA as check_prior_auth_metadata
    from skills.check_prior_authorization import check_prior_authorization
    from skills.escalate_case import METADATA as escalate_case_metadata
    from skills.escalate_case import escalate_case
    from skills.explain_benefit import METADATA as explain_benefit_metadata
    from skills.explain_benefit import explain_benefit
    from skills.investigate_claim import METADATA as investigate_claim_metadata
    from skills.investigate_claim import investigate_claim

    registry = SkillRegistry()
    registry.register(investigate_claim_metadata, investigate_claim)
    registry.register(explain_benefit_metadata, explain_benefit)
    registry.register(check_prior_auth_metadata, check_prior_authorization)
    registry.register(escalate_case_metadata, escalate_case)
    return registry
