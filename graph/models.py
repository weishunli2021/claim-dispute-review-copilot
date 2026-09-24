"""Lightweight, NetworkX-independent typed structures for graph retrieval results.

Nothing outside graph/ should ever need to import networkx directly:
graph/retriever.py converts NetworkX's internal graph representation into
these plain Pydantic models before returning anything, so the rest of the
application (and any future consumer of graph evidence, such as a context
assembler) depends only on these types, not on the graph library used to
build the graph.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """One node in the knowledge graph: its stable id, declared type, and properties.

    `properties` mirrors the underlying domain record's own fields (see
    graph/builder.py) -- it is provenance, not derived or inferred data.
    """

    node_id: str
    node_type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    """One directed edge in the knowledge graph.

    `relation` is always one of the fixed relation-type constants in
    graph/builder.py (e.g. "BELONGS_TO", "HAS_BENEFIT") -- a structural
    fact about how two records reference each other, never a business
    conclusion (e.g. there is no "CAUSED_DENIAL" relation).
    """

    source_id: str
    relation: str
    target_id: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphContext(BaseModel):
    """A fact bundle returned by a graph retrieval function: the root node's
    id plus every node and relationship found within the requested
    traversal depth. Carries no derived, explanatory, or natural-language
    fields -- see graph/retriever.py.
    """

    root_id: str
    max_hops: int
    nodes: list[GraphNode] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)
