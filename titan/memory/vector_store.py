# titan/memory/vector_store.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple

class VectorStore(ABC):
    """
    Abstract Base Class for all Vector Store implementations (Annoy, FAISS, etc.).
    Enforces a stable API contract for the Planner and Executor subsystems.
    This pattern ensures the memory system can be replaced easily without
    modifying the Core Kernel logic.
    """

    @abstractmethod
    def init(self, vector_dim: int, **kwargs):
        """Initializes the store, ensuring all necessary indices/tables exist."""
        pass

    @abstractmethod
    def add(self, text: str, embedding: List[float], metadata: Optional[Dict[str, Any]] = None) -> str:
        """Adds a single record to the store and returns a unique ID."""
        pass

    @abstractmethod
    def add_many(self, records: List[Tuple[str, List[float], Optional[Dict[str, Any]]]]) -> List[str]:
        """Adds multiple records efficiently."""
        pass

    @abstractmethod
    def query(self, query_vector: List[float], k: int = 5, filter_metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Performs a nearest neighbor search.

        Returns a list of dictionaries, where each dict contains:
        {'id': str, 'text': str, 'metadata': dict, 'score': float}
        """
        pass

    @abstractmethod
    def query_by_text(self, text: str, k: int = 5, filter_metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Queries the store by text, handles embedding internally."""
        pass

    @abstractmethod
    def persist(self):
        """Forces the vector index and metadata to be written to disk."""
        pass

    @abstractmethod
    def rebuild_index(self):
        """Rebuilds the underlying vector index for optimal performance."""
        pass

    @abstractmethod
    def close(self):
        """Closes all connections and flushes data."""
        pass

# The PersistentAnnoyStore must now inherit from VectorStore and implement all @abstractmethods.