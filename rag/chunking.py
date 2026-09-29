"""Configurable chunking of policy sections into embeddable text chunks.

Two named configurations are provided for experimentation (SMALL, LARGE).
Nothing downstream is hard-wired to either -- every function that needs a
configuration takes one explicitly, so the two can be compared (see
evals/retrieval_eval.py) rather than one being baked in as "the" chunking
strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.models import Chunk, PolicyDocument


@dataclass(frozen=True)
class ChunkingConfig:
    """One named chunking strategy: target chunk size and overlap, in characters."""

    name: str
    chunk_size: int
    chunk_overlap: int


SMALL = ChunkingConfig(name="SMALL", chunk_size=400, chunk_overlap=60)
LARGE = ChunkingConfig(name="LARGE", chunk_size=900, chunk_overlap=120)


def chunk_documents(documents: list[PolicyDocument], config: ChunkingConfig) -> list[Chunk]:
    """Split every section of every document into Chunks under `config`.

    Each chunk keeps its parent section's document_id, document_title,
    section_id, and section_title as metadata. Its chunk_id is built
    deterministically from the section's stable section_uid, the chunking
    config name, and the chunk's position within that section --
    re-chunking the same source documents under the same config always
    reproduces the same chunk_ids, which lets the vector store rebuild
    without accumulating duplicates.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks: list[Chunk] = []
    for document in documents:
        for section in document.sections:
            pieces = splitter.split_text(section.text) or [section.text]
            for position, piece in enumerate(pieces):
                chunk_id = f"{section.section_uid}::{config.name.lower()}::{position:02d}"
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        document_id=section.document_id,
                        document_title=section.document_title,
                        section_id=section.section_id,
                        section_title=section.section_title,
                        text=piece.strip(),
                    )
                )
    return chunks
