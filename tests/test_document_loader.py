"""Tests for the policy document loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag.document_loader import (
    DocumentLoadError,
    POLICY_FILENAMES,
    load_all_policy_documents,
    load_policy_document,
)


def test_load_all_policy_documents_succeeds():
    documents = load_all_policy_documents()
    assert len(documents) == len(POLICY_FILENAMES)
    assert {d.document_id for d in documents} == {Path(f).stem for f in POLICY_FILENAMES}
    assert all(d.document_title for d in documents)
    assert all(d.sections for d in documents)


def test_section_ids_and_titles_preserved():
    documents = {d.document_id: d for d in load_all_policy_documents()}
    imaging = documents["imaging_policy"]
    section_ids = [s.section_id for s in imaging.sections]
    assert section_ids == ["IMG-1", "IMG-2", "IMG-3", "IMG-4"]

    img_2 = next(s for s in imaging.sections if s.section_id == "IMG-2")
    assert img_2.section_title == "Outpatient MRI Prior Authorization Requirement"
    assert "prior authorization" in img_2.text.lower()


def test_section_uid_is_deterministic_and_stable():
    documents = load_all_policy_documents()
    img_doc = next(d for d in documents if d.document_id == "imaging_policy")
    img_2 = next(s for s in img_doc.sections if s.section_id == "IMG-2")
    assert img_2.section_uid == "imaging_policy::IMG-2"


def test_document_missing_disclaimer_is_rejected(tmp_path):
    bad_doc = tmp_path / "no_disclaimer.md"
    bad_doc.write_text(
        "# A Policy\n\n## X-1. A Section\n\nSome body text with no disclaimer at all.\n",
        encoding="utf-8",
    )
    with pytest.raises(DocumentLoadError, match="disclaimer"):
        load_policy_document(bad_doc)


def test_document_missing_sections_is_rejected(tmp_path):
    bad_doc = tmp_path / "no_sections.md"
    bad_doc.write_text(
        "# A Policy\n\n> Synthetic Data Notice: fictional.\n\nJust a paragraph, no numbered sections.\n",
        encoding="utf-8",
    )
    with pytest.raises(DocumentLoadError, match="sections"):
        load_policy_document(bad_doc)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(DocumentLoadError, match="Missing policy document"):
        load_policy_document(tmp_path / "does_not_exist.md")
