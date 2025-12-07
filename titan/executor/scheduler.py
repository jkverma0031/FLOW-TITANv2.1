# Path: titan/executor/scheduler.py
from __future__ import annotations
from typing import Dict, Any, Optional, Callable, List
import logging
import time
from datetime import datetime

from titan.schemas.plan import Plan
from titan.schemas.graph import CFG, CFGNode, CFGNodeType, DecisionNode, TaskNode, LoopNode, RetryNode
from titan.schemas.events import Event, EventType

from .state_tracker import StateTracker
from .worker_pool import WorkerPool
from .condition_evaluator import ConditionEvaluator
from .loop_engine import LoopEngine
from .retry_engine import RetryEngine
from .replanner import Replanner

logger = logging.getLogger(__name__)

class Scheduler:
    """
    The brain of the CFG-VM. Determines which nodes are ready to run,
    enforces control flow transitions, and orchestrates the execution engines.
    """

    def __init__(
        self,
        cfg: CFG,
        worker_pool: WorkerPool,
        state_tracker: StateTracker,
        condition_evaluator: ConditionEvaluator,
        loop_engine: LoopEngine,
        retry_engine: RetryEngine,
        replanner: Optional[Replanner] = None,
        event_emitter: Optional[Callable[[Event], None]] = None,
    ):
        self.cfg = cfg
        self.worker_pool = worker_pool
        self.state = state_tracker
        self.cond_eval = condition_evaluator
        self.loop_eng = loop_engine
        self.retry_eng = retry_engine
        self.replanner = replanner
        self.event_emitter = event_emitter
        
        self._nodes_to_process: List[str] = []
        self._finished = False
        
        # Initialize state for all nodes
        for node_id, node in self.cfg.nodes.items():
            self.state.initialize_node_state(node_id, name=node.name) 
            
        if self.cfg.entry:
            self._nodes_to_process.append(self.cfg.entry)


    def _emit(self, event_type: EventType, plan_id: str, payload: Dict[str, Any]):
        """Helper for emitting events."""
        if self.event_emitter:
            try:
                event = Event(
                    type=event_type,
                    plan_id=plan_id,
                    payload=payload,
                )
                self.event_emitter(event)
            except Exception as e:
                logger.error(f"Failed to emit event {event_type}: {e}")

    def _get_node(self, node_id: str) -> CFGNode:
        node = self.cfg.nodes.get(node_id)
        if not node:
            raise KeyError(f"Node ID {node_id} not found in CFG.")
        return node
        
    def _is_node_ready(self, node_id: str) -> bool:
        state = self.state.get_state(node_id)
        if state and state.get('status') in ['completed', 'failed', 'running']:
            return False
        return True

    def _process_node(self, node_id: str, session_id: str, plan_id: str):
        """Processes a single node based on its type."""
        
        node = self._get_node(node_id)
        self._emit(EventType.NODE_STARTED, plan_id, {"node_id": node.id, "node_type": node.type.value})
        
        # FIX: Explicitly update 'type' in StateTracker so tests can filter by 'task'
        self.state.update_node_state(
            node_id, 
            status='running', 
            type=node.type.value, 
            started_at=time.time()
        )
        
        result: Optional[Dict[str, Any]] = None
        
        try:
            # --- 1. ACTION NODE EXECUTION (TASK/CALL) ---
            if node.type in [CFGNodeType.TASK, CFGNodeType.CALL]:
                task_node: TaskNode = node 
                
                action_request = {
                    "id": task_node.id,
                    "name": task_node.task_ref,
                    "args": task_node.metadata.get('task_args', {}),
                    "context": {"session_id": session_id, "plan_id": plan_id, "task_name": task_node.task_ref}
                }
                
                result = self.worker_pool.runner(action_request)
                
                if result and result.get('status') == 'failure':
                    self.state.update_node_state(node_id, status='failed', result=result, error=result.get('error'))
                    self._emit(EventType.ERROR_OCCURRED, plan_id, {"node_id": node.id, "error": result.get('error'), "is_action_failure": True})
                    return

                self.state.update_node_state(node_id, status='completed', result=result)
                self._emit(EventType.NODE_FINISHED, plan_id, {"node_id": node.id, "node_type": node.type.value, "result_summary": result})
                self._transition_to_successors(node, 'next')
                
            # --- 2. CONTROL FLOW NODES ---
            elif node.type == CFGNodeType.DECISION:
                decision_node: DecisionNode = node 
                condition = decision_node.condition
                
                resolver_fn = lambda name: self.cond_eval.resolver(name, self.state)
                evaluator = ConditionEvaluator(resolver=resolver_fn) 
                
                eval_result = evaluator.evaluate(condition)
                
                successor_label = 'true' if eval_result else 'false'
                if successor_label not in node.successors:
                    successor_label = 'next'
                
                self._emit(EventType.DECISION_TAKEN, plan_id, {"node_id": node.id, "condition": condition, "result": eval_result, "branch": successor_label})
                self.state.update_node_state(node_id, status='completed', result={"branch_taken": successor_label})

                self._transition_to_successors(node, successor_label)
                self._emit(EventType.NODE_FINISHED, plan_id, {"node_id": node.id, "node_type": node.type.value})
                
            elif node.type in [CFGNodeType.START, CFGNodeType.NOOP]:
                self.state.update_node_state(node_id, status='completed')
                self._emit(EventType.NODE_FINISHED, plan_id, {"node_id": node.id, "node_type": node.type.value})
                self._transition_to_successors(node, 'next')

            elif node.type == CFGNodeType.END:
                self.state.update_node_state(node_id, status='completed')
                self._emit(EventType.NODE_FINISHED, plan_id, {"node_id": node.id, "node_type": node.type.value})
                self._finished = True 
                
        except Exception as e:
            logger.exception(f"CRITICAL ERROR processing node {node_id}")
            self.state.update_node_state(node_id, status='failed', error=str(e))
            self._emit(EventType.ERROR_OCCURRED, plan_id, {"node_id": node.id, "error": str(e), "critical": True}) 
            self._finished = True

    def _transition_to_successors(self, node: CFGNode, label: str):
        target_id = node.successors.get(label)
        if target_id:
            if target_id not in self._nodes_to_process:
                self._nodes_to_process.append(target_id)
            logger.debug(f"TRANSITION: Node {node.id} -> {target_id} via label '{label}'")
        elif not node.successors and node.type != CFGNodeType.END:
            logger.warning(f"Node {node.id} is a terminal node but not of type END.")

    def run(self, session_id: str, plan_id: str) -> Dict[str, Any]:
        self._finished = False
        nodes_executed = 0
        
        while self._nodes_to_process and not self._finished:
            current_node_id = self._nodes_to_process.pop(0)
            
            if self._is_node_ready(current_node_id):
                self._process_node(current_node_id, session_id, plan_id)
                nodes_executed += 1
            
            if nodes_executed > 1000:
                logger.error("Scheduler hit maximum execution limit. Potential infinite loop detected.")
                self._finished = True
                
        end_state = self.state.get_state(self.cfg.exit)
        if end_state and end_state.get('status') == 'completed':
            return {"status": "success", "nodes_executed": nodes_executed}
        else:
            critical_error = next((s.get('error') for s in self.state.get_all_states().values() if s.get('status') == 'failed'), "Execution halted unexpectedly.")
            return {"status": "failed", "nodes_executed": nodes_executed, "message": critical_error}