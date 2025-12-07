# Path: FLOW/titan/memory/in_memory_vector.py
from __future__ import annotations
from typing import List, Dict, Any
from threading import RLock
from titan.schemas.memory import MemoryRecord
import math


def _cosine(a: List[float], b: List[float]) -> float:
    # safe cosine (no normalization assumption)
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryVectorStore:
    """
    Simple memory-backed vector store implementing the VectorStore Protocol.
    Not intended for huge datasets — useful for unit tests and small runs.
    """

    def __init__(self):
        self._lock = RLock()
        self._records = {}  # id -> MemoryRecord
        self._vectors = {}  # id -> embedding

    def add(self, record: MemoryRecord) -> None:
        with self._lock:
            self._records[record.id] = record
            if record.embedding:
                self._vectors[record.id] = record.embedding

    def add_many(self, records: List[MemoryRecord]) -> None:
        for r in records:
            self.add(r)

    def query_by_text(self, text: str, embed_fn: callable, top_k: int = 10) -> List[Dict[str, Any]]:
        emb = embed_fn(text)
        return self.query_by_embedding(emb, top_k)

    def query_by_embedding(self, embedding: List[float], top_k: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            hits = []
            for id_, vec in self._vectors.items():
                score = _cosine(embedding, vec)
                rec = self._records[id_]
                hits.append({
                    "id": rec.id,
                    "text": rec.text,
                    "metadata": rec.metadata,
                    "created_at": rec.created_at,
                    "score": score,
                })
            hits.sort(key=lambda x: x["score"], reverse=True)
            return hits[:top_k]

    def persist(self) -> None:
        # nothing to persist for in-memory
        return

    def close(self) -> None:
        return
