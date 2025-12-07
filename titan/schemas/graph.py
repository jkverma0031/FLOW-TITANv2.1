# Path: titan/schemas/graph.py
from __future__ import annotations
from typing import Dict, List, Optional, Any, Set, Type, Literal
from enum import Enum
from pydantic import BaseModel, Field, root_validator, validator
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
    label: Optional[str] = None

    @validator("source", "target")
    def id_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Edge endpoints cannot be empty")
        return v


class NodeBase(BaseModel):
    id: str
    type: NodeType
    name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("id")
    def validate_id(cls, v):
        if not v.strip():
            raise ValueError("Node id cannot be empty")
        return v

    model_config = {"extra": "forbid"}


class TaskNode(NodeBase):
    type: Literal[NodeType.TASK] = NodeType.TASK
    task_ref: str
    timeout_seconds: Optional[int] = None
    supports_parallel: bool = False


class DecisionNode(NodeBase):
    type: Literal[NodeType.DECISION] = NodeType.DECISION
    condition: str


class LoopNode(NodeBase):
    type: Literal[NodeType.LOOP] = NodeType.LOOP
    iterator_var: str
    iterable_expr: str
    max_iterations: Optional[int] = 1000
    continue_on_error: bool = False


class RetryNode(NodeBase):
    type: Literal[NodeType.RETRY] = NodeType.RETRY
    attempts: int = 3
    backoff_seconds: float = 1.0
    child_node_id: Optional[str] = None


class NoOpNode(NodeBase):
    type: Literal[NodeType.NOOP] = NodeType.NOOP


class StartNode(NodeBase):
    type: Literal[NodeType.START] = NodeType.START


class EndNode(NodeBase):
    type: Literal[NodeType.END] = NodeType.END


_NODE_MAP: Dict[NodeType, Type[NodeBase]] = {
    NodeType.TASK: TaskNode,
    NodeType.DECISION: DecisionNode,
    NodeType.LOOP: LoopNode,
    NodeType.RETRY: RetryNode,
    NodeType.NOOP: NoOpNode,
    NodeType.START: StartNode,
    NodeType.END: EndNode,
}


class CFG(BaseModel):
    nodes: Dict[str, NodeBase] = Field(default_factory=dict)
    edges: List[Edge] = Field(default_factory=list)
    entry: Optional[str] = None
    exit: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @root_validator(pre=True)
    def coerce_nodes(cls, values):
        raw = values.get("nodes", {})
        out = {}
        for nid, obj in raw.items():
            if isinstance(obj, NodeBase):
                out[nid] = obj
                continue

            node_type = NodeType(obj["type"])
            cls_ = _NODE_MAP[node_type]
            out[nid] = cls_(**obj)

        values["nodes"] = out
        return values

    def add_node(self, node: NodeBase):
        if node.id in self.nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self.nodes[node.id] = node

    def add_edge(self, src: str, dst: str, label: Optional[str] = None):
        if src not in self.nodes or dst not in self.nodes:
            raise ValueError("Edge endpoints must exist")
        self.edges.append(Edge(source=src, target=dst, label=label))

    def as_dict(self):
        return {
            "nodes": {nid: n.model_dump() for nid, n in self.nodes.items()},
            "edges": [e.model_dump() for e in self.edges],
            "entry": self.entry,
            "exit": self.exit,
            "metadata": self.metadata,
        }
