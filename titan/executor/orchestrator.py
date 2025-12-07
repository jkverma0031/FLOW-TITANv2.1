# Path: FLOW/titan/executor/orchestrator.py
from __future__ import annotations
from typing import Optional, Callable, Any, Dict

from titan.schemas.plan import Plan, PlanStatus
from titan.schemas.events import Event, EventType
from titan.schemas.graph import CFG

from .worker_pool import WorkerPool
from .state_tracker import StateTracker
from .condition_evaluator import ConditionEvaluator
from .loop_engine import LoopEngine
from .retry_engine import RetryEngine
from .replanner import Replanner
from .scheduler import Scheduler

from titan.observability.tracing import tracer
from titan.observability.metrics import metrics

import logging
logger = logging.getLogger(__name__)


class Orchestrator:
    """
    High-level executor orchestrator.
    Responsibilities:
      - Accept a Plan
      - Instantiate WorkerPool (with a provided runner)
      - Instantiate StateTracker, ConditionEvaluator, Engines
      - Run Scheduler synchronously and return execution summary
    """

    def __init__(
        self,
        runner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[Event], None]] = None,
        max_workers: int = 8
    ):
        self.runner = runner
        self.event_emitter = event_emitter
        self.max_workers = max_workers

    def execute_plan(
        self,
        plan: Plan,
        session_id: str,
        replanner_fn: Optional[Callable[[dict], Plan]] = None
    ) -> Dict[str, Any]:

        # -------- OBSERVABILITY: PLAN-LEVEL SPAN --------
        with tracer.span("orchestrator.execute_plan"):
            metrics.counter("orchestrator.plans_started").inc()

            logger.info("Orchestrator: executing plan",
                        extra={
                            "plan_id": plan.id,
                            "session_id": session_id,
                            "trace_id": tracer.current_trace_id(),
                            "span_id": tracer.current_span_id()
                        })

            if plan.status != PlanStatus.CREATED:
                logger.warning("Orchestrator: executing plan not in CREATED status",
                               extra={"plan_id": plan.id})

            cfg: CFG = plan.cfg

            # instantiate components
            worker_pool = WorkerPool(max_workers=self.max_workers, runner=self.runner)
            state = StateTracker()

            # --------------------------------------------
            # Resolver for condition evaluator
            # --------------------------------------------
            def resolver(name: str):
                parts = name.split(".")
                nid = parts[0]
                st = state.get_state(nid)
                if not st:
                    return None
                val = st.get("result")
                for p in parts[1:]:
                    if val is None:
                        return None
                    if isinstance(val, dict):
                        val = val.get(p)
                    else:
                        val = getattr(val, p, None)
                return val

            cond_eval = ConditionEvaluator(resolver)
            loop_eng = LoopEngine(cond_eval, state)
            retry_eng = RetryEngine()
            replanner = Replanner(replanner_fn) if replanner_fn else None

            # FIX: Updated to use keyword arguments for Scheduler instantiation
            scheduler = Scheduler(
                cfg=cfg,
                worker_pool=worker_pool,
                state_tracker=state,
                condition_evaluator=cond_eval,
                loop_engine=loop_eng,
                retry_engine=retry_eng,
                replanner=replanner,
                event_emitter=self.event_emitter
            )

            # Emit plan created event
            if self.event_emitter:
                self.event_emitter(Event(
                    type=EventType.PLAN_CREATED,
                    timestamp=None,
                    plan_id=plan.id,
                    payload={"plan": plan.to_summary()}
                ))

            # -------- OBSERVABILITY: SCHEDULER EXECUTION SPAN --------
            with tracer.span("orchestrator.scheduler_run"):
                with metrics.timer("orchestrator.scheduler.duration"):
                    summary = scheduler.run(session_id=session_id, plan_id=plan.id)

            # shutdown pool
            try:
                worker_pool.shutdown(wait=True)
            except Exception:
                logger.exception("Worker pool shutdown failed")

            # Emit completion/failure event
            if isinstance(summary, dict) and summary.get("status") == "success":
                metrics.counter("orchestrator.plans_completed").inc()

                if self.event_emitter:
                    self.event_emitter(Event(
                        type=EventType.PLAN_COMPLETED,
                        timestamp=None,
                        plan_id=plan.id,
                        payload={"summary": summary}
                    ))

                logger.info("Plan completed successfully",
                            extra={"plan_id": plan.id, "session_id": session_id})

            else:
                metrics.counter("orchestrator.plans_failed").inc()

                if self.event_emitter:
                    self.event_emitter(Event(
                        type=EventType.ERROR_OCCURRED,
                        timestamp=None,
                        plan_id=plan.id,
                        payload={"summary": summary}
                    ))

                logger.warning("Plan failed",
                               extra={"plan_id": plan.id, "session_id": session_id})

            return summary