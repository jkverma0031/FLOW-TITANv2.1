# Path: titan/schemas/graph.py
from __future__ import annotations
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field
import hashlib
import json

class NodeType:
    TASK = "task"
    DECISION = "decision"
    LOOP = "loop"
    RETRY = "retry"
    NOOP = "noop"
    START = "start"
    END = "end"

class NodeBase(BaseModel):
    id: str
    name: Optional[str] = None
    type: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class StartNode(NodeBase):
    type: str = NodeType.START

class EndNode(NodeBase):
    type: str = NodeType.END

class TaskNode(NodeBase):
    type: str = NodeType.TASK
    task_ref: str  # Reference to the Task object definition
    timeout_seconds: Optional[float] = None
    supports_parallel: bool = False

class DecisionNode(NodeBase):
    type: str = NodeType.DECISION
    condition: str  # The expression to evaluate

class LoopNode(NodeBase):
    type: str = NodeType.LOOP
    iterator_var: str
    iterable_expr: str
    max_iterations: int = 1000
    continue_on_error: bool = False

class RetryNode(NodeBase):
    type: str = NodeType.RETRY
    attempts: int = 3
    backoff_seconds: float = 1.0
    child_node_id: Optional[str] = None

class NoOpNode(NodeBase):
    type: str = NodeType.NOOP

class Edge(BaseModel):
    source: str
    target: str
    label: Optional[str] = "next"

class CFG(BaseModel):
    """
    Control Flow Graph.
    The deterministic executable structure of a plan.
    """
    nodes: Dict[str, NodeBase] = Field(default_factory=dict)
    edges: List[Edge] = Field(default_factory=list)
    entry: Optional[str] = None
    exit: Optional[str] = None

    def add_node(self, node: NodeBase):
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str, label: str = "next"):
        self.edges.append(Edge(source=source, target=target, label=label))

    def get_successors(self, node_id: str) -> List[str]:
        return [e.target for e in self.edges if e.source == node_id]
    
    def get_edges_from(self, node_id: str) -> List[Edge]:
        return [e for e in self.edges if e.source == node_id]

    def validate_integrity(self, allow_orphan_nodes: bool = True) -> bool:
        """
        Ensures the graph is structurally sound for execution.
        """
        # 1. Check Entry/Exit
        if not self.entry:
            raise ValueError("CFG validation failed: No entry node defined.")
        if not self.exit:
            raise ValueError("CFG validation failed: No exit node defined.")
        if self.entry not in self.nodes:
            raise ValueError(f"CFG validation failed: Entry node {self.entry} missing.")
        if self.exit not in self.nodes:
            raise ValueError(f"CFG validation failed: Exit node {self.exit} missing.")

        # 2. Check Edges point to existing nodes
        for edge in self.edges:
            if edge.source not in self.nodes:
                raise ValueError(f"Edge source {edge.source} does not exist.")
            if edge.target not in self.nodes:
                raise ValueError(f"Edge target {edge.target} does not exist.")

        # 3. Reachability check (BFS from entry)
        visited = set()
        queue = [self.entry]
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            queue.extend(self.get_successors(curr))
        
        # In a strict CFG, Exit must be reachable
        if self.exit not in visited:
             # It's possible the graph has infinite loops or disjoint sections, 
             # but for a valid plan, we generally want end reachability.
             # We warn or fail. For TITAN, we fail.
             raise ValueError("CFG validation failed: Exit node is not reachable from Entry.")

        return True

    def canonical_hash(self) -> str:
        """
        Produces a deterministic hash of the graph structure.
        Useful for provenance and caching.
        """
        # Sort nodes and edges to ensure determinism
        sorted_nodes = sorted(self.nodes.items())
        sorted_edges = sorted(
            [(e.source, e.target, e.label) for e in self.edges]
        )
        data = {
            "nodes": [(nid, n.type, n.name) for nid, n in sorted_nodes],
            "edges": sorted_edges,
            "entry": self.entry,
            "exit": self.exit
        }
        raw = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()