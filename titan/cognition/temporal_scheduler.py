# titan/cognition/temporal_scheduler.py
"""
Temporal Scheduler - improved version

Features / improvements:
 - robust persistent job store via session_manager (if available)
 - priority queue driven trigger loop (efficient)
 - supports worker_pool submission and coroutine callbacks
 - safe cancellation and idempotent scheduling
 - better logging and metrics integration
"""
from __future__ import annotations
import asyncio
import logging
import time
import uuid
import heapq
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

class ScheduledJob:
    def __init__(self, job_id: str, start_ts: float, payload: Dict[str, Any], recurrence: Optional[float] = None):
        self.job_id = job_id
        self.start_ts = float(start_ts)
        self.payload = payload or {}
        self.recurrence = recurrence
        self.last_run: Optional[float] = None
        self.cancelled = False

    def next_run(self):
        if self.last_run is None:
            return self.start_ts
        if self.recurrence:
            return self.last_run + self.recurrence
        return float("inf")

    def to_dict(self):
        return {"id": self.job_id, "start_ts": self.start_ts, "payload": self.payload, "recurrence": self.recurrence, "last_run": self.last_run, "cancelled": self.cancelled}


class TemporalScheduler:
    def __init__(self, app: Dict[str, Any]):
        self.app = app or {}
        self.event_bus = app.get("event_bus")
        self.session_manager = app.get("session_manager")
        self.worker_pool = app.get("worker_pool")
        self._jobs: Dict[str, ScheduledJob] = {}
        self._pq: List[Tuple[float, str]] = []  # (next_run_ts, job_id)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._persistence_key = "cognition.scheduler.jobs"
        # attempt to load persisted jobs
        self._load_persisted_jobs()

    # ------------------------
    # Persistence helpers
    # ------------------------
    def _persist_jobs(self):
        try:
            sid = self.app.get("default_session_id")
            if sid and self.session_manager:
                # store minimal serializable representations
                serial = {jid: j.to_dict() for jid, j in self._jobs.items()}
                try:
                    self.session_manager.update(sid, context={self._persistence_key: serial})
                except Exception:
                    # fallback direct save
                    s = self.session_manager.get(sid) or {}
                    ctx = s.get("context", {}) or {}
                    ctx[self._persistence_key] = serial
                    try:
                        self.session_manager._enqueue_save(sid, s)
                    except Exception:
                        logger.debug("TemporalScheduler: persistence fallback failed")
        except Exception:
            logger.exception("TemporalScheduler: failed to persist jobs")

    def _load_persisted_jobs(self):
        try:
            sid = self.app.get("default_session_id")
            if sid and self.session_manager:
                s = self.session_manager.get(sid) or {}
                ctx = s.get("context", {}) or {}
                jobs = ctx.get(self._persistence_key, {}) or {}
                for jid, j in jobs.items():
                    job = ScheduledJob(job_id=jid, start_ts=j.get("start_ts", time.time()), payload=j.get("payload", {}), recurrence=j.get("recurrence"))
                    job.last_run = j.get("last_run")
                    job.cancelled = j.get("cancelled", False)
                    self._jobs[jid] = job
                    nr = job.next_run()
                    if nr != float("inf") and not job.cancelled:
                        heapq.heappush(self._pq, (nr, jid))
        except Exception:
            logger.debug("TemporalScheduler: no persisted jobs or failed to load")

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
        logger.info("TemporalScheduler stopped")

    # ------------------------
    # API
    # ------------------------
    def schedule(self, start_ts: float, payload: Dict[str, Any], *, recurrence: Optional[float] = None, job_id: Optional[str] = None) -> str:
        jid = job_id or f"job_{uuid.uuid4().hex[:8]}"
        job = ScheduledJob(job_id=jid, start_ts=start_ts, payload=payload, recurrence=recurrence)
        self._jobs[jid] = job
        nr = job.next_run()
        if nr != float("inf") and not job.cancelled:
            heapq.heappush(self._pq, (nr, jid))
        self._persist_jobs()
        logger.info("Scheduled job %s at %s recurrence=%s", jid, time.ctime(start_ts), str(recurrence))
        return jid

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job.cancelled = True
        # job remains in _pq but will be skipped on trigger
        self._persist_jobs()
        logger.info("Cancelled job %s", job_id)
        return True

    def list(self) -> List[Dict[str, Any]]:
        return [j.to_dict() for j in self._jobs.values()]

    # ------------------------
    # Internal loop
    # ------------------------
    async def _loop(self):
        while self._running:
            now_ts = time.time()
            next_run = None
            while self._pq and self._pq[0][0] <= now_ts:
                _, jid = heapq.heappop(self._pq)
                job = self._jobs.get(jid)
                if not job or job.cancelled:
                    continue
                # trigger job
                await self._trigger_job_safe(job)
                # update last_run and reschedule if recurring
                job.last_run = time.time()
                if job.recurrence and not job.cancelled:
                    heapq.heappush(self._pq, (job.next_run(), jid))
                else:
                    # one-off: remove after run
                    try:
                        del self._jobs[jid]
                    except Exception:
                        pass
                self._persist_jobs()
            # determine sleep time until next job
            if self._pq:
                next_run = max(0.0, self._pq[0][0] - time.time())
            # sleep a short interval (bounded) to be responsive
            await asyncio.sleep(min(1.0, next_run if next_run is not None else 1.0))

    async def _trigger_job_safe(self, job: ScheduledJob):
        payload = job.payload or {}
        try:
            # publish to event bus if available
            if self.event_bus and getattr(self.event_bus, "publish", None):
                try:
                    self.event_bus.publish(payload.get("type", "scheduler.trigger"), payload)
                    return
                except Exception:
                    logger.exception("EventBus publish failed for scheduled job %s", job.job_id)
            # else use worker_pool to execute if payload contains a callable
            if self.worker_pool and getattr(self.worker_pool, "submit", None):
                try:
                    # support coroutine functions specified as payload["callable"] or payload["coro"]
                    call = payload.get("callable") or payload.get("call")
                    coro = payload.get("coro")
                    if coro and hasattr(coro, "__call__"):
                        # if coro is function/coroutine function, submit accordingly
                        if asyncio.iscoroutinefunction(coro):
                            # run supervised as background task (non-blocking)
                            asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, coro())
                        else:
                            # wrapper sync work
                            self.worker_pool.submit(coro)
                        return
                    if call:
                        self.worker_pool.submit(lambda: call(payload))
                        return
                except Exception:
                    logger.exception("Worker pool submission failed for job %s", job.job_id)
            # fallback: log
            logger.info("Scheduler trigger fallback: %s", payload)
        except Exception:
            logger.exception("Failed triggering scheduled job %s", job.job_id)
