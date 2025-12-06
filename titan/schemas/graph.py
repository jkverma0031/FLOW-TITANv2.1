# Path: FLOW/titan/schemas/graph.py
from __future__ import annotations
from typing import Dict, List, Optional, Any, Set, Type
from enum import Enum
from pydantic import BaseModel, Field, root_validator, validator
from uuid import uuid4
import hashlib
import json


class NodeType(str, Enum):
    START = "start"
    END = "end"
    TASK = "task"
    DECISION = "decision"
    LOOP = "loop"
    RETRY = "retry"
    NOOP = "noop"


class Edge(BaseModel):
    source: str
    target: str
    label: Optional[str] = None  # e.g. 'true'/'false', 'next', 'break', 'continue'

    @validator("source", "target")
    def id_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Edge source/target must be non-empty node id")
        return v


class NodeBase(BaseModel):
    id: str
    type: NodeType
    name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("id")
    def id_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("node.id must be a non-empty string")
        return v

    class Config:
        extra = "forbid"

    def dict_safe(self) -> Dict[str, Any]:
        """
        Use pydantic's dict() for safe serialization.
        This method exists so callers don't rely on ad-hoc attributes.
        """
        return self.dict(exclude_none=True)


class TaskNode(NodeBase):
    type: NodeType = Field(default=NodeType.TASK, const=True)
    task_ref: str  # reference to Task schema id (t1, t2, or a task uid)
    timeout_seconds: Optional[int] = None
    supports_parallel: bool = False

    @validator("task_ref")
    def task_ref_nonempty(cls, v):
        if not v or not v.strip():
            raise ValueError("task_ref must be a non-empty string pointing to a Task.id")
        return v


class DecisionNode(NodeBase):
    type: NodeType = Field(default=NodeType.DECISION, const=True)
    condition: str  # safe expression in DSL supported by condition_evaluator

    @validator("condition")
    def condition_nonempty(cls, v):
        if not v or not v.strip():
            raise ValueError("DecisionNode.condition must be a non-empty expression")
        return v


class LoopNode(NodeBase):
    type: NodeType = Field(default=NodeType.LOOP, const=True)
    iterator_var: str
    iterable_expr: str  # expression resolved at runtime
    max_iterations: Optional[int] = 1000

    @validator("iterator_var")
    def var_name_check(cls, v):
        if not v or not v.strip():
            raise ValueError("iterator_var must be a non-empty variable name")
        return v


class RetryNode(NodeBase):
    type: NodeType = Field(default=NodeType.RETRY, const=True)
    attempts: int = 3
    backoff_seconds: float = 1.0
    child_node_id: Optional[str] = None

    @validator("attempts")
    def attempts_positive(cls, v):
        if v < 1 or v > 100:
            raise ValueError("RetryNode.attempts must be between 1 and 100")
        return v


class NoOpNode(NodeBase):
    type: NodeType = Field(default=NodeType.NOOP, const=True)


class StartNode(NodeBase):
    type: NodeType = Field(default=NodeType.START, const=True)


class EndNode(NodeBase):
    type: NodeType = Field(default=NodeType.END, const=True)


_NODE_TYPE_TO_CLASS: Dict[NodeType, Type[NodeBase]] = {
    NodeType.TASK: TaskNode,
    NodeType.DECISION: DecisionNode,
    NodeType.LOOP: LoopNode,
    NodeType.RETRY: RetryNode,
    NodeType.NOOP: NoOpNode,
    NodeType.START: StartNode,
    NodeType.END: EndNode,
}


