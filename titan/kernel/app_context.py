# Path: titan/kernel/app_context.py
from __future__ import annotations
import threading
import logging
import contextlib
from typing import Any, Callable, Dict, Optional, Iterable, Tuple

logger = logging.getLogger(__name__)


class ServiceNotRegistered(KeyError):
    pass


class AppContext:
    """
    Enterprise-grade application service registry / lightweight DI container.

    Features:
      - thread-safe register/get/remove
      - optional service factories (callable) for lazy instantiation
      - lifecycle: start_services(), stop_services()
      - health checks exposure via .health()
      - ability to list services and metadata
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # storage maps name -> (provider, instance_or_None, metadata)
        # provider can be:
        #   - a concrete instance
        #   - a callable factory: factory(app_context) -> instance
        self._services: Dict[str, Tuple[Callable[[], Any] | Any, Any, Dict[str, Any]]] = {}

    # ---- Registration API ----
    def register(self, name: str, service: Any | Callable[[], Any], metadata: Optional[Dict[str, Any]] = None, replace: bool = False) -> None:
        """
        Register a service.
        - name: unique name
        - service: instance or zero-arg factory (or callable that accepts app_context)
        - metadata: optional dict for discovery
        - replace: if True, will replace existing registration
        """
        metadata = metadata or {}
        with self._lock:
            if name in self._services and not replace:
                raise KeyError(f"Service '{name}' already registered")
            # store (provider, instance(None if factory), metadata)
            instance = service if not callable(service) else None
            self._services[name] = (service, instance, dict(metadata))
            logger.debug("Registered service %s (callable=%s)", name, callable(service))

    def unregister(self, name: str) -> None:
        with self._lock:
            if name in self._services:
                del self._services[name]
                logger.debug("Unregistered service %s", name)
            else:
                raise ServiceNotRegistered(name)

    # ---- Retrieval API ----
    def get(self, name: str, default: Any = None) -> Any:
        """
        Get a service by name.
        If the registered provider is a factory/callable, instantiate on first access and cache the instance.
        If not registered and default is provided, return default; otherwise raise KeyError.
        """
        with self._lock:
            if name not in self._services:
                if default is not None:
                    return default
                raise ServiceNotRegistered(f"AppContext: service '{name}' not registered")
            provider, instance, metadata = self._services[name]
            # if instance already materialized, return
            if instance is not None:
                return instance
            # else provider might be a factory or a concrete instance
            if callable(provider):
                # pass self if factory accepts argument, else call without args
                try:
                    # prefer factory(self) if it accepts an argument
                    import inspect

                    sig = inspect.signature(provider)
                    if len(sig.parameters) == 0:
                        instance = provider()
                    else:
                        instance = provider(self)
                except Exception:
                    # fallback: attempt to call provider with no args
                    instance = provider()
                # cache
                self._services[name] = (provider, instance, metadata)
                logger.debug("AppContext: instantiated lazy service %s", name)
                return instance
            else:
                # provider is concrete instance, cache and return
                self._services[name] = (provider, provider, metadata)
                return provider

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._services

    def list_services(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                name: {"materialized": (instance is not None), "metadata": dict(metadata)}
                for name, (_, instance, metadata) in self._services.items()
            }

    # ---- Lifecycle helpers ----
    def start_services(self) -> None:
        """
        Call 'start()' on services that provide it (safely).
        """
        with self._lock:
            for name in list(self._services.keys()):
                provider, instance, metadata = self._services[name]
                try:
                    # ensure instance materialized
                    svc = self.get(name)
                    if hasattr(svc, "start") and callable(svc.start):
                        try:
                            svc.start()
                            logger.debug("Started service %s", name)
                        except Exception:
                            logger.exception("Error starting service %s", name)
                except Exception:
                    logger.exception("Failed materializing service %s during start", name)

    def stop_services(self) -> None:
        """
        Call 'stop()' on services that provide it (safely).
        """
        with self._lock:
            # iterate in reverse registration order for safety
            for name in list(self._services.keys())[::-1]:
                provider, instance, metadata = self._services[name]
                try:
                    svc = self.get(name)
                    if hasattr(svc, "stop") and callable(svc.stop):
                        try:
                            svc.stop()
                            logger.debug("Stopped service %s", name)
                        except Exception:
                            logger.exception("Error stopping service %s", name)
                except Exception:
                    logger.exception("Failed materializing service %s during stop", name)

    # ---- Health / diagnostics ----
    def health(self) -> Dict[str, Any]:
        """
        Return a health snapshot of the app context and its services.
        Each service that exposes 'health()' will be called.
        """
        out = {"services": {}}
        with self._lock:
            for name, (provider, instance, metadata) in self._services.items():
                try:
                    svc = self.get(name)
                    svc_health = {"materialized": True}
                    if hasattr(svc, "health") and callable(svc.health):
                        try:
                            svc_health["detail"] = svc.health()
                        except Exception:
                            svc_health["detail"] = {"error": "health check failed"}
                    out["services"][name] = svc_health
                except ServiceNotRegistered:
                    out["services"][name] = {"materialized": False}
                except Exception:
                    out["services"][name] = {"materialized": False, "error": "failed to get service"}
        return out

    # Context manager helper
    @contextlib.contextmanager
    def provide(self, **services: Any):
        """
        Temporarily register provided services for the context manager scope.
        After exiting, the original registrations are restored.
        Usage:
            with app.provide(llm_client=my_llm):
                ... inside context ...
        """
        with self._lock:
            original = {}
            for name, svc in services.items():
                if name in self._services:
                    original[name] = self._services[name]
                self.register(name, svc, replace=True)
            try:
                yield
            finally:
                # restore originals (or unregister newly added)
                for name in services.keys():
                    if name in original:
                        self._services[name] = original[name]
                    else:
                        if name in self._services:
                            del self._services[name]


# Convenience singleton for simple scripts (optional)
_default_app_context: Optional[AppContext] = None


def get_global_app() -> AppContext:
    global _default_app_context
    if _default_app_context is None:
        _default_app_context = AppContext()
    return _default_app_context
