# Path: titan/schemas/graph.py
from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional, Any, Set
from pydantic import BaseModel, Field, model_validator
from uuid import uuid4
import hashlib
import json

def new_node_id(prefix: str = "n") -> str:
    """Generates a deterministic-looking but unique node ID."""
    return f"{prefix}{uuid4().hex[:8]}"

class CFGNodeType(str, Enum):
    """
    Defines the standard set of nodes for the Control Flow Graph (CFG).
    Using Enum ensures strict type checking.
    """
    START = "start"
    END = "end"
    TASK = "task"
    DECISION = "decision"
    LOOP = "loop"
    RETRY = "retry"
    NOOP = "noop"
    # FUTURE-PROOFING: Added Call node for Multi-Agent / Sub-Plan Execution
    CALL = "call" 

# --- Base Node Definition ---

class CFGNode(BaseModel):
    """
    Base class for all CFG nodes. Successors are defined on the node itself.
    This replaces the need for a separate Edge class in the main CFG model.
    """
    id: str = Field(default_factory=new_node_id)
    # The type is defined by the subclass but defaults to NOOP for safety
    type: CFGNodeType = CFGNodeType.NOOP 
    name: Optional[str] = None
    description: str = Field(default="", description="Human-readable description of the node's function.")
    
    # Successors define the control flow. Keys are labels (e.g., 'next', 'true', 'false', 'exit')
    # and values are the target node IDs. This is the core structural change for efficiency.
    successors: Dict[str, str] = Field(default_factory=dict) 
    
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @model_validator(mode='after')
    def validate_node_type_matches(self) -> 'CFGNode':
        if self.type != self.__class__.__fields__['type'].default:
             # Allow for explicit override, but warn/check if complex graph logic is involved
             pass
        return self

# --- Specific Node Implementations (all original types plus the new CallNode) ---

class StartNode(CFGNode):
    type: CFGNodeType = CFGNodeType.START
    # Start node typically only has one successor, labeled 'next'
    
class EndNode(CFGNode):
    type: CFGNodeType = CFGNodeType.END
    # End node has no successors

class TaskNode(CFGNode):
    type: CFGNodeType = CFGNodeType.TASK
    task_ref: str  # Reference to the specific Task object definition (ID)
    timeout_seconds: Optional[float] = None
    supports_parallel: bool = False
    
class DecisionNode(CFGNode):
    type: CFGNodeType = CFGNodeType.DECISION
    condition: str  # The expression to evaluate
    # Decision nodes must have 'true' and 'false' successors, or default logic handles one successor
    
class LoopNode(CFGNode):
    type: CFGNodeType = CFGNodeType.LOOP
    iterator_var: str = Field(description="The variable name for the current item in the loop.")
    iterable_expr: str = Field(description="The expression yielding the collection to iterate over.")
    max_iterations: int = 1000
    continue_on_error: bool = False
    # Loop nodes typically have successors for 'body' (loop entry) and 'exit'

class RetryNode(CFGNode):
    type: CFGNodeType = CFGNodeType.RETRY
    attempts: int = 3
    backoff_seconds: float = 1.0
    child_node_id: Optional[str] = Field(None, description="The ID of the single node contained within the retry block.")
    # Retry nodes typically have 'success' and 'failure' successors

class NoOpNode(CFGNode):
    type: CFGNodeType = CFGNodeType.NOOP
    # NoOp nodes typically have one successor, labeled 'next', used for structural joining.

class CallNode(CFGNode):
    """
    Future-Proofing: Node for Multi-Agent / Sub-Plan calls.
    Allows TITAN to delegate execution to another agent or sub-routine.
    """
    type: CFGNodeType = CFGNodeType.CALL
    target_service: str = Field(description="The name of the external agent service, plan ID, or sub-routine to call.")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result_var: str = Field(description="The context variable name to store the CallNode's output.")
    
# --- The Main CFG Structure ---

