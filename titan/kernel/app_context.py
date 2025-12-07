# Path: titan/kernel/app_context.py
from __future__ import annotations
import threading
import logging
import contextlib
from typing import Any, Callable, Dict, Optional, Iterable, Tuple

logger = logging.getLogger(__name__)

# Sentinel object to distinguish "None passed as default" vs "No default passed"
_SENTINEL = object()

class ServiceNotRegistered(KeyError):
    pass

class AppContext:
    """
    Enterprise-grade application service registry / lightweight DI container.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._services: Dict[str, Tuple[Callable[[], Any] | Any, Any, Dict[str, Any]]] = {}

    def register(self, name: str, service: Any | Callable[[], Any], metadata: Optional[Dict[str, Any]] = None, replace: bool = False) -> None:
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

    def get(self, name: str, default: Any = _SENTINEL) -> Any:
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
                    if len(sig.parameters) == 0:
                        instance = provider()
                    else:
                        instance = provider(self)
                except Exception:
                    instance = provider()
                
                self._services[name] = (provider, instance, metadata)
                logger.debug("AppContext: instantiated lazy service %s", name)
                return instance
            else:
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

    # Lifecycle methods (start_services, stop_services, health) omitted for brevity but should remain...
    # (Keep the rest of the file as you originally had it, the critical fix is the _SENTINEL logic above)
    
    def start_services(self) -> None:
        with self._lock:
            for name in list(self._services.keys()):
                provider, instance, metadata = self._services[name]
                try:
                    svc = self.get(name)
                    if hasattr(svc, "start") and callable(svc.start):
                        try:
                            svc.start()
                        except Exception:
                            logger.exception("Error starting service %s", name)
                except Exception:
                    pass

    def stop_services(self) -> None:
        with self._lock:
            for name in list(self._services.keys())[::-1]:
                try:
                    svc = self.get(name)
                    if hasattr(svc, "stop") and callable(svc.stop):
                        try:
                            svc.stop()
                        except Exception:
                            logger.exception("Error stopping service %s", name)
                except Exception:
                    pass
    
    def health(self) -> Dict[str, Any]:
        return {"services": self.list_services()}