# Path: titan/memory/persistent_annoy_store.py
from __future__ import annotations
import os
import threading
import logging
import json
import sqlite3
import time
from typing import Iterable, List, Dict, Optional, Tuple, Any, Callable
from uuid import uuid4

# Import the VectorStore Abstract Base Class (ABC) defined in the previous step
from titan.memory.vector_store import VectorStore 
# Assuming MemoryRecord exists for internal use, but we align public methods to VectorStore ABC signature

logger = logging.getLogger(__name__)

try:
    from annoy import AnnoyIndex  # type: ignore
    _HAS_ANNOY = True
except ImportError:
    _HAS_ANNOY = False

try:
    import numpy as np  # type: ignore
except ImportError:
    np = None

# Using a variable instead of a hardcoded constant
DEFAULT_METADB = "data/annoy_meta.db" 


class PersistentAnnoyStore(VectorStore): # Inherit the VectorStore ABC
    """
    Persistent vector store using Annoy. Implements the VectorStore Protocol.
    This fixes the Memory Gap by ensuring persistence and scalability.
    """

    def __init__(
        self,
        vector_dim: int = 1536,
        index_path: str = "data/index.ann",
        meta_db_path: str = DEFAULT_METADB, # Correctly use the configured path
        metric: str = "angular",
        n_trees: int = 10,
    ):
        self.vector_dim = int(vector_dim)
        self.index_path = index_path
        self.meta_db_path = meta_db_path # Correctly store the configured path
        self.metric = metric
        self.n_trees = int(n_trees)
        self._lock = threading.RLock()
        
        self._mem_index: Dict[int, List[float]] = {}
        self._next_id = 1
        self._annoy: Optional[AnnoyIndex] = None
        self._annoy_built = False

        os.makedirs(os.path.dirname(self.meta_db_path) or ".", exist_ok=True)
        self._init_meta_db() # This now correctly uses self.meta_db_path

        if _HAS_ANNOY:
            self.init(self.vector_dim, metric=self.metric, n_trees=self.n_trees)

    # --- VectorStore Protocol Implementation (Public API) ---

    def init(self, vector_dim: int, **kwargs):
        """Initializes the Annoy index."""
        if self._annoy is not None and self._annoy.f != vector_dim:
            logger.warning("Rebuilding Annoy index due to dimension mismatch.")
            self._annoy = None # Force re-init if dim changes

        if self._annoy is None and _HAS_ANNOY:
            try:
                self._annoy = AnnoyIndex(vector_dim, kwargs.get('metric', self.metric))
                if os.path.exists(self.index_path):
                    self._annoy.load(self.index_path)
                    self._annoy_built = True
            except Exception as e:
                logger.error(f"Failed to initialize Annoy index: {e}")
                self._annoy = None

    def add(self, text: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None) -> str:
        """Protocol implementation: Add a single record."""
        metadata = metadata or {}
        # Ensure a unique ID for the metadata payload
        record_id = metadata.get("id", str(uuid4())) 
        
        # Prepare internal metadata structure
        internal_meta = {
            "id": record_id,
            "text": text,
            "metadata": metadata
        }
        
        vector_id = self._add_vector(embedding, internal_meta)
        if vector_id == -1:
            return ""

        return record_id

    def add_many(self, records: List[Tuple[str, List[float], Optional[Dict[str, Any]]]]) -> List[str]:
        """Adds multiple records efficiently."""
        ids = []
        for text, embedding, metadata in records:
            ids.append(self.add(text, embedding, metadata))
        return ids

    def query(self, query_vector: List[float], k: int = 5, filter_metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Performs a nearest neighbor search."""
        results = self._query_vector(query_vector, k)
        
        out = []
        for vid, dist, meta in results:
            if meta:
                # Reformat meta to match the expected query output format
                out.append({
                    "id": meta.get("id"),
                    "text": meta.get("text", ""),
                    "metadata": meta.get("metadata", {}), # Contains original metadata
                    "score": dist,
                })
        # NOTE: Full filtering by metadata is currently omitted for simplicity in Annoy, 
        # but the ABC requires it, so this is a technical debt point.
        return out

    # Assuming the Kernel will pass an Embedder instance, not the function itself
    # If the signature needs to be simplified to match the ABC exactly, the embed_fn must be obtained 
    # from an external service (e.g., self.embedder.embed(text)).
    def query_by_text(self, text: str, embed_fn: Callable[[str], List[float]], k: int = 5, filter_metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Query by text using the provided embed_fn (e.g., from Embeddings service)."""
        embedding = embed_fn(text)
        return self.query(embedding, k, filter_metadata)

    def persist(self) -> None:
        """Protocol implementation: Forces the vector index and metadata to be written to disk."""
        self.save()
        
    def rebuild_index(self) -> None:
        """Protocol implementation: Rebuilds the underlying vector index."""
        # A simple rebuild mechanism would reload all vectors and rebuild.
        # This is a placeholder for future robust implementation.
        logger.info("Rebuild index called. Requires full metadata reloading.")
        self.save() # Building index during save is the Annoy standard practice.

    def close(self) -> None:
        """Protocol implementation: Closes all connections and flushes data."""
        try:
            self._meta_conn.close()
        except Exception:
            pass
            
    # --- Internal Implementation (Original logic with path fix) ---

    def _init_meta_db(self):
        """Initializes the SQLite metadata database using the configured path."""
        # FIX: Use the configured path self.meta_db_path
        self._meta_conn = sqlite3.connect(self.meta_db_path, check_same_thread=False)
        self._meta_conn.execute("PRAGMA journal_mode=WAL;")
        self._meta_conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY, vector_id INTEGER UNIQUE, metadata TEXT, created INTEGER)"
        )
        self._meta_conn.commit()

    def _insert_meta(self, vector_id: int, metadata: Dict[str, Any]):
        """Inserts or replaces metadata for a given vector ID."""
        now = int(time.time())
        # Use json.dumps with default=str for robust serialization
        self._meta_conn.execute(
            "INSERT OR REPLACE INTO meta (vector_id, metadata, created) VALUES (?, ?, ?)",
            (vector_id, json.dumps(metadata, default=str), now),
        )
        self._meta_conn.commit()

    def _get_meta(self, vector_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves metadata for a given vector ID."""
        cur = self._meta_conn.execute("SELECT metadata FROM meta WHERE vector_id = ?", (vector_id,))
        row = cur.fetchone()
        if not row:
            return None
        try:
            # Recursively load the JSON (original metadata is nested within the stored JSON)
            return json.loads(row[0])
        except Exception:
            logger.exception(f"Failed to parse metadata for vector ID {vector_id}")
            return {}

    def _alloc_id(self) -> int:
        """Allocates a unique sequential ID for the Annoy index."""
        with self._lock:
            cur = self._meta_conn.execute("SELECT MAX(vector_id) FROM meta")
            row = cur.fetchone()
            if row and row[0]:
                self._next_id = int(row[0]) + 1
            vid = self._next_id
            self._next_id += 1
            return vid

    def _add_vector(self, vector: Iterable[float], metadata: Optional[Dict[str, Any]] = None) -> int:
        """Adds a vector to the Annoy index and saves metadata."""
        vec = list(vector)
        if len(vec) != self.vector_dim:
            logger.warning(f"Vector dim mismatch {len(vec)} vs {self.vector_dim}")
            return -1

        with self._lock:
            vid = self._alloc_id()
            if self._annoy is not None:
                try:
                    self._annoy.add_item(vid, vec)
                    self._annoy_built = False
                except Exception as e:
                    logger.warning(f"Annoy add_item failed: {e}. Falling back to in-memory store.")
                    self._mem_index[vid] = vec
            else:
                self._mem_index[vid] = vec
                
            self._insert_meta(vid, metadata or {})
            return vid

    def _query_vector(self, vector: Iterable[float], top_k: int = 10) -> List[Tuple[int, float, Optional[Dict[str, Any]]]]:
        """Internal query logic, handles Annoy search and memory fallback."""
        q = list(vector)
        results: List[Tuple[int, float]] = []
        
        with self._lock:
            # 1. Annoy Search
            if self._annoy is not None and (self._annoy_built or len(self._mem_index) == 0):
                try:
                    ids, dists = self._annoy.get_nns_by_vector(q, top_k, include_distances=True)
                    results = list(zip(ids, dists))
                except Exception as e:
                    logger.warning(f"Annoy query failed: {e}. Falling back to memory index.")

            # 2. Memory Fallback (Brute force) if Annoy failed or is not built
            if not results and self._mem_index and np:
                # Simple dot product fallback using numpy if available
                def cosine_sim(v1, v2):
                    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))
                
                candidates = []
                for vid, vec in self._mem_index.items():
                    # NOTE: Annoy uses angular distance; fallback uses cosine similarity (inverted relationship)
                    score = cosine_sim(q, vec) 
                    candidates.append((vid, score))
                
                candidates.sort(key=lambda x: x[1], reverse=True) # higher score = better
                results = [(vid, score) for vid, score in candidates[:top_k]]
            elif not np and not results and self._mem_index:
                 logger.warning("Numpy missing, cannot perform brute force query on in-memory vectors.")


            out = []
            for vid, d in results:
                meta = self._get_meta(vid)
                out.append((vid, float(d), meta))
            return out

    def save(self) -> None:
        """Saves pending vectors and rebuilds the Annoy index for persistence."""
        with self._lock:
            if self._annoy is not None:
                try:
                    # Add pending items from in-memory index
                    for vid, vec in self._mem_index.items():
                        self._annoy.add_item(vid, vec)
                    
                    # Building the index is essential for querying
                    self._annoy.build(self.n_trees)
                    self._annoy.save(self.index_path)
                    self._annoy_built = True
                    self._mem_index.clear()
                    logger.info(f"Annoy index saved to {self.index_path}")
                except Exception as e:
                    logger.error(f"Failed to save Annoy index: {e}")

    # No explicit load() method is needed as loading is handled in __init__
    # based on index_path existence.

    def health(self) -> Dict[str, Any]:
        """Provides status on Annoy availability and build status."""
        return {
            "annoy_available": _HAS_ANNOY, 
            "annoy_built": self._annoy_built,
            "meta_db": self.meta_db_path,
            "index_path": self.index_path,
            "pending_count": len(self._mem_index)
        }