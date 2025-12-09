# titan/schemas/memory.py
from __future__ import annotations
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime


def new_memory_id(prefix: str = "m") -> str:
    return f"{prefix}{uuid4().hex[:10]}"


class MemoryRecord(BaseModel):
    """
    Unified memory record used by:
    - episodic store
    - semantic memory vector DB
    - skill-generated annotations
    - planner context history
    """
    id: str = Field(default_factory=new_memory_id)
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Embedding is optional and added lazily
    embedding: Optional[List[float]] = None

    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )

    # Source field is critical for skill-based summarization
    source: Optional[str] = Field(
        default=None,
        description="Where this memory came from: planner, executor, perception, skill, user"
    )

    model_config = {"extra": "forbid"}

    # ----------------------------------------
    # Embedding Helpers
    # ----------------------------------------
    def with_embedding(self, vec: List[float]) -> "MemoryRecord":
        self.embedding = vec
        return self

    # ----------------------------------------
    # Vector DB serialization
    # ----------------------------------------
    def to_index_doc(self) -> Dict[str, Any]:
        """
        Converts memory into a dict suitable for:
        - Pinecone / Annoy / FAISS
        - SQLite JSON storage
        - JSON event logs
        """
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "created_at": self.created_at,
            "source": self.source,
        }
