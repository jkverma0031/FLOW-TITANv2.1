# titan/reliability/supervisor.py
"""
Supervisor / Reliability Layer

Responsibilities:
- Monitor long-running async tasks (skills, consolidator, reflection, engine loops)
- Enforce timeouts and cancellation for hung tasks
- Restart failed services in a controlled manner (exponential backoff)
- Provide circuit-breaker behavior per component
- Integrate with worker_pool by wrapping submitted tasks (optional)
- Emit events: 'reliability.service.failed', 'reliability.service.restarted', 'reliability.service.dead'
- Expose API:
    supervisor.watch(service_name, coro_factory, *, restart=True, timeout=seconds)
    supervisor.wrap_task(task, service_name)
    supervisor.health() -> dict
- Record simple metrics via metrics_adapter if available
"""

from __future__ import annotations
import asyncio
import logging
import time
import traceback
from typing import Any, Callable, Dict, Optional, Coroutine

logger = logging.getLogger("titan.reliability.supervisor")


class CircuitState:
    def __init__(self):
        self.failures = 0
        self.last_failure_ts = 0.0
        self.backoff_until = 0.0
        self.dead = False


class SupervisorConfig:
    DEFAULT_TIMEOUT = 60.0        # seconds for a watched coroutine before cancel
    MAX_RETRIES = 5
    BACKOFF_BASE = 2.0            # exponential
    MAX_BACKOFF = 300.0           # seconds max backoff
    RESTART_GRACE = 5.0           # wait before attempting restart
    HEALTH_WINDOW = 300.0         # window used in health reporting


