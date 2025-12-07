# Path: titan/memory/persistent_annoy_store.py
from __future__ import annotations
import os
import threading
import logging
import json
import sqlite3
import time
from typing import Iterable, List, Dict, Optional, Tuple, Any

from titan.schemas.memory import MemoryRecord

logger = logging.getLogger(__name__)

try:
    from annoy import AnnoyIndex  # type: ignore
    _HAS_ANNOY = True
except Exception:
    _HAS_ANNOY = False

try:
    import numpy as np  # type: ignore
except Exception:
    np = None

DEFAULT_METADB = "data/annoy_meta.db"


class PersistentAnnoyStore:
    """
    Persistent vector store using Annoy if available; otherwise a memory fallback.
    Implements VectorStore Protocol.
    """

    def __init__(
        self,
        vector_dim: int = 1536,
        index_path: str = "data/index.ann",
        meta_db_path: str = DEFAULT_METADB,
        metric: str = "angular",
        n_trees: int = 10,
    ):
        self.vector_dim = int(vector_dim)
        self.index_path = index_path
        self.meta_db_path = meta_db_path
        self.metric = metric
        self.n_trees = int(n_trees)
        self._lock = threading.RLock()
        
        self._mem_index: Dict[int, List[float]] = {}
        self._next_id = 1
        self._annoy: Optional[AnnoyIndex] = None
        self._annoy_built = False

        os.makedirs(os.path.dirname(self.meta_db_path) or ".", exist_ok=True)
        self._init_meta_db()

        if _HAS_ANNOY:
            try:
                self._annoy = AnnoyIndex(self.vector_dim, self.metric)
                if os.path.exists(self.index_path):
                    try:
                        self._annoy.load(self.index_path)
                        self._annoy_built = True
                    except Exception:
                        pass
            except Exception:
                pass

    # --- VectorStore Protocol Implementation ---

    def add(self, record: MemoryRecord) -> None:
        """Protocol implementation: Add a MemoryRecord."""
        if record.embedding:
            self._add_vector(record.embedding, {
                "id": record.id,
                "text": record.text,
                **record.metadata
            })
        else:
            # If no embedding, we might log a warning or just store in SQLite (not implemented here)
            # For now, we only store if embedding exists
            pass

    def add_many(self, records: List[MemoryRecord]) -> None:
        for r in records:
            self.add(r)

    def query_by_text(self, text: str, embed_fn: callable, top_k: int = 10) -> List[Dict[str, Any]]:
        """Query by text using the provided embed_fn."""
        embedding = embed_fn(text)
        return self.query_by_embedding(embedding, top_k)

    def query_by_embedding(self, embedding: List[float], top_k: int = 10) -> List[Dict[str, Any]]:
        """Protocol implementation: Query by embedding."""
        results = self._query_vector(embedding, top_k)
        # Convert to list of dicts as expected by interface
        out = []
        for vid, dist, meta in results:
            out.append({
                "score": dist,
                **meta
            })
        return out

    def persist(self) -> None:
        self.save()

    # --- Internal Implementation (Original logic) ---

    def _init_meta_db(self):
        self._meta_conn = sqlite3.connect(self.meta_db_path, check_same_thread=False)
        self._meta_conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY, vector_id INTEGER UNIQUE, metadata TEXT, created INTEGER)"
        )
        self._meta_conn.commit()

    def _insert_meta(self, vector_id: int, metadata: Dict[str, Any]):
        now = int(time.time())
        self._meta_conn.execute(
            "INSERT OR REPLACE INTO meta (vector_id, metadata, created) VALUES (?, ?, ?)",
            (vector_id, json.dumps(metadata, default=str), now),
        )
        self._meta_conn.commit()

    def _get_meta(self, vector_id: int) -> Optional[Dict[str, Any]]:
        cur = self._meta_conn.execute("SELECT metadata FROM meta WHERE vector_id = ?", (vector_id,))
        row = cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return {}

    def _alloc_id(self) -> int:
        with self._lock:
            cur = self._meta_conn.execute("SELECT MAX(vector_id) FROM meta")
            row = cur.fetchone()
            if row and row[0]:
                self._next_id = int(row[0]) + 1
            vid = self._next_id
            self._next_id += 1
            return vid

    def _add_vector(self, vector: Iterable[float], metadata: Optional[Dict[str, Any]] = None) -> int:
        vec = list(vector)
        if len(vec) != self.vector_dim:
            # In production, maybe pad or truncate; here we warn
            logger.warning(f"Vector dim mismatch {len(vec)} vs {self.vector_dim}")
            return -1

        with self._lock:
            vid = self._alloc_id()
            if self._annoy is not None:
                try:
                    self._annoy.add_item(vid, vec)
                    self._annoy_built = False
                except Exception:
                    self._mem_index[vid] = vec
            else:
                self._mem_index[vid] = vec
            self._insert_meta(vid, metadata or {})
            return vid

    def _query_vector(self, vector: Iterable[float], top_k: int = 10) -> List[Tuple[int, float, Optional[Dict[str, Any]]]]:
        q = list(vector)
        results: List[Tuple[int, float]] = []
        
        with self._lock:
            # 1. Annoy Search
            if self._annoy is not None and (self._annoy_built or len(self._mem_index) == 0):
                try:
                    ids, dists = self._annoy.get_nns_by_vector(q, top_k, include_distances=True)
                    # Convert distance (angular) to similarity if needed, or keep as distance
                    results = list(zip(ids, dists))
                except Exception:
                    pass

            # 2. Memory Fallback (Brute force) if Annoy failed or empty
            if not results and self._mem_index:
                # Simple dot product fallback
                def cosine_sim(v1, v2):
                    return sum(a*b for a,b in zip(v1,v2))
                
                candidates = []
                for vid, vec in self._mem_index.items():
                    score = cosine_sim(q, vec)
                    candidates.append((vid, score))
                
                candidates.sort(key=lambda x: x[1], reverse=True) # higher score = better
                results = candidates[:top_k]

            out = []
            for vid, d in results:
                meta = self._get_meta(vid)
                out.append((vid, float(d), meta))
            return out

    def save(self) -> None:
        with self._lock:
            if self._annoy is not None:
                try:
                    # Add pending items
                    for vid, vec in self._mem_index.items():
                        self._annoy.add_item(vid, vec)
                    self._annoy.build(self.n_trees)
                    self._annoy.save(self.index_path)
                    self._annoy_built = True
                    self._mem_index.clear()
                except Exception:
                    pass

    def load(self) -> None:
        # handled in init
        pass

    def close(self) -> None:
        try:
            self._meta_conn.close()
        except Exception:
            pass

    def health(self) -> Dict[str, Any]:
        return {"annoy": _HAS_ANNOY, "built": self._annoy_built}