class CFG(BaseModel):
    """
    Control Flow Graph. The deterministic executable structure of a plan.
    It holds all nodes and manages graph integrity.
    """
    # Nodes are mapped by ID to their specific node type (TaskNode, DecisionNode, etc.)
    nodes: Dict[str, CFGNode] = Field(default_factory=dict) 
    entry: Optional[str] = Field(None, description="The ID of the entry node (must be of type START).")
    exit: Optional[str] = Field(None, description="The ID of the exit node (must be of type END).")
    
    # NOTE: The 'edges' list from the original code has been removed for efficiency,
    # as the successor data is now contained within each node.

    @classmethod
    def from_node_list(cls, nodes_data: List[Dict[str, Any]]) -> 'CFG':
        """Constructs a CFG instance from a flat list of node dictionaries."""
        cfg = cls()
        
        # Mapping for dynamic node instantiation
        NODE_MAP = {
            CFGNodeType.START: StartNode,
            CFGNodeType.END: EndNode,
            CFGNodeType.TASK: TaskNode,
            CFGNodeType.DECISION: DecisionNode,
            CFGNodeType.LOOP: LoopNode,
            CFGNodeType.RETRY: RetryNode,
            CFGNodeType.NOOP: NoOpNode,
            CFGNodeType.CALL: CallNode,
        }

        for data in nodes_data:
            node_type_str = data.get("type", CFGNodeType.NOOP.value)
            try:
                node_type = CFGNodeType(node_type_str)
                NodeClass = NODE_MAP.get(node_type, CFGNode)
                node = NodeClass(**data)
                cfg.nodes[node.id] = node
                
                if node_type == CFGNodeType.START:
                    cfg.entry = node.id
                elif node_type == CFGNodeType.END:
                    cfg.exit = node.id
            except Exception as e:
                raise ValueError(f"Failed to instantiate node type '{node_type_str}' for ID '{data.get('id', 'unknown')}': {e}") from e
                
        # Basic check to ensure the core nodes exist if data was provided
        if not cfg.entry and cfg.nodes:
            raise ValueError("CFG constructed without a START node.")
        
        return cfg


    def add_node(self, node: CFGNode):
        """Adds a node instance to the graph."""
        self.nodes[node.id] = node
        if node.type == CFGNodeType.START:
            self.entry = node.id
        elif node.type == CFGNodeType.END:
            self.exit = node.id

    def add_successor(self, source_id: str, target_id: str, label: str = "next"):
        """Creates a directional flow transition between two nodes."""
        if source_id not in self.nodes:
            raise ValueError(f"Source node {source_id} not in CFG.")
        if target_id not in self.nodes:
            raise ValueError(f"Target node {target_id} not in CFG.")
            
        self.nodes[source_id].successors[label] = target_id


    def get_successors(self, node_id: str) -> Dict[str, str]:
        """Returns the successor map for a given node ID."""
        node = self.nodes.get(node_id)
        if not node:
            return {}
        return node.successors

    def get_all_successors(self, node_id: str) -> List[str]:
        """Returns a list of all target node IDs for a given node."""
        node = self.nodes.get(node_id)
        if not node:
            return []
        return list(node.successors.values())


    def validate_integrity(self, allow_orphan_nodes: bool = True) -> bool:
        """
        Ensures the graph is structurally sound for execution.
        """
        # 1. Check Entry/Exit
        if not self.entry or not self.exit:
            raise ValueError("CFG validation failed: Both entry and exit nodes must be defined.")
        if self.entry not in self.nodes:
            raise ValueError(f"CFG validation failed: Entry node {self.entry} missing.")
        if self.exit not in self.nodes:
            raise ValueError(f"CFG validation failed: Exit node {self.exit} missing.")

        # 2. Check Successor integrity
        for node in self.nodes.values():
            for label, target_id in node.successors.items():
                if target_id not in self.nodes:
                    raise ValueError(f"Node {node.id} has a successor target {target_id} that does not exist.")
                
        # 3. Reachability check (BFS from entry)
        visited: Set[str] = set()
        queue: List[str] = [self.entry]
        
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            
            # Use get_all_successors which reads the successors dictionary
            queue.extend(self.get_all_successors(curr))
        
        # Exit must be reachable
        if self.exit not in visited:
             raise ValueError("CFG validation failed: Exit node is not reachable from Entry.")

        return True

    def canonical_hash(self) -> str:
        """
        Produces a deterministic hash of the graph structure.
        Uses node successors instead of a separate edge list for hashing.
        """
        # Sort nodes by ID for determinism
        sorted_nodes = sorted(self.nodes.items())
        
        # Generate a canonical list of successor connections (label, target) for each node
        canonical_connections = []
        for nid, node in sorted_nodes:
            # Sort successors by label for determinism
            sorted_successors = sorted(node.successors.items())
            canonical_connections.append({
                "id": nid,
                "type": node.type.value, # Use .value for the string enum value
                "name": node.name,
                "successors": sorted_successors,
            })
            
        data = {
            "nodes": canonical_connections,
            "entry": self.entry,
            "exit": self.exit
        }
        raw = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()