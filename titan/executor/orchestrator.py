# titan/executor/orchestrator.py
from __future__ import annotations
import asyncio
import time
import logging
from typing import Optional, Dict, Any, List

from titan.schemas.plan import Plan
from titan.schemas.events import Event, EventType
from titan.schemas.graph import CFGNodeType
from titan.schemas.action import Action, ActionType

from .worker_pool import WorkerPool
from .state_tracker import StateTracker
from titan.augmentation.negotiator import Negotiator
from titan.augmentation.sandbox.sandbox_runner import SandboxRunner
from titan.augmentation.hostbridge.hostbridge_service import HostBridgeService

logger = logging.getLogger(__name__)

class Orchestrator:
    """
    Async-first orchestrator with parallel execution support.
    Public API:
      - async execute_plan_async(plan)
      - execute_plan(plan)  (sync wrapper)
    Parallel execution:
      - If a node has metadata.parallel == True, its children (nodes in the same group) run concurrently.
      - If the plan contains a special node.type == 'PARALLEL', orchestrator will execute its 'branches' concurrently.
    """

    def __init__(self, worker_pool: Optional[WorkerPool] = None, event_emitter: Optional[callable] = None, policy_engine: Optional[Any] = None):
        self.worker_pool = worker_pool or WorkerPool()
        self.event_emitter = event_emitter
        self._policy_engine = policy_engine
        self._negotiator = Negotiator(policy_engine=policy_engine)
        try:
            self._sandbox = SandboxRunner(policy_engine=policy_engine)
        except Exception:
            self._sandbox = None
        try:
            self._hostbridge = HostBridgeService(policy_engine=policy_engine)
        except Exception:
            self._hostbridge = None

    def _emit_event(self, event: Event):
        if callable(self.event_emitter):
            try:
                self.event_emitter(event)
            except Exception:
                logger.exception("Event emitter failed")
        else:
            logger.debug("Event: %s", event)

    def _normalize_cfg_nodes(self, plan: Plan) -> List[Dict[str, Any]]:
        if isinstance(plan.cfg, dict) and "nodes" in plan.cfg:
            return plan.cfg["nodes"]
        if isinstance(plan.cfg, list):
            return plan.cfg
        if hasattr(plan.cfg, "nodes"):
            return getattr(plan.cfg, "nodes")
        raise RuntimeError("Unknown plan.cfg structure")

    async def _execute_node(self, plan: Plan, node: Dict[str, Any], state_tracker: StateTracker) -> Dict[str, Any]:
        node_type = node.get("type") or node.get("node_type")
        node_id = node.get("id")
        name = node.get("name") or node_id
        metadata = node.get("metadata") or {}
        task_args = metadata.get("task_args", {}) if isinstance(metadata, dict) else {}
        task_ref = node.get("task_ref") or node.get("task_name") or node.get("name")

        # Build Action object
        action_type_str = metadata.get("action_type") or metadata.get("type") or "exec"
        try:
            at = ActionType[action_type_str.upper()] if isinstance(action_type_str, str) and action_type_str.upper() in ActionType.__members__ else ActionType.EXEC
        except Exception:
            at = ActionType.EXEC

        action_kwargs = {
            "type": at,
            "module": metadata.get("module") or metadata.get("plugin"),
            "args": task_args,
            "command": metadata.get("command") or task_args.get("cmd") if isinstance(task_args, dict) else None,
            "metadata": metadata,
        }
        try:
            action = Action(**action_kwargs)
        except Exception:
            # fallback namespace
            class _FallbackAction:
                pass
            action = _FallbackAction()
            action.type = at
            action.module = action_kwargs["module"]
            action.args = action_kwargs["args"]
            action.command = action_kwargs["command"]
            action.metadata = metadata

        context = {
            "plan_id": getattr(plan, "id", None),
            "node_id": node_id,
            "user_id": getattr(plan, "metadata", {}).get("user_id") if getattr(plan, "metadata", None) else None,
            "trust_level": getattr(plan, "metadata", {}).get("trust_level") if getattr(plan, "metadata", None) else None,
        }

        # Emit start
        try:
            self._emit_event(Event(type=EventType.NODE_STARTED, payload={"node_id": node_id, "name": name}))
        except Exception:
            logger.exception("Failed to emit NODE_STARTED")

        # Prepare action_request
        action_request = {
            "action": action,
            "node": node,
            "node_id": node_id,
            "task_name": name,
            "task_args": task_args,
            "context": context,
            "_negotiator": self._negotiator,
            "_sandbox": self._sandbox,
            "_hostbridge": self._hostbridge,
            "_policy_engine": self._policy_engine,
        }

        # Pre policy check at orchestrator level
        if self._policy_engine is not None:
            try:
                # try async policy if available
                if hasattr(self._policy_engine, "allow_action_async") and asyncio.iscoroutinefunction(self._policy_engine.allow_action_async):
                    allowed, reason = await self._policy_engine.allow_action_async(actor=context.get("user_id", "system"), trust_level=context.get("trust_level", "low"), action="execute_node", resource={"node_id": node_id, "task": task_ref})
                else:
                    allowed, reason = await asyncio.get_event_loop().run_in_executor(None, lambda: self._policy_engine.allow_action(context.get("user_id", "system"), context.get("trust_level", "low"), "execute_node", {"node_id": node_id, "task": task_ref}))
                if not allowed:
                    result = {"status": "error", "error": f"policy_denied:{reason}"}
                    # emit finished
                    try:
                        self._emit_event(Event(type=EventType.NODE_FINISHED, payload={"node_id": node_id, "result": result}))
                    except Exception:
                        pass
                    return result
            except Exception:
                logger.exception("Policy engine pre-check failed; proceeding by default")

        # Delegate to WorkerPool (async)
        exec_result = await self.worker_pool.run_async(action_request)

        # Update state tracker
        try:
            status_str = "completed" if exec_result.get("status") in ("ok","success") else "failed"
            if hasattr(state_tracker, "update_state"):
                state_tracker.update_state(node_id, status=status_str, result=exec_result.get("result"))
        except Exception:
            logger.exception("Failed to update state tracker")

        # Emit finished event
        try:
            self._emit_event(Event(type=EventType.NODE_FINISHED, payload={"node_id": node_id, "result": exec_result}))
        except Exception:
            logger.exception("Failed to emit NODE_FINISHED")

        return exec_result

    async def execute_plan_async(self, plan: Plan, state_tracker: Optional[StateTracker] = None) -> Dict[str, Any]:
        start = time.time()
        logger.info("Orchestrator: executing plan id=%s", getattr(plan, "id", "<no-id>"))
        if state_tracker is None:
            state_tracker = StateTracker()

        nodes = self._normalize_cfg_nodes(plan)
        results = []

        # High-level execution: support parallel groups
        i = 0
        while i < len(nodes):
            node = nodes[i]
            node_type = node.get("type") or node.get("node_type")
            # handle structural nodes
            if node_type in (CFGNodeType.START, CFGNodeType.END) or str(node_type).lower() in ("start","end","noop"):
                i += 1
                continue

            # If node metadata requests a parallel group, collect group
            metadata = node.get("metadata") or {}
            if metadata.get("parallel_group") or metadata.get("parallel") or node_type == "PARALLEL":
                # collect consecutive nodes with same parallel marker or belonging to this group
                group = [node]
                j = i + 1
                while j < len(nodes):
                    n2 = nodes[j]
                    m2 = n2.get("metadata") or {}
                    if m2.get("parallel_group") == metadata.get("parallel_group") or m2.get("parallel") or n2.get("type") == "PARALLEL":
                        group.append(n2)
                        j += 1
                    else:
                        break
                # run group concurrently
                tasks = [asyncio.create_task(self._execute_node(plan, n, state_tracker)) for n in group]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
                for t in done:
                    try:
                        res = t.result()
                    except Exception as e:
                        res = {"status": "error", "error": str(e)}
                    results.append({"node_id": getattr(t, "node_id", None), "result": res})
                    # abort on failure if necessary
                    if res.get("status") not in ("ok","success"):
                        logger.warning("Parallel node failed: aborting plan")
                        return {"plan_id": getattr(plan, "id", None), "elapsed": time.time() - start, "results": results}
                i = j
                continue
            else:
                # normal sequential execution
                res = await self._execute_node(plan, node, state_tracker)
                results.append({"node_id": node.get("id"), "result": res})
                if res.get("status") not in ("ok","success"):
                    logger.warning("Node failed, aborting plan execution")
                    break
                i += 1

        elapsed = time.time() - start
        logger.info("Orchestrator: plan execution finished in %.2fs", elapsed)
        return {"plan_id": getattr(plan, "id", None), "elapsed": elapsed, "results": results}

    def execute_plan(self, plan: Plan, state_tracker: Optional[StateTracker] = None) -> Dict[str, Any]:
        """
        Sync wrapper for execute_plan_async for backward compatibility.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # running loop: schedule and return result via run_coroutine_threadsafe
                fut = asyncio.run_coroutine_threadsafe(self.execute_plan_async(plan, state_tracker), loop)
                return fut.result()
            else:
                return asyncio.run(self.execute_plan_async(plan, state_tracker))
        except Exception as e:
            logger.exception("execute_plan failed")
            return {"plan_id": getattr(plan, "id", None), "elapsed": 0.0, "results": [{"error": str(e)}]}
