"""Tests that the synthetic policy documents are clearly synthetic and contain
no real payer identifiers."""

from __future__ import annotations

from rag.document_loader import DEFAULT_DOCUMENTS_DIR, POLICY_FILENAMES

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


def test_all_policy_files_exist():
    missing = [f for f in POLICY_FILENAMES if not (DEFAULT_DOCUMENTS_DIR / f).is_file()]
    assert not missing


def test_policy_documents_contain_no_real_payer_identifiers():
    for filename in POLICY_FILENAMES:
        text = (DEFAULT_DOCUMENTS_DIR / filename).read_text(encoding="utf-8").lower()
        found = [s for s in FORBIDDEN_SUBSTRINGS if s in text]
        assert not found, f"{filename} contains disallowed real-world identifier(s): {found}"


def test_policy_documents_each_declare_synthetic_disclaimer():
    for filename in POLICY_FILENAMES:
        text = (DEFAULT_DOCUMENTS_DIR / filename).read_text(encoding="utf-8").lower()
        assert "synthetic data notice" in text
