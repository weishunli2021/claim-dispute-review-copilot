"""Smoke test for application/cli.py: default (non---live) invocation makes
no network call and always labels its output as mocked."""

from __future__ import annotations

import json
import subprocess
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cli_default_invocation_is_mocked_and_prints_json():
    completed = subprocess.run(
        [sys.executable, "-m", "application.cli", "CLM-1003", "Why was my lab claim denied?"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr

    # Streamlit/telemetry warnings may precede the JSON payload on stderr;
    # stdout must be pure JSON.
    output = json.loads(completed.stdout)
    assert output["_live"] is False
    assert output["generation_status"] == "DRAFTED"
    assert "MOCKED" in output["investigation_brief"]["summary"]
