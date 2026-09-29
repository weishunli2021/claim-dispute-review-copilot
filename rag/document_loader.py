"""Loads synthetic policy Markdown documents into typed PolicyDocument objects.

Every policy document must carry a visible synthetic-data disclaimer near
its top; a document missing it is rejected rather than silently loaded, so
this loader can never mistake a real or production document for part of
this synthetic corpus.
"""

from __future__ import annotations

import re
from pathlib import Path

from rag.models import PolicyDocument, PolicySection

DEFAULT_DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "documents"

# The explicit corpus. Deliberately not a glob over documents/*.md: that
# directory also holds documents/README.md, which is documentation, not a
# policy document, and would otherwise fail every check below.
POLICY_FILENAMES = [
    "imaging_policy.md",
    "prior_authorization_policy.md",
    "claims_denial_guide.md",
    "benefits_guide.md",
    "provider_network_policy.md",
    "appeals_guide.md",
]

REQUIRED_DISCLAIMER_MARKER = "synthetic data notice"
_DISCLAIMER_SEARCH_WINDOW = 1000

_TITLE_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_SECTION_PATTERN = re.compile(r"^##\s+([A-Z]+-\d+)\.\s+(.+?)\s*$", re.MULTILINE)


class DocumentLoadError(RuntimeError):
    """Raised when a policy document is missing, malformed, or missing its disclaimer."""


def _extract_title(text: str, path: Path) -> str:
    match = _TITLE_PATTERN.search(text)
    if not match:
        raise DocumentLoadError(f"{path.name}: no top-level '# Title' heading found")
    return match.group(1)


def _extract_sections(
    text: str, document_id: str, document_title: str, path: Path
) -> list[PolicySection]:
    matches = list(_SECTION_PATTERN.finditer(text))
    if not matches:
        raise DocumentLoadError(f"{path.name}: no '## ID. Title' sections found")

    sections: list[PolicySection] = []
    seen_ids: set[str] = set()
    for index, match in enumerate(matches):
        section_id = match.group(1)
        section_title = match.group(2)
        if section_id in seen_ids:
            raise DocumentLoadError(f"{path.name}: duplicate section id {section_id!r}")
        seen_ids.add(section_id)

        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if not body:
            raise DocumentLoadError(f"{path.name}: section {section_id!r} has no body text")

        sections.append(
            PolicySection(
                document_id=document_id,
                document_title=document_title,
                section_id=section_id,
                section_title=section_title,
                section_uid=f"{document_id}::{section_id}",
                text=body,
            )
        )
    return sections


def load_policy_document(path: Path) -> PolicyDocument:
    """Load one Markdown policy file into a validated PolicyDocument.

    Raises DocumentLoadError if the file is missing, has no synthetic-data
    disclaimer near the top, has no title, has no numbered '## ID. Title'
    sections, or has a duplicate/empty section.
    """
    if not path.is_file():
        raise DocumentLoadError(f"Missing policy document: {path}")

    text = path.read_text(encoding="utf-8")

    if REQUIRED_DISCLAIMER_MARKER not in text[:_DISCLAIMER_SEARCH_WINDOW].lower():
        raise DocumentLoadError(
            f"{path.name}: missing required synthetic-data disclaimer near the top of the file"
        )

    document_id = path.stem
    document_title = _extract_title(text, path)
    sections = _extract_sections(text, document_id, document_title, path)

    return PolicyDocument(
        document_id=document_id,
        document_title=document_title,
        source_path=str(path),
        sections=sections,
    )


def load_all_policy_documents(documents_dir: Path | None = None) -> list[PolicyDocument]:
    """Load every document in POLICY_FILENAMES from documents_dir (default: documents/)."""
    base_dir = documents_dir or DEFAULT_DOCUMENTS_DIR
    return [load_policy_document(base_dir / filename) for filename in POLICY_FILENAMES]
