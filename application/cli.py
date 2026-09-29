"""Opt-in debug CLI for the H1 application service.

    ./.venv/Scripts/python.exe -m application.cli CLM-1001 "Why was this claim denied?"

By default this uses FakeInvestigationBriefAdapter and makes NO network
call and NO paid API request -- every printed result is clearly labeled
`"live": false`. Pass --live to perform exactly one real OpenAI call using
OPENAI_API_KEY / LLM_MODEL from the environment (see .env.example); if
those are not configured, the printed result will show
generation_status="FAILED" with generation_failure_category="CONFIGURATION"
rather than crash or silently fall back to a mock.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from application.investigation_service import run_investigation
from application.llm_adapter import FakeInvestigationBriefAdapter, OpenAIInvestigationBriefAdapter


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Debug utility: run the H1 application service (existing evidence workflow + "
            "context assembly + investigation-brief generation) for one claim/question."
        )
    )
    parser.add_argument("claim_id", help="e.g. CLM-1001")
    parser.add_argument("query", help="e.g. 'Why was this claim denied?'")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Perform one real OpenAI API call (requires OPENAI_API_KEY and LLM_MODEL set, "
            "e.g. via a local .env). Without this flag, a fixed mocked adapter is used and "
            "no network call is made -- this is the default specifically so this CLI never "
            "triggers a paid API call by accident."
        ),
    )
    args = parser.parse_args(argv)

    adapter = OpenAIInvestigationBriefAdapter() if args.live else FakeInvestigationBriefAdapter()
    result = run_investigation(args.claim_id, args.query, adapter=adapter)

    output = result.model_dump(mode="json")
    output["_live"] = args.live  # explicit, so mocked vs. real output is never ambiguous in logs
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    _main(sys.argv[1:])
