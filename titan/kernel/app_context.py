# titan/kernel/app_context.py
from __future__ import annotations
import threading
import logging
import contextlib
import asyncio
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Sentinel object to distinguish "None passed as default" vs "No default passed"
_SENTINEL = object()

class ServiceNotRegistered(KeyError):
    pass

class AppContext:
    """
    Enterprise-grade application service registry / lightweight DI container.

    - Keeps backward-compatible register/get/unregister semantics from original file.
    - Preserves lazy instantiation of callables (factories) and supports factory(self) signatures.
    - Adds:
      * get_or_create(name, factory)
      * add_startup_task(coro_or_callable)
      * run_startup_tasks()  -> async runner for startup tasks
      * run_shutdown_tasks() -> async runner for shutdown tasks
      * async-aware start/stop for services exposing start_async/stop_async
      * dump() alias for list_services()
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # store: name -> (provider_or_instance, instance_or_None, metadata_dict)
        self._services: Dict[str, Tuple[Callable[[], Any] | Any, Any, Dict[str, Any]]] = {}
        # startup/shutdown hooks (callable or coroutine/coroutinefunction)
        self._startup_tasks: list = []
        self._shutdown_tasks: list = []

    # -----------------------
    # REGISTER
    # -----------------------
    def register(self, name: str, service: Any | Callable[[], Any], metadata: Optional[Dict[str, Any]] = None, replace: bool = False) -> None:
        """
        Register a service or a factory. If 'service' is callable we treat it as a provider
        to be lazily instantiated on first get().
        If replace is False and service already registered -> raises KeyError (same as original).
        """
        metadata = metadata or {}
        with self._lock:
            if name in self._services and not replace:
                raise KeyError(f"Service '{name}' already registered")
            # store (provider, instance(None if factory), metadata)
            instance = service if not callable(service) else None
            self._services[name] = (service, instance, dict(metadata))
            logger.debug("Registered service %s (callable=%s)", name, callable(service))

    # -----------------------
    # UNREGISTER
    # -----------------------
    def unregister(self, name: str) -> None:
        with self._lock:
            if name in self._services:
                del self._services[name]
                logger.debug("Unregistered service %s", name)
            else:
                raise ServiceNotRegistered(name)

    # -----------------------
    # GET
    # -----------------------
    def get(self, name: str, default: Any = _SENTINEL) -> Any:
        """
        Retrieve a service. If the service was registered with a callable provider it will be
        instantiated lazily. The provider may optionally accept the AppContext as a parameter.
        """
        with self._lock:
            if name not in self._services:
                if default is not _SENTINEL:
                    return default
                raise ServiceNotRegistered(f"AppContext: service '{name}' not registered")

            provider, instance, metadata = self._services[name]

            if instance is not None:
                return instance

            # Instantiate lazy service
            if callable(provider):
                try:
                    import inspect
                    sig = inspect.signature(provider)
                    # If factory expects no args -> call directly; if expects one -> pass self
                    if len(sig.parameters) == 0:
                        instance = provider()
                    else:
                        instance = provider(self)
                except Exception:
                    # last-resort, try calling without args
                    instance = provider()
                # save instance
                self._services[name] = (provider, instance, metadata)
                logger.debug("AppContext: instantiated lazy service %s", name)
                return instance
            else:
                # provider is a concrete instance already
                self._services[name] = (provider, provider, metadata)
                return provider

    # -----------------------
    # GET OR CREATE
    # -----------------------
    def get_or_create(self, name: str, factory: Callable[[], Any]) -> Any:
        """
        Retrieve component or create it lazily via factory().
        Factory may accept AppContext as its single parameter.
        """
        with self._lock:
            if name in self._services:
                _, instance, _ = self._services[name]
                if instance is not None:
                    return instance
                # fabricate using provider if provider present
                provider, instance, metadata = self._services[name]
                if callable(provider):
                    try:
                        import inspect
                        sig = inspect.signature(provider)
                        if len(sig.parameters) == 0:
                            inst = provider()
                        else:
                            inst = provider(self)
                    except Exception:
                        inst = provider()
                    self._services[name] = (provider, inst, metadata)
                    return inst
                # if stored non-callable (unexpected), return it
                return provider
            # create, register and return
            try:
                import inspect
                sig = inspect.signature(factory)
                if len(sig.parameters) == 0:
                    instance = factory()
                else:
                    instance = factory(self)
            except Exception:
                instance = factory()
            # register concrete instance
            self._services[name] = (instance, instance, {})
            logger.debug("AppContext: get_or_create created service %s", name)
            return instance

    # -----------------------
    # HAS
    # -----------------------
    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._services

    # -----------------------
    # LIST / DUMP
    # -----------------------
    def list_services(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                name: {"materialized": (instance is not None), "metadata": dict(metadata)}
                for name, (_, instance, metadata) in self._services.items()
            }

    # backward-compatible alias
    def dump(self) -> Dict[str, Dict[str, Any]]:
        return self.list_services()

    # -----------------------
    # START / STOP services (sync & async aware)
    # -----------------------
    def start_services(self) -> None:
        """
        Calls start() on all registered services that expose it (sync). If a service exposes
        an async start_async coroutine method we run it in the event loop if available or synchronously via asyncio.run.
        """
        with self._lock:
            for name in list(self._services.keys()):
                provider, instance, metadata = self._services[name]
                try:
                    svc = self.get(name)
                    # preferred async start method
                    if hasattr(svc, "start_async") and callable(getattr(svc, "start_async")):
                        try:
                            # try to run async start in event loop if present
                            try:
                                loop = asyncio.get_running_loop()
                                # schedule without awaiting
                                asyncio.run_coroutine_threadsafe(svc.start_async(), loop)
                            except RuntimeError:
                                # no running loop; run synchronously
                                asyncio.run(svc.start_async())
                        except Exception:
                            logger.exception("Error starting async service %s", name)
                    # fallback to sync start
                    elif hasattr(svc, "start") and callable(svc.start):
                        try:
                            svc.start()
                        except Exception:
                            logger.exception("Error starting service %s", name)
                except Exception:
                    # service instantiation could fail; ignore to preserve boot resilience
                    logger.exception("start_services: failed to start service %s", name)

    def stop_services(self) -> None:
        """
        Calls stop() on all registered services in reverse registration order.
        If a service exposes stop_async, try to run it in the running event loop or synchronously.
        """
        with self._lock:
            for name in list(self._services.keys())[::-1]:
                try:
                    svc = self.get(name)
                    if hasattr(svc, "stop_async") and callable(getattr(svc, "stop_async")):
                        try:
                            try:
                                loop = asyncio.get_running_loop()
                                asyncio.run_coroutine_threadsafe(svc.stop_async(), loop)
                            except RuntimeError:
                                asyncio.run(svc.stop_async())
                        except Exception:
                            logger.exception("Error stopping async service %s", name)
                    elif hasattr(svc, "stop") and callable(svc.stop):
                        try:
                            svc.stop()
                        except Exception:
                            logger.exception("Error stopping service %s", name)
                except Exception:
                    logger.exception("stop_services: failed to stop service %s", name)

    # -----------------------
    # STARTUP / SHUTDOWN TASKS
    # -----------------------
    def add_startup_task(self, coro_or_func: Callable[..., Any]) -> None:
        """
        Register a startup task. It can be:
          - a coroutine function (async def)
          - a coroutine object
          - a sync function to be run in threadpool
        These tasks will be executed by run_startup_tasks().
        """
        with self._lock:
            self._startup_tasks.append(coro_or_func)
            logger.debug("AppContext: added startup task %s", getattr(coro_or_func, "__name__", str(coro_or_func)))

    def add_shutdown_task(self, coro_or_func: Callable[..., Any]) -> None:
        with self._lock:
            self._shutdown_tasks.append(coro_or_func)
            logger.debug("AppContext: added shutdown task %s", getattr(coro_or_func, "__name__", str(coro_or_func)))

    async def run_startup_tasks(self, *, stop_on_failure: bool = False, concurrency: int = 4) -> None:
        """
        Run registered startup tasks. This is async and will:
          - await coroutine objects or coroutine functions
          - run synchronous callables in a threadpool executor
        stop_on_failure: if True, abort on first failure (default False)
        concurrency: number of tasks to run in parallel
        """
        tasks = []
        # create local copy
        with self._lock:
            seq = list(self._startup_tasks)

        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(concurrency)

        async def _run_one(item):
            async with contextlib.asynccontextmanager(lambda: (yield))():
                pass  # placeholder to satisfy asynccontextmanager usage in older python versions

        # We'll schedule tasks honoring their type
        async def _runner(item):
            await asyncio.sleep(0)  # yield control
            try:
                # If item is a coroutine object (already created), await directly
                if asyncio.iscoroutine(item):
                    return await item
                # If item is a coroutine function
                if asyncio.iscoroutinefunction(item):
                    return await item()
                # Else assume it's a sync callable -> run in threadpool
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, item)
            except Exception:
                logger.exception("AppContext: startup task failed: %s", getattr(item, "__name__", str(item)))
                if stop_on_failure:
                    raise

        # schedule all runners with concurrency limit
        async def _schedule_all():
            sem = asyncio.Semaphore(concurrency)
            coros = []
            for it in seq:
                async def _sem_runner(it=it):
                    async with _AsyncSemaphoreContext(sem):
                        return await _runner(it)
                coros.append(asyncio.create_task(_sem_runner()))
            # await all, propagate exceptions
            results = await asyncio.gather(*coros, return_exceptions=True)
            # log exceptions if any
            for r in results:
                if isinstance(r, Exception):
                    logger.debug("AppContext: startup task returned exception: %s", r)
            return results

        await _schedule_all()

    async def run_shutdown_tasks(self, *, concurrency: int = 4) -> None:
        """
        Run registered shutdown tasks (reverse order). Accepts same task types as startup tasks.
        """
        with self._lock:
            seq = list(self._shutdown_tasks)[::-1]

        async def _runner(item):
            try:
                if asyncio.iscoroutine(item):
                    return await item
                if asyncio.iscoroutinefunction(item):
                    return await item()
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, item)
            except Exception:
                logger.exception("AppContext: shutdown task failed: %s", getattr(item, "__name__", str(item)))
                return None

        coros = [asyncio.create_task(_runner(it)) for it in seq]
        await asyncio.gather(*coros, return_exceptions=True)

    # -----------------------
    # HEALTH
    # -----------------------
    def health(self) -> Dict[str, Any]:
        """
        Basic health summary (sync). More advanced health checks can be registered separately.
        """
        return {"services": self.list_services()}


# -----------------------
# Small helper to use Semaphore as async context manager for older python
# -----------------------
class _AsyncSemaphoreContext:
    def __init__(self, sem: asyncio.Semaphore):
        self._sem = sem

    async def __aenter__(self):
        await self._sem.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._sem.release()
