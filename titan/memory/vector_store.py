# Path: FLOW/titan/memory/vector_store.py
from __future__ import annotations
from typing import Protocol, List, Dict, Any, Optional
from titan.schemas.memory import MemoryRecord


class VectorStore(Protocol):
    """
    Protocol for vector stores used by TITAN memory subsystem.
    Implementations must be thread-safe.
    """

    def add(self, record: MemoryRecord) -> None:
        """Add a single MemoryRecord (embedding may be present or None)."""

    def add_many(self, records: List[MemoryRecord]) -> None:
        """Add many records efficiently."""

    def query_by_text(self, text: str, embed_fn: callable, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Query by text: embed the text using embed_fn(text) and return top_k results.
        Results are dicts with at least: id, text, metadata, score, created_at.
        """

    def query_by_embedding(self, embedding: List[float], top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Query by embedding vector. Returns top_k results sorted by decreasing score.
        """

    def persist(self) -> None:
        """Persist short-term buffers to long-term storage (e.g., rebuild indexes)."""

    def close(self) -> None:
        """Close DB connections / release resources."""
