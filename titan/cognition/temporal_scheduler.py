# titan/cognition/temporal_scheduler.py
"""
Temporal Scheduler (enterprise-grade)

Responsibilities:
- Schedule one-off or recurring tasks
- Trigger skill proposals based on time, calendar, or computed 'next-action' predictions
- Expose simple API:
    schedule(start_ts, callback_payload, recurrence=None, id=None)
    cancel(schedule_id)
    list()
- Internally publishes events to EventBus when triggers fire
- Integrates with session_manager to persist scheduled jobs
- Supports safe execution via worker_pool if provided
"""
from __future__ import annotations
import asyncio
import logging
import time
import uuid
from typing import Dict, Any, Optional, List

logger = logging.getLogger("titan.cognition.temporal_scheduler")


class TemporalScheduler:
    def __init__(self, app: Dict[str, Any]):
        self.app = app
        self.event_bus = app.get("event_bus")
        self.session_manager = app.get("session_manager")
        self.worker_pool = app.get("worker_pool")
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._persistence_key = "cognition.scheduler.jobs"
        # try load persisted jobs
        try:
            sid = app.get("default_session_id")
            if sid and self.session_manager:
                s = self.session_manager.get(sid) or {}
                ctx = s.get("context", {}) or {}
                jobs = ctx.get(self._persistence_key, {}) or {}
                self._jobs = {k: v for k, v in jobs.items()}
        except Exception:
            logger.debug("Failed to load persisted jobs")

    # ------------------------
    # Lifecycle
    # ------------------------
    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TemporalScheduler started")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            try:
                self._task.cancel()
                await self._task
            except Exception:
                pass
            self._task = None
        logger.info("TemporalScheduler stopped")

    # ------------------------
    # Job API
    # ------------------------
    def schedule(self, start_ts: float, payload: Dict[str, Any], *, recurrence: Optional[float] = None, job_id: Optional[str] = None) -> str:
        """
        Schedule a job.
        payload: dict - will be published to event bus when trigger fires; expected to contain at least 'type'
        recurrence: seconds between repeats; None for one-off
        """
        jid = job_id or f"job_{uuid.uuid4().hex[:8]}"
        self._jobs[jid] = {"id": jid, "start_ts": float(start_ts), "payload": payload, "recurrence": recurrence, "last_run": None}
        self._persist_jobs()
        logger.info("Scheduled job %s for %s (recurrence=%s)", jid, time.ctime(start_ts), str(recurrence))
        return jid

    def cancel(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._persist_jobs()
            logger.info("Cancelled job %s", job_id)
            return True
        return False

    def list(self) -> List[Dict[str, Any]]:
        return list(self._jobs.values())

    # ------------------------
    # Loop
    # ------------------------
    async def _loop(self):
        while self._running:
            now_ts = time.time()
            due = []
            for jid, job in list(self._jobs.items()):
                start_ts = job.get("start_ts", 0)
                last_run = job.get("last_run")
                recurrence = job.get("recurrence")
                # if not run yet and due
                if last_run is None and now_ts >= start_ts:
                    due.append(job)
                # if recurring and next due
                if last_run is not None and recurrence:
                    if now_ts >= last_run + recurrence:
                        due.append(job)
            # trigger due jobs
            for job in due:
                try:
                    await self._trigger_job(job)
                    # update last_run
                    jid = job["id"]
                    self._jobs[jid]["last_run"] = time.time()
                    # if not recurring, remove
                    if not job.get("recurrence"):
                        del self._jobs[jid]
                    self._persist_jobs()
                except Exception:
                    logger.exception("Failed triggering job %s", job.get("id"))
            await asyncio.sleep(1.0)

    async def _trigger_job(self, job: Dict[str, Any]):
        """
        Execute job payload: publish to EventBus or call worker_pool
        """
        payload = job.get("payload", {})
        try:
            if self.event_bus and getattr(self.event_bus, "publish", None):
                self.event_bus.publish(payload.get("type", "scheduler.trigger"), payload)
            elif self.worker_pool and getattr(self.worker_pool, "submit", None):
                # schedule synchronous callback in worker pool
                self.worker_pool.submit(lambda: payload)
            else:
                logger.info("Scheduler trigger: %s", payload)
        except Exception:
            logger.exception("Scheduler trigger failed")

    def _persist_jobs(self):
        """
        Persist _jobs into session_manager under context key self._persistence_key
        """
        try:
            sid = self.app.get("default_session_id")
            if not sid or not self.session_manager:
                return
            try:
                self.session_manager.update(sid, context={self._persistence_key: self._jobs})
            except Exception:
                # fallback
                s = self.session_manager.get(sid) or {}
                ctx = s.get("context", {}) or {}
                ctx[self._persistence_key] = self._jobs
                try:
                    self.session_manager._enqueue_save(sid, s)
                except Exception:
                    logger.debug("Scheduler persist fallback failed")
        except Exception:
            logger.exception("Failed persist jobs")
