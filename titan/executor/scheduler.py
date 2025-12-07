# Path: FLOW/titan/executor/scheduler.py
from __future__ import annotations
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime
import logging

from titan.schemas.graph import CFG, NodeType, TaskNode, DecisionNode, LoopNode, RetryNode
from titan.schemas.events import Event, EventType
from titan.schemas.action import Action, ActionType
from titan.schemas.task import Task

from .state_tracker import StateTracker
from .worker_pool import WorkerPool
from .condition_evaluator import ConditionEvaluator
from .loop_engine import LoopEngine
from .retry_engine import RetryEngine
from .replanner import Replanner

# Observability imports
from titan.observability.metrics import metrics
from titan.observability.tracing import tracer

logger = logging.getLogger(__name__)


class Scheduler:
    """
    Deterministic CFG interpreter and scheduler.
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
        self.cond = condition_evaluator
        self.loop_eng = loop_engine
        self.retry_eng = retry_engine
        self.replanner = replanner
        self.event_emitter = event_emitter

    def _emit(self, event: Event):
        if self.event_emitter:
            try:
                self.event_emitter(event)
            except Exception:
                logger.exception("event_emitter failed")

    def run(self, session_id: str, plan_id: str) -> Dict[str, Any]:
        """
        Execute the CFG deterministically.
        """
        logger.info("Scheduler: starting execution", extra={"plan_id": plan_id})
        self.cfg.validate_integrity()

        current = self.cfg.entry
        visited = set()

        run_summary = {
            "plan_id": plan_id,
            "start": datetime.utcnow().isoformat(),
            "nodes_executed": []
        }

        while current is not None:
            node = self.cfg.nodes[current]

            # Observability: count scheduled nodes
            metrics.counter("scheduler.nodes_scheduled").inc()

            with tracer.span(f"scheduler.node.{node.type}"):
                logger.info("Scheduler: executing node",
                    extra={
                        "node_id": node.id,
                        "node_type": node.type.value,
                        "plan_id": plan_id,
                        "trace_id": tracer.current_trace_id(),
                        "span_id": tracer.current_span_id(),
                    }
                )

                self.state.init_node(node.id, metadata=node.metadata)
                self._emit(Event(type=EventType.NODE_STARTED,
                                 timestamp=datetime.utcnow().isoformat(),
                                 plan_id=plan_id,
                                 node_id=node.id,
                                 payload={"node": node.dict_safe()}))

                try:
                    # Dispatch by node type
                    if node.type == NodeType.TASK:
                        res = self._execute_task_node(node, session_id, plan_id)
                        succs = self.cfg.get_successors(node.id)
                        current = succs[0] if succs else None

                    elif node.type == NodeType.DECISION:
                        current = self._handle_decision(node, current, plan_id)

                    elif node.type == NodeType.LOOP:
                        current = self._handle_loop(node, current, plan_id)

                    elif node.type == NodeType.RETRY:
                        current = self._handle_retry(node, current, plan_id)

                    elif node.type in (NodeType.NOOP, NodeType.START):
                        succs = self.cfg.get_successors(node.id)
                        current = succs[0] if succs else None

                    elif node.type == NodeType.END:
                        self.state.set_success(node.id, result={"message": "plan_end"})
                        current = None

                    else:
                        raise RuntimeError(f"Unsupported node type: {node.type}")

                    # Emit finished event
                    self._emit(Event(
                        type=EventType.NODE_FINISHED,
                        timestamp=datetime.utcnow().isoformat(),
                        plan_id=plan_id,
                        node_id=node.id,
                        payload={
                            "node": node.dict_safe(),
                            "state": self.state.get_state(node.id)
                        }
                    ))

                    run_summary["nodes_executed"].append(node.id)

                except Exception as e:
                    metrics.counter("scheduler.node_failures").inc()

                    logger.exception("Scheduler: node execution failed",
                                     extra={"node_id": node.id, "plan_id": plan_id})

                    self._emit(Event(
                        type=EventType.ERROR_OCCURRED,
                        timestamp=datetime.utcnow().isoformat(),
                        plan_id=plan_id,
                        node_id=node.id,
                        payload={"error": str(e)}
                    ))

                    if self.replanner:
                        new_plan = self.replanner.maybe_replan({"node": node, "error": str(e)})
                        if new_plan:
                            return {"replan_requested": True, "new_plan": new_plan}

                    self.state.set_failed(node.id, error=str(e), exc=e)
                    return {
                        "plan_id": plan_id,
                        "status": "failed",
                        "failed_node": node.id,
                        "error": str(e)
                    }

        run_summary["end"] = datetime.utcnow().isoformat()
        run_summary["status"] = "success"

        logger.info("Scheduler: completed plan", extra={"plan_id": plan_id})
        return run_summary


    # ----------------------------
    #   TASK NODE EXECUTION
    # ----------------------------
    def _execute_task_node(self, node: TaskNode, session_id: str, plan_id: str) -> Dict[str, Any]:

        with tracer.span(f"scheduler.task.{node.id}"):
            metrics.counter("scheduler.tasks_executed").inc()

            self.state.set_running(node.id)

            meta = node.metadata or {}
            dsl_call = meta.get("dsl_call", {})

            action = Action(
                type=ActionType.EXEC,
                command=dsl_call.get("name"),
                args=dsl_call.get("args", {}),
                timeout_seconds=getattr(node, "timeout_seconds", None),
                metadata={
                    "node_id": node.id,
                    "plan_id": plan_id,
                    "session_id": session_id,
                }
            )

            action_payload = action.to_exec_payload()

            self._emit(Event(
                type=EventType.TASK_STARTED,
                timestamp=None,
                plan_id=plan_id,
                node_id=node.id,
                payload={"action": action_payload},
            ))

            fut = self.worker_pool.submit(action_payload)
            res = fut.result()

            if res.get("success"):
                self.state.set_success(node.id, result=res)
            else:
                self.state.set_failed(node.id, error=str(res.get("error", "action_failed")), exc=res)

            self._emit(Event(
                type=EventType.TASK_FINISHED,
                timestamp=None,
                plan_id=plan_id,
                node_id=node.id,
                payload={"result": res},
            ))

            return res


    # ----------------------------
    #   DECISION NODE
    # ----------------------------
    def _handle_decision(self, node: DecisionNode, current_id: str, plan_id: str) -> Optional[str]:

        with tracer.span(f"scheduler.decision.{node.id}"):
            cond = node.condition
            branch = self.cond.eval_bool(cond)
            succs = [e for e in self.cfg.edges if e.source == node.id]

            chosen = None
            for e in succs:
                if (branch and (e.label == "true" or e.label is None)) or \
                   (not branch and (e.label == "false" or e.label is None)):
                    chosen = e.target
                    break

            if not chosen and succs:
                chosen = succs[0].target

            self._emit(Event(
                type=EventType.DECISION_TAKEN,
                timestamp=datetime.utcnow().isoformat(),
                plan_id=plan_id,
                node_id=node.id,
                payload={"condition": cond, "result": bool(branch), "chosen": chosen},
            ))

            return chosen


    # ----------------------------
    #   LOOP NODE
    # ----------------------------
    def _handle_loop(self, node: LoopNode, current_id: str, plan_id: str) -> Optional[str]:

        with tracer.span(f"scheduler.loop.{node.id}"):

            iterations = self.loop_eng.resolve_iterations(node, run_context=None)

            if not iterations:
                succs = [e for e in self.cfg.edges if e.source == node.id]
                for e in succs:
                    if e.label == "break":
                        return e.target
                return succs[0].target if succs else None

            body_entry = None
            for e in self.cfg.edges:
                if e.source == node.id and e.label in ("body", None):
                    body_entry = e.target
                    break

            if not body_entry:
                return None

            for it in iterations:
                cur = body_entry
                while cur and cur != node.id:
                    n = self.cfg.nodes[cur]
                    if n.type == NodeType.TASK:
                        self._execute_task_node(n, session_id="", plan_id=plan_id)
                    succs = self.cfg.get_successors(cur)
                    cur = succs[0] if succs else None

            succs = [e for e in self.cfg.edges if e.source == node.id]
            for e in succs:
                if e.label == "break":
                    return e.target
            return succs[0].target if succs else None


    # ----------------------------
    #   RETRY NODE
    # ----------------------------
    def _handle_retry(self, node: RetryNode, current_id: str, plan_id: str) -> Optional[str]:

        with tracer.span(f"scheduler.retry.{node.id}"):

            child_id = getattr(node, "metadata", {}).get("child_node_id") or getattr(node, "child_node_id", None)
            attempts = getattr(node, "attempts", 3)
            backoff = getattr(node, "backoff_seconds", 1.0)

            if not child_id:
                return None

            def attempt_fn():
                child_node = self.cfg.nodes.get(child_id)
                if not child_node:
                    return {"success": False, "error": "child_node_missing"}
                if child_node.type == NodeType.TASK:
                    return self._execute_task_node(child_node, session_id="", plan_id=plan_id)
                return {"success": False, "error": "unsupported_retry_child_type"}

            success, out = self.retry_eng.run_with_retries(attempt_fn, attempts=attempts, backoff_seconds=backoff)

            if success:
                succs = self.cfg.get_successors(node.id)
                return succs[0] if succs else None

            self.state.set_failed(node.id, error=str(out))
            return None