class CFG(BaseModel):
    """
    Control Flow Graph representation.
    - nodes: mapping node_id -> NodeBase subclass
    - edges: list of Edge
    - entry: start node id
    - exit: end node id
    """
    nodes: Dict[str, NodeBase] = Field(default_factory=dict)
    edges: List[Edge] = Field(default_factory=list)
    entry: Optional[str] = None
    exit: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    @root_validator(pre=True)
    def coerce_node_types(cls, values):
        # Ensure nodes are cast to correct subclasses when provided as dicts
        raw_nodes = values.get("nodes", {})
        coerced: Dict[str, NodeBase] = {}
        for nid, node_obj in raw_nodes.items():
            if isinstance(node_obj, NodeBase):
                coerced[nid] = node_obj
                continue
            if isinstance(node_obj, dict):
                node_type = node_obj.get("type")
                if not node_type:
                    raise ValueError(f"Node {nid} missing 'type' field")
                if isinstance(node_type, NodeType):
                    nt = node_type
                else:
                    nt = NodeType(node_type)
                node_cls = _NODE_TYPE_TO_CLASS.get(nt)
                if node_cls is None:
                    raise ValueError(f"Unknown node type '{nt}' for node id {nid}")
                coerced[nid] = node_cls(**node_obj)
            else:
                raise ValueError(f"Unsupported node representation for {nid}: {type(node_obj)}")
        values["nodes"] = coerced
        return values

    def add_node(self, node: NodeBase):
        if node.id in self.nodes:
            raise ValueError(f"Node id already exists: {node.id}")
        self.nodes[node.id] = node

    def add_edge(self, source: str, target: str, label: Optional[str] = None):
        if source not in self.nodes:
            raise ValueError(f"Cannot add edge: source node '{source}' does not exist")
        if target not in self.nodes:
            raise ValueError(f"Cannot add edge: target node '{target}' does not exist")
        self.edges.append(Edge(source=source, target=target, label=label))

    def get_successors(self, node_id: str) -> List[str]:
        return [e.target for e in self.edges if e.source == node_id]

    def get_predecessors(self, node_id: str) -> List[str]:
        return [e.source for e in self.edges if e.target == node_id]

    def validate_integrity(self, allow_orphan_nodes: bool = False) -> None:
        """Raise ValueError if graph integrity checks fail."""
        node_ids: Set[str] = set(self.nodes.keys())
        if self.entry is None:
            raise ValueError("CFG.entry must be set to the start node id")
        if self.exit is None:
            raise ValueError("CFG.exit must be set to the end node id")
        if self.entry not in node_ids:
            raise ValueError("CFG.entry points to a non-existent node")
        if self.exit not in node_ids:
            raise ValueError("CFG.exit points to a non-existent node")

        # check edges point to existing nodes
        for e in self.edges:
            if e.source not in node_ids:
                raise ValueError(f"Edge source '{e.source}' not present in nodes")
            if e.target not in node_ids:
                raise ValueError(f"Edge target '{e.target}' not present in nodes")

        if not allow_orphan_nodes:
            # nodes reachable from entry should cover all nodes (unless allow_orphan_nodes)
            reachable = self._reachable_from(self.entry)
            orphans = node_ids - reachable
            if orphans:
                raise ValueError(f"CFG contains orphan nodes unreachable from entry: {sorted(list(orphans))}")

    def _reachable_from(self, start: str) -> Set[str]:
        visited: Set[str] = set()
        stack = [start]
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            stack.extend(self.get_successors(nid))
        return visited

    def as_dict(self) -> Dict[str, Any]:
        """
        Deterministic, safe serialization of CFG into dict using pydantic node.dict().
        This avoids relying on custom per-node canonical() functions.
        """
        nodes_dict = {}
        # ensure deterministic ordering by sorting keys
        for nid in sorted(self.nodes.keys()):
            nodes_dict[nid] = self.nodes[nid].dict(exclude_none=True, by_alias=True)
        edges_list = [e.dict(exclude_none=True) for e in self.edges]
        return {
            "nodes": nodes_dict,
            "edges": edges_list,
            "entry": self.entry,
            "exit": self.exit,
            "metadata": self.metadata,
        }

    def canonical_hash(self) -> str:
        """
        Deterministic hash of the graph content for provenance/versioning.
        """
        canonical = self.as_dict()
        payload = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def make_simple_linear(cls, nodes: List[NodeBase]) -> "CFG":
        """
        Utility: create a simple linear CFG with start -> tasks... -> end.
        Adds nodes and wires them linearly.
        """
        if not nodes:
            raise ValueError("nodes must be non-empty")
        cfg = cls()
        for n in nodes:
            cfg.add_node(n)
        ids = list(cfg.nodes.keys())
        cfg.entry = ids[0]
        cfg.exit = ids[-1]
        for i in range(len(ids) - 1):
            cfg.add_edge(ids[i], ids[i + 1], label="next")
        return cfg
