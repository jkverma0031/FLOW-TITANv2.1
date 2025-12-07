# Path: titan/augmentation/provenance.py
from __future__ import annotations
from typing import Optional, Dict, List, Any
import uuid
import time
import logging

logger = logging.getLogger(__name__)


class ProvenanceNode:
    """
    A node in the provenance DAG.

    Each node represents:
    - output data
    - operation type
    - timestamp
    - metadata
    - parent sources
    """

    def __init__(
        self,
        data: Any,
        op: str,
        parents: Optional[List["ProvenanceNode"]] = None,
        metadata: Optional[Dict] = None
    ):
        self.id = str(uuid.uuid4())
        self.timestamp = time.time()
        self.data = data
        self.op = op
        self.parents = parents or []
        self.metadata = metadata or {}

    def to_dict(self):
        return {
            "id": self.id,
            "op": self.op,
            "time": self.timestamp,
            "metadata": self.metadata,
            "parents": [p.id for p in self.parents],
            "data_preview": str(self.data)[:200]
        }

    def __repr__(self):
        return f"<ProvNode id={self.id} op={self.op} parents={len(self.parents)}>"


class ProvenanceTracker:
    """
    Enterprise-grade provenance tracker.
    -------------------------------------
    - Each operation generates a new node
    - Nodes can be linked to form a DAG
    - The DAG can be exported for audits / debugging
    """

    def __init__(self):
        self._nodes: Dict[str, ProvenanceNode] = {}
        self._latest: Optional[ProvenanceNode] = None

    # ------------------------------------------------------------------
    def record(self, data: Any, op: str, parents: Optional[List[ProvenanceNode]] = None, metadata=None):
        """
        Create a new provenance node.
        """
        node = ProvenanceNode(
            data=data,
            op=op,
            parents=parents,
            metadata=metadata
        )
        self._nodes[node.id] = node
        self._latest = node
        logger.debug(f"Provenance recorded node {node.id} (op={op})")
        return node

    # ------------------------------------------------------------------
    def latest(self) -> Optional[ProvenanceNode]:
        return self._latest

    # ------------------------------------------------------------------
    def export(self) -> Dict[str, Any]:
        """
        Export full provenance DAG
        """
        return {
            "nodes": {nid: node.to_dict() for nid, node in self._nodes.items()},
            "latest": self._latest.id if self._latest else None
        }
