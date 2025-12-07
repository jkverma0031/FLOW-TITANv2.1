# Path: titan/executor/scheduler.py
from __future__ import annotations
from typing import List, Dict, Set, Optional, Any
import logging
import asyncio
from datetime import datetime

from titan.schemas.graph import CFG, NodeType, NodeBase
from titan.schemas.events import Event, EventType
# FIXED: Import NodeState
from titan.executor.state_tracker import StateTracker, NodeState
from titan.executor.condition_evaluator import ConditionEvaluator
from titan.executor.loop_engine import LoopEngine
from titan.executor.retry_engine import RetryEngine
from titan.executor.replanner import Replanner
from titan.executor.worker_pool import WorkerPool

logger = logging.getLogger(__name__)

class Scheduler:
    """
    Determines which nodes are ready to execute based on the CFG and current State.
    """
    def __init__(
        self,
        cfg: CFG,
        worker_pool: WorkerPool,
        state_tracker: StateTracker,
        condition_evaluator: ConditionEvaluator,
        loop_engine: LoopEngine,
        retry_engine: RetryEngine,
        replanner: Optional[Replanner],
        event_emitter: Optional[Any] = None
    ):
        self.cfg = cfg
        self.pool = worker_pool
        self.state = state_tracker
        self.cond_eval = condition_evaluator
        self.loop_engine = loop_engine
        self.retry_engine = retry_engine
        self.replanner = replanner
        self.emit = event_emitter if event_emitter else lambda e: None

    def run(self, session_id: str, plan_id: str) -> Dict[str, Any]:
        """
        Main execution loop.
        """
        logger.info("Scheduler: starting execution")
        
        try:
            self.cfg.validate_integrity()
        except Exception as e:
            return {"status": "failed", "error": f"Graph integrity check failed: {e}"}

        # Initialize State
        for nid in self.cfg.nodes:
            self.state.set_state(nid, NodeState.PENDING)

        queue = [self.cfg.entry]
        visited = set()

        while queue:
            current_id = queue.pop(0)
            
            if current_id in visited:
                continue
            
            node = self.cfg.nodes[current_id]
            
            # Emit Node Started
            self.emit(Event(
                type=EventType.NODE_STARTED,
                plan_id=plan_id,
                payload={
                    "node_id": node.id,
                    "node_type": node.type,
                    "timestamp": datetime.utcnow().isoformat()
                }
            ))

            # EXECUTE NODE
            try:
                # Mark as Running
                self.state.set_state(node.id, NodeState.RUNNING)
                
                result = self._execute_node(node)
                self.state.set_result(node.id, result)
                
                # Emit Node Finished
                self.emit(Event(
                    type=EventType.NODE_FINISHED,
                    plan_id=plan_id,
                    payload={
                        "node_id": node.id,
                        "result": result,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                ))
            except Exception as e:
                self.state.set_state(node.id, NodeState.FAILED, str(e))
                logger.exception(f"Node {node.id} failed")
                return {"status": "failed", "error": str(e), "failed_node": node.id}

            visited.add(current_id)

            next_ids = self._get_next_nodes(node, result)
            for nid in next_ids:
                if nid not in visited and nid not in queue:
                    queue.append(nid)

            if node.type == NodeType.END:
                break

        return {"status": "success", "nodes_executed": len(visited)}

    def _execute_node(self, node: NodeBase) -> Any:
        if node.type == NodeType.TASK:
            payload = {
                "type": "exec",
                "command": f"mock_execute {node.name}",
                "args": node.metadata,
                "timeout_seconds": getattr(node, "timeout_seconds", 60)
            }
            future = self.pool.submit(payload)
            return future.result()

        elif node.type == NodeType.DECISION:
            return self.cond_eval.evaluate(node.condition)

        elif node.type == NodeType.LOOP:
            return self.loop_engine.should_continue(node)

        elif node.type == NodeType.RETRY:
            return True

        elif node.type == NodeType.START or node.type == NodeType.END or node.type == NodeType.NOOP:
            return {"status": "ok"}
            
        return None

    def _get_next_nodes(self, node: NodeBase, result: Any) -> List[str]:
        edges = self.cfg.get_edges_from(node.id)
        
        if node.type == NodeType.DECISION:
            label = "true" if result else "false"
            return [e.target for e in edges if e.label == label]
            
        elif node.type == NodeType.LOOP:
            target_labels = ["body", "continue"] if result else ["break"]
            return [e.target for e in edges if e.label in target_labels]
            
        else:
            return [e.target for e in edges]