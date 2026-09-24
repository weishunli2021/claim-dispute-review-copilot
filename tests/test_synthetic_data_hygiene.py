"""Tests that the data fixtures are clearly synthetic, not real payer data."""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# Real payer/insurer names that must never appear in the synthetic fixtures.
FORBIDDEN_SUBSTRINGS = [
    "humana",
    "unitedhealth",
    "united healthcare",
    "aetna",
    "cigna",
    "anthem",
    "blue cross",
    "blue shield",
    "kaiser permanente",
    "molina",
    "centene",
]

DATA_FILES = [
    "members.json",
    "plans.json",
    "claims.json",
    "benefits.json",
    "prior_authorizations.json",
    "providers.json",
]


def test_all_expected_data_files_exist():
    missing = [f for f in DATA_FILES if not (DATA_DIR / f).is_file()]
    assert not missing, f"Missing data files: {missing}"


def test_data_files_contain_no_real_payer_identifiers():
    for filename in DATA_FILES:
        text = (DATA_DIR / filename).read_text(encoding="utf-8").lower()
        found = [s for s in FORBIDDEN_SUBSTRINGS if s in text]
        assert not found, f"{filename} contains disallowed real-world identifier(s): {found}"


def test_data_readme_documents_synthetic_only_policy():
    readme = (DATA_DIR / "README.md").read_text(encoding="utf-8").lower()
    assert "synthetic" in readme