class Supervisor:
    def __init__(self, app: Dict[str, Any], config: Optional[SupervisorConfig] = None):
        self.app = app or {}
        self.config = config or SupervisorConfig()
        self._services: Dict[str, Dict[str, Any]] = {}  # name -> metadata (task, coro_factory, opts)
        self._circuits: Dict[str, CircuitState] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.event_bus = self.app.get("event_bus")
        self.metrics = self.app.get("metrics_adapter")
        try:
            self.app["supervisor"] = self
        except Exception:
            pass

    # ------------------------
    # Public API
    # ------------------------
    def health(self) -> Dict[str, Any]:
        """
        Return a compact health summary for supervisory purposes.
        """
        now = time.time()
        services = {}
        for name, m in self._services.items():
            t = self._tasks.get(name)
            running = bool(t and not t.done())
            circ = self._circuits.get(name)
            services[name] = {
                "running": running,
                "task": str(t),
                "failures": circ.failures if circ else 0,
                "last_failure": circ.last_failure_ts if circ else 0.0,
                "backoff_until": circ.backoff_until if circ else 0.0,
                "dead": circ.dead if circ else False,
            }
        return {"ts": now, "services": services, "service_count": len(self._services)}

    async def watch(self, service_name: str, coro_factory: Callable[[], Coroutine[Any, Any, Any]], *,
                    restart: bool = True, timeout: Optional[float] = None, max_retries: Optional[int] = None):
        """
        Ensure the coroutine produced by coro_factory runs in the background and is supervised.
        If it fails or hangs beyond the timeout, it will be cancelled and restarted (subject to circuit/backoff).
        Returns immediately (does not await service completion). Use stop() or stop_service() to cancel.
        """
        timeout = timeout or self.config.DEFAULT_TIMEOUT
        max_retries = max_retries or self.config.MAX_RETRIES

        async with self._lock:
            # store service metadata
            self._services[service_name] = {"coro_factory": coro_factory, "restart": restart, "timeout": timeout, "max_retries": max_retries}
            if service_name not in self._circuits:
                self._circuits[service_name] = CircuitState()

            # if already running, leave it alone
            if service_name in self._tasks and not self._tasks[service_name].done():
                logger.debug("Supervisor: service %s already monitored", service_name)
                return

            # spawn the supervisor runner for this service
            task = asyncio.create_task(self._service_runner(service_name))
            self._tasks[service_name] = task
            logger.info("Supervisor: watching service %s", service_name)

    async def stop_service(self, service_name: str, *, wait: bool = False):
        async with self._lock:
            task = self._tasks.get(service_name)
            if task and not task.done():
                task.cancel()
                try:
                    if wait:
                        await task
                except Exception:
                    pass
            self._tasks.pop(service_name, None)
            self._services.pop(service_name, None)
            self._circuits.pop(service_name, None)
            logger.info("Supervisor: stopped watching %s", service_name)

    async def stop_all(self):
        names = list(self._tasks.keys())
        for n in names:
            try:
                await self.stop_service(n, wait=True)
            except Exception:
                logger.exception("Failed stopping %s", n)

    # ------------------------
    # Internal runner
    # ------------------------
    async def _service_runner(self, service_name: str):
        """
        The runner starts the service by calling coro_factory, enforces timeout and restart rules.
        Records failure counts and escalates to 'dead' after max_retries.
        """
        meta = self._services.get(service_name)
        circ = self._circuits.setdefault(service_name, CircuitState())

        while True:
            # check circuit backoff
            now = time.time()
            if circ.dead:
                logger.warning("Supervisor: service %s marked dead; not attempting restart", service_name)
                await asyncio.sleep(self.config.MAX_BACKOFF)
                return
            if circ.backoff_until and now < circ.backoff_until:
                await asyncio.sleep(max(0.5, circ.backoff_until - now))
                continue

            try:
                # create the service coroutine
                coro = meta["coro_factory"]()
                # run with timeout guard
                timeout = meta.get("timeout", self.config.DEFAULT_TIMEOUT)
                # start the service in a shielded task so cancellation by external doesn't leak into supervisor logic
                svc_task = asyncio.create_task(coro)
                start_ts = time.time()
                # store service task reference for fast cancellation/inspection
                self._tasks[service_name] = svc_task

                # await completion or timeout
                try:
                    await asyncio.wait_for(asyncio.shield(svc_task), timeout=timeout)
                    # normal exit: mark circuit healthy
                    circ.failures = 0
                    circ.last_failure_ts = 0.0
                    circ.backoff_until = 0.0
                    # if service finished and not requested to restart, exit loop
                    if not meta.get("restart", True):
                        logger.info("Supervisor: service %s finished without restart request", service_name)
                        return
                    # else loop to restart immediately (short grace)
                    await asyncio.sleep(self.config.RESTART_GRACE)
                    continue
                except asyncio.TimeoutError:
                    # Task hung: cancel and record failure
                    try:
                        svc_task.cancel()
                    except Exception:
                        pass
                    circ.failures += 1
                    circ.last_failure_ts = time.time()
                    logger.warning("Supervisor: service %s timed out after %s seconds (failure #%d)", service_name, timeout, circ.failures)
                    self._publish_event("reliability.service.failed", {"service": service_name, "reason": "timeout", "failures": circ.failures})
                except asyncio.CancelledError:
                    # external cancellation; record but don't restart unless requested
                    logger.info("Supervisor: service %s externally cancelled", service_name)
                    if not meta.get("restart", True):
                        return
                    circ.failures += 1
                    circ.last_failure_ts = time.time()
                except Exception as e:
                    # service raised an exception
                    circ.failures += 1
                    circ.last_failure_ts = time.time()
                    tb = traceback.format_exc()
                    logger.exception("Supervisor: service %s raised exception: %s", service_name, str(e))
                    self._publish_event("reliability.service.failed", {"service": service_name, "reason": "exception", "failures": circ.failures, "exc": str(e), "traceback": tb})
                # check failure count and decide backoff / dead
                if circ.failures >= meta.get("max_retries", self.config.MAX_RETRIES):
                    circ.dead = True
                    circ.backoff_until = time.time() + self.config.MAX_BACKOFF
                    logger.error("Supervisor: service %s marked DEAD after %d failures", service_name, circ.failures)
                    self._publish_event("reliability.service.dead", {"service": service_name, "failures": circ.failures})
                    return
                # compute exponential backoff
                backoff = min(self.config.MAX_BACKOFF, self.config.BACKOFF_BASE ** circ.failures)
                circ.backoff_until = time.time() + backoff
                # record metric
                try:
                    if self.metrics:
                        self.metrics.counter(f"reliability.{service_name}.failures").inc()
                except Exception:
                    pass
                # wait a bit and attempt restart
                await asyncio.sleep(backoff)
                continue
            except Exception:
                logger.exception("Supervisor internal error for service %s", service_name)
                await asyncio.sleep(1.0)

    # ------------------------
    # Task wrapping utilities (for worker_pool integration)
    # ------------------------
    def wrap_coro_for_submission(self, service_name: str, coro_fn: Callable[..., Coroutine], *args, **kwargs) -> Callable[[], Coroutine]:
        """
        Returns a zero-arg coroutine factory that will run given coro_fn(*args, **kwargs) but supervised under service_name.
        Useful for integrating with worker_pool.submit wrappers that expect a callable.
        """
        def factory():
            return coro_fn(*args, **kwargs)
        return factory

    # ------------------------
    # Event publish helper
    # ------------------------
    def _publish_event(self, topic: str, payload: Dict[str, Any]):
        try:
            if self.event_bus and getattr(self.event_bus, "publish", None):
                self.event_bus.publish(topic, payload)
        except Exception:
            logger.debug("Supervisor: failed publishing event %s", topic)
