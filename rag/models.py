"""Typed models for the Vector RAG evidence layer.

These carry no business logic and no reasoning -- they represent policy
documents, their stable sections, the chunks produced from them for
embedding, and the chunks a retriever hands back as evidence.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PolicySection(BaseModel):
    """One stable, numbered section of a synthetic policy document."""

    document_id: str
    document_title: str
    section_id: str
    section_title: str
    section_uid: str
    text: str


class PolicyDocument(BaseModel):
    """A loaded synthetic policy document and its sections."""

    document_id: str
    document_title: str
    source_path: str
    sections: list[PolicySection]


class Chunk(BaseModel):
    """A chunk of section text produced by the chunking layer, ready to embed."""

    chunk_id: str
    document_id: str
    document_title: str
    section_id: str
    section_title: str
    text: str


class RetrievedChunk(BaseModel):
    """A chunk of evidence returned by the retriever. No answer, no explanation."""

    chunk_id: str
    document_id: str
    document_title: str
    section_id: str
    section_title: str
    text: str
    score: Optional[float] = None
