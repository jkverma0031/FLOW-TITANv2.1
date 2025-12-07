# Path: titan/executor/orchestrator.py
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
    """

    def __init__(
        self,
        worker_pool: WorkerPool,
        runner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        event_emitter: Optional[Callable[[Event], None]] = None,
        max_workers: int = 8,
        condition_evaluator: Optional[ConditionEvaluator] = None 
    ):
        self.runner = runner 
        self.event_emitter = event_emitter
        self.max_workers = max_workers
        self.worker_pool = worker_pool
        self.cond_eval = condition_evaluator 


    def execute_plan(
        self,
        plan: Plan,
        session_id: str,
        replanner_fn: Optional[Callable[[dict], Plan]] = None,
        # FIX: Allow injecting an external state tracker (crucial for testing shared state)
        state_tracker: Optional[StateTracker] = None
    ) -> Dict[str, Any]:

        with tracer.span("orchestrator.execute_plan"):
            metrics.counter("orchestrator.plans_started").inc()

            logger.info("Orchestrator: executing plan",
                        extra={
                            "plan_id": plan.id,
                            "session_id": session_id,
                        })

            if plan.status != PlanStatus.CREATED:
                logger.warning("Orchestrator: executing plan not in CREATED status",
                               extra={"plan_id": plan.id})

            cfg: CFG = plan.cfg

            # FIX: Use injected state tracker if provided, otherwise create new.
            state = state_tracker if state_tracker is not None else StateTracker()

            cond_eval = self.cond_eval or ConditionEvaluator() 
            
            loop_eng = LoopEngine(cond_eval, state)
            retry_eng = RetryEngine()
            replanner = Replanner(replanner_fn) if replanner_fn else None

            scheduler = Scheduler(
                cfg=cfg,
                worker_pool=self.worker_pool,
                state_tracker=state,
                condition_evaluator=cond_eval, 
                loop_engine=loop_eng,
                retry_engine=retry_eng,
                replanner=replanner,
                event_emitter=self.event_emitter
            )

            if self.event_emitter:
                self.event_emitter(Event(
                    type=EventType.PLAN_CREATED,
                    plan_id=plan.id,
                    session_id=session_id,
                    payload={"plan": plan.to_summary()}
                ))

            with tracer.span("orchestrator.scheduler_run"):
                with metrics.timer("orchestrator.scheduler.duration"):
                    summary = scheduler.run(session_id=session_id, plan_id=plan.id)

            if isinstance(summary, dict) and summary.get("status") == "success":
                metrics.counter("orchestrator.plans_completed").inc()
                if self.event_emitter:
                    self.event_emitter(Event(
                        type=EventType.PLAN_COMPLETED,
                        plan_id=plan.id,
                        session_id=session_id,
                        payload={"summary": summary}
                    ))
                logger.info("Plan completed successfully",
                            extra={"plan_id": plan.id, "session_id": session_id})
            else:
                metrics.counter("orchestrator.plans_failed").inc()
                if self.event_emitter:
                    self.event_emitter(Event(
                        type=EventType.ERROR_OCCURRED,
                        plan_id=plan.id,
                        session_id=session_id,
                        payload={"summary": summary}
                    ))
                logger.warning("Plan failed",
                               extra={"plan_id": plan.id, "session_id": session_id})

            return summary