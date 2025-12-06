# Path: FLOW/titan/schemas/memory.py
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime


def new_memory_id(prefix: str = "m") -> str:
    return f"{prefix}{uuid4().hex[:10]}"


class MemoryRecord(BaseModel):
    """
    A single memory record for semantic memory / episodic store.
    Embeddings optional and can be added asynchronously.
    """
    id: str = Field(default_factory=new_memory_id)
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    source: Optional[str] = None  # e.g. "planner.dsl", "executor.event", "user.input"

    class Config:
        extra = "forbid"

    def with_embedding(self, emb: List[float]) -> "MemoryRecord":
        self.embedding = emb
        return self

    def to_index_doc(self) -> Dict[str, Any]:
        """Return the serializable document that will be stored in vector DB + sqlite."""
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "created_at": self.created_at,
            "source": self.source,
        }
