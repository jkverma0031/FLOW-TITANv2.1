# titan/schemas/graph.py
from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional, Any, Set, Type
from pydantic import BaseModel, Field, model_validator
from uuid import uuid4
import hashlib
import json


# ------------------------------
# ID Factory
# ------------------------------

def new_node_id(prefix: str = "n") -> str:
    return f"{prefix}{uuid4().hex[:8]}"


# ------------------------------
# Node Types
# ------------------------------

class CFGNodeType(str, Enum):
    START = "start"
    END = "end"
    TASK = "task"
    DECISION = "decision"
    LOOP = "loop"
    RETRY = "retry"
    NOOP = "noop"
    CALL = "call"   # For sub-plan / multi-agent calls


# ------------------------------
# Base Node
# ------------------------------

class CFGNode(BaseModel):
    """
    Base class for all CFG nodes.
    Successor format:  { "label": "target_node_id" }
    """
    id: str = Field(default_factory=new_node_id)
    type: CFGNodeType = CFGNodeType.NOOP
    name: Optional[str] = None
    description: str = ""
    successors: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate(self):
        # Nothing strict here, but hook exists for extension
        return self


# ------------------------------
# Subclasses
# ------------------------------

class StartNode(CFGNode):
    type: CFGNodeType = CFGNodeType.START

class EndNode(CFGNode):
    type: CFGNodeType = CFGNodeType.END

class TaskNode(CFGNode):
    type: CFGNodeType = CFGNodeType.TASK
    task_ref: str
    timeout_seconds: Optional[float] = None
    supports_parallel: bool = False

class DecisionNode(CFGNode):
    type: CFGNodeType = CFGNodeType.DECISION
    condition: str

class LoopNode(CFGNode):
    type: CFGNodeType = CFGNodeType.LOOP
    iterator_var: str
    iterable_expr: str
    max_iterations: int = 1000
    continue_on_error: bool = False

class RetryNode(CFGNode):
    type: CFGNodeType = CFGNodeType.RETRY
    attempts: int = 3
    backoff_seconds: float = 1.0
    child_node_id: Optional[str] = None

class NoOpNode(CFGNode):
    type: CFGNodeType = CFGNodeType.NOOP

class CallNode(CFGNode):
    type: CFGNodeType = CFGNodeType.CALL
    target_service: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result_var: str


# NODE TYPE MAP (for dynamic instantiation)
NODE_CLASSES: Dict[CFGNodeType, Type[CFGNode]] = {
    CFGNodeType.START: StartNode,
    CFGNodeType.END: EndNode,
    CFGNodeType.TASK: TaskNode,
    CFGNodeType.DECISION: DecisionNode,
    CFGNodeType.LOOP: LoopNode,
    CFGNodeType.RETRY: RetryNode,
    CFGNodeType.NOOP: NoOpNode,
    CFGNodeType.CALL: CallNode,
}


# ------------------------------
# CFG Structure
# ------------------------------

class CFG(BaseModel):
    nodes: Dict[str, CFGNode] = Field(default_factory=dict)
    entry: Optional[str] = None
    exit: Optional[str] = None

    model_config = {"extra": "forbid"}

    # ------------------------------------
    # Construction from flat list
    # ------------------------------------
    @classmethod
    def from_node_list(cls, data: List[Dict[str, Any]]) -> "CFG":
        cfg = cls()

        for raw in data:
            t = raw.get("type", "noop")
            try:
                node_type = CFGNodeType(t)
            except Exception:
                raise ValueError(f"Invalid node type: {t}")

            NodeClass = NODE_CLASSES.get(node_type, CFGNode)

            try:
                node = NodeClass(**raw)
            except Exception as e:
                raise ValueError(f"Failed to build node '{raw.get('id')}': {e}") from e

            cfg.nodes[node.id] = node

            if node_type == CFGNodeType.START:
                cfg.entry = node.id
            elif node_type == CFGNodeType.END:
                cfg.exit = node.id

        if cfg.nodes and not cfg.entry:
            raise ValueError("CFG missing START node.")
        return cfg


    # ------------------------------------
    # Node Manipulation
    # ------------------------------------
    def add_node(self, node: CFGNode):
        self.nodes[node.id] = node
        if node.type == CFGNodeType.START:
            self.entry = node.id
        if node.type == CFGNodeType.END:
            self.exit = node.id


    def add_successor(self, src: str, dst: str, label: str = "next"):
        if src not in self.nodes:
            raise ValueError(f"Source node '{src}' does not exist.")
        if dst not in self.nodes:
            raise ValueError(f"Target node '{dst}' does not exist.")
        self.nodes[src].successors[label] = dst


    def get_successors(self, nid: str) -> Dict[str, str]:
        node = self.nodes.get(nid)
        return node.successors if node else {}


    def get_all_successors(self, nid: str) -> List[str]:
        node = self.nodes.get(nid)
        return list(node.successors.values()) if node else []


    # ------------------------------------
    # Integrity Validation
    # ------------------------------------
    def validate_integrity(self, allow_orphan_nodes: bool = True) -> bool:
        if not self.entry or not self.exit:
            raise ValueError("CFG must define entry and exit nodes.")

        if self.entry not in self.nodes:
            raise ValueError("Entry node not found in CFG.")
        if self.exit not in self.nodes:
            raise ValueError("Exit node not found in CFG.")

        # Check successor integrity
        for node in self.nodes.values():
            for _, tid in node.successors.items():
                if tid not in self.nodes:
                    raise ValueError(f"Node {node.id} targets missing node {tid}")

        # Reachability check
        visited: Set[str] = set()
        queue = [self.entry]

        while queue:
            nid = queue.pop(0)
            if nid in visited:
                continue
            visited.add(nid)
            queue.extend(self.get_all_successors(nid))

        if self.exit not in visited:
            raise ValueError("Exit node is unreachable from entry node.")

        # Detect cycles in non-loop nodes (executor expects loops only where intended)
        # — We leave actual detection optional for now.

        return True


    # ------------------------------------
    # Hash
    # ------------------------------------
    def canonical_hash(self) -> str:
        """
        Deterministic structural hash of the CFG.
        """
        sorted_nodes = sorted(self.nodes.items(), key=lambda x: x[0])

        canonical = []
        for nid, node in sorted_nodes:
            succ = sorted(node.successors.items())
            canonical.append({
                "id": nid,
                "type": node.type.value,
                "name": node.name,
                "succ": succ,
            })

        data = {
            "entry": self.entry,
            "exit": self.exit,
            "nodes": canonical,
        }

        raw = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
