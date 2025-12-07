# Path: titan/memory/persistent_annoy_store.py
from __future__ import annotations
import os
import threading
import logging
import json
import sqlite3
import time
from typing import Iterable, List, Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)

try:
    from annoy import AnnoyIndex  # type: ignore
    _HAS_ANNOY = True
except Exception:
    _HAS_ANNOY = False

try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore

DEFAULT_METADB = "data/annoy_meta.db"


class PersistentAnnoyStore:
    """
    Persistent vector store using Annoy if available; otherwise a memory fallback.
    - Maintains a metadata sqlite DB for id -> metadata mapping.
    - Exposes add/upsert/query/save/load/compact operations.
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

        # in-memory index fallback
        self._mem_index: Dict[int, List[float]] = {}
        self._next_id = 1

        # Annoy index (lazy init)
        self._annoy: Optional[AnnoyIndex] = None
        self._annoy_built = False

        # sqlite metadata
        os.makedirs(os.path.dirname(self.meta_db_path) or ".", exist_ok=True)
        self._init_meta_db()

        # if Annoy present, prepare instance (not build)
        if _HAS_ANNOY:
            try:
                self._annoy = AnnoyIndex(self.vector_dim, self.metric)
                if os.path.exists(self.index_path):
                    try:
                        self._annoy.load(self.index_path)
                        self._annoy_built = True
                        logger.info("Loaded Annoy index from %s", self.index_path)
                    except Exception:
                        logger.warning("Annoy index exists but failed to load; starting fresh")
                else:
                    logger.info("Annoy backend available, will build index on save()")
            except Exception:
                logger.exception("Failed initializing Annoy backend; falling back to in-memory")

    # -------------------------
    # Metadata DB helpers
    # -------------------------
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
            return {"raw": row[0]}

    # -------------------------
    # ID management
    # -------------------------
    def _alloc_id(self) -> int:
        with self._lock:
            # pick largest existing id and increment
            cur = self._meta_conn.execute("SELECT MAX(vector_id) FROM meta")
            row = cur.fetchone()
            if row and row[0]:
                self._next_id = int(row[0]) + 1
            vid = self._next_id
            self._next_id += 1
            return vid

    # -------------------------
    # Add / Upsert / Bulk
    # -------------------------
    def add(self, vector: Iterable[float], metadata: Optional[Dict[str, Any]] = None) -> int:
        """
        Add a single vector. Returns assigned vector id.
        """
        vec = list(vector)
        if len(vec) != self.vector_dim:
            raise ValueError(f"Vector length {len(vec)} != expected dim {self.vector_dim}")

        with self._lock:
            vid = self._alloc_id()
            if self._annoy is not None:
                # Annoy requires ints index insertion before build
                try:
                    self._annoy.add_item(vid, vec)
                except Exception:
                    # fallback to rebuild strategy: re-create index later
                    self._mem_index[vid] = vec
                else:
                    self._annoy_built = False
            else:
                self._mem_index[vid] = vec
            self._insert_meta(vid, metadata or {})
            logger.debug("Added vector id=%s (meta=%s)", vid, bool(metadata))
            return vid

    def bulk_add(self, vectors: Iterable[Tuple[Iterable[float], Optional[Dict[str, Any]]]]) -> List[int]:
        """
        Add multiple vectors: iterable of (vector, metadata) tuples.
        Returns list of vector ids in insertion order.
        """
        ids = []
        with self._lock:
            for vec, meta in vectors:
                ids.append(self.add(vec, meta))
        return ids

    def upsert(self, vector_id: int, vector: Iterable[float], metadata: Optional[Dict[str, Any]] = None) -> int:
        """
        Replace or insert vector with a specified id.
        """
        vec = list(vector)
        if len(vec) != self.vector_dim:
            raise ValueError("vector dim mismatch")
        with self._lock:
            if self._annoy is not None:
                try:
                    # Annoy has no direct update; we store in mem_index and rebuild during save
                    self._mem_index[vector_id] = vec
                except Exception:
                    self._mem_index[vector_id] = vec
            else:
                self._mem_index[vector_id] = vec
            self._insert_meta(vector_id, metadata or {})
            logger.debug("Upserted vector id=%s", vector_id)
            return vector_id

    # -------------------------
    # Query
    # -------------------------
    def query(self, vector: Iterable[float], top_k: int = 10) -> List[Tuple[int, float, Optional[Dict[str, Any]]]]:
        """
        Return list of (vector_id, distance, metadata) for top_k nearest neighbors.
        Distance is Annoy's distance metric (lower is closer).
        """
        q = list(vector)
        if len(q) != self.vector_dim:
            raise ValueError("vector dim mismatch")
        with self._lock:
            results: List[Tuple[int, float]] = []
            # query Annoy if available and built
            if self._annoy is not None and (self._annoy_built or len(self._mem_index) == 0):
                try:
                    ids, dists = self._annoy.get_nns_by_vector(q, top_k, include_distances=True)
                    results = list(zip(ids, dists))
                except Exception:
                    logger.exception("Annoy query failed; falling back to brute force")
                    self._annoy_built = False

            if not results:
                # brute force across in-memory index plus metadata for Annoy items
                cand_items = list(self._mem_index.items())
                # If annoy exists and was used earlier, try to include its content via scanning saved meta
                if self._annoy is not None and self._annoy_built:
                    # we still fallback to scanning: Annoy doesn't provide getting vectors
                    pass
                # brute force distance (cosine approx using dot / l2 fallback)
                def dist(a: List[float], b: List[float]) -> float:
                    if np is not None:
                        a_arr = np.array(a, dtype=float)
                        b_arr = np.array(b, dtype=float)
                        # cosine distance
                        denom = (np.linalg.norm(a_arr) * np.linalg.norm(b_arr))
                        if denom == 0:
                            return float("inf")
                        cos_sim = float(np.dot(a_arr, b_arr) / denom)
                        return 1.0 - cos_sim
                    else:
                        # naive L2
                        s = 0.0
                        for i in range(len(a)):
                            d = a[i] - b[i]
                            s += d * d
                        return s ** 0.5

                # collect
                for vid, vec in cand_items:
                    try:
                        d = dist(q, vec)
                        results.append((vid, d))
                    except Exception:
                        continue
                # sort and trim
                results.sort(key=lambda x: x[1])
                results = results[:top_k]

            out = []
            for vid, d in results:
                meta = self._get_meta(vid)
                out.append((vid, float(d), meta))
            return out

    # -------------------------
    # Persistence
    # -------------------------
    def save(self) -> None:
        """
        Persist Annoy index to disk (if available) and persist metadata (already in sqlite).
        If using in-memory fallback, writes nothing for index.
        """
        with self._lock:
            if self._annoy is not None:
                try:
                    # re-add mem_index items to annoy
                    for vid, vec in list(self._mem_index.items()):
                        try:
                            self._annoy.add_item(vid, vec)
                        except Exception:
                            pass
                    self._annoy.build(self.n_trees)
                    self._annoy.save(self.index_path)
                    self._annoy_built = True
                    logger.info("Saved Annoy index to %s", self.index_path)
                    # clear mem_index now that items are in annoy
                    self._mem_index.clear()
                except Exception:
                    logger.exception("Failed saving annoy index")
            else:
                # no Annoy backend — nothing to persist besides sqlite meta
                logger.info("Annoy backend not available; save() persists metadata only")

    def load(self) -> None:
        with self._lock:
            if _HAS_ANNOY and os.path.exists(self.index_path):
                if self._annoy is None:
                    self._annoy = AnnoyIndex(self.vector_dim, self.metric)
                try:
                    self._annoy.load(self.index_path)
                    self._annoy_built = True
                    logger.info("Loaded Annoy index from %s", self.index_path)
                except Exception:
                    logger.exception("Failed to load annoy index")
            # metadata DB is always present

    def close(self) -> None:
        try:
            self._meta_conn.commit()
            self._meta_conn.close()
        except Exception:
            pass

    # -------------------------
    # Utilities
    # -------------------------
    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "annoy_available": _HAS_ANNOY,
                "annoy_loaded": bool(self._annoy_built),
                "meta_db_path": self.meta_db_path,
                "index_path": self.index_path,
            }
