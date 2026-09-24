"""Shared debug-CLI printing for skill modules.

Not part of the skill contract itself (see skills/base.py) -- just
formatting shared by each skill's `python -m skills.<name>` entry point,
so the "SKILL / VERSION / STATUS / DEPENDENCIES USED / EVIDENCE SUMMARY /
MISSING INFORMATION / NEXT CAPABILITY" layout stays identical across all
four skills.
"""

from __future__ import annotations

from skills.base import SkillMetadata, SkillResult


def print_skill_result(metadata: SkillMetadata, result: SkillResult) -> None:
    print("SKILL")
    print(f"  {metadata.name}")
    print()

    print("VERSION")
    print(f"  {metadata.version}")
    print()

    print("STATUS")
    print(f"  {result.status.value}")
    print()

    print("DEPENDENCIES USED")
    if metadata.allowed_tools:
        for dep in metadata.allowed_tools:
            print(f"  {dep}")
    else:
        print("  (none)")
    print()

    print("EVIDENCE SUMMARY")
    if result.evidence:
        for key, value in result.evidence.items():
            if isinstance(value, list):
                print(f"  {key}: {len(value)} item(s)")
            elif isinstance(value, dict):
                print(f"  {key}: present" if value else f"  {key}: (empty)")
            else:
                print(f"  {key}: {value}")
    else:
        print("  (none)")
    print()

    print("MISSING INFORMATION")
    if result.missing_information:
        for item in result.missing_information:
            print(f"  {item}")
    else:
        print("  (none)")
    print()

    print("NEXT CAPABILITY")
    print(f"  {result.next_capability or '(none)'}")

    if result.error:
        print()
        print("ERROR")
        print(f"  {result.error}")
