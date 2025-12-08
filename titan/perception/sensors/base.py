# titan/perception/sensors/base.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)

class SensorError(Exception):
    pass

class BaseSensor:
    """
    Async-first base sensor contract.
    - start()/stop() are async methods
    - emits events via callback provided at registration
    - provides get_manifest() for Planner introspection
    """

    name: str = "base"
    version: str = "0.0.1"

    def __init__(self, *, name: Optional[str] = None, loop: Optional[asyncio.AbstractEventLoop] = None):
        if name:
            self.name = name
        self.loop = loop or asyncio.get_event_loop()
        self._running = False
        self._event_callback: Optional[Callable[[Dict[str, Any]], Any]] = None
        self._health = {"status": "starting"}

    def set_event_callback(self, cb: Callable[[Dict[str, Any]], Any]):
        """Callback signature: async def cb(event: dict) or sync callable"""
        self._event_callback = cb

    async def start(self):
        """Start sensor capture."""
        self._running = True
        self._health = {"status": "running"}
        logger.debug("%s sensor started", self.name)

    async def stop(self):
        self._running = False
        self._health = {"status": "stopped"}
        logger.debug("%s sensor stopped", self.name)

    async def health(self) -> Dict[str, Any]:
        return self._health

    def _emit(self, event: Dict[str, Any]):
        """Internal: call the callback safely (async if required)."""
        if not self._event_callback:
            return
        try:
            result = self._event_callback(event)
            if asyncio.iscoroutine(result) or asyncio.iscoroutinefunction(self._event_callback):
                # schedule it
                asyncio.create_task(result)
        except Exception:
            logger.exception("Sensor %s failed to emit event", self.name)

    def get_manifest(self) -> Dict[str, Any]:
        """Return a manifest describing events emitted by this sensor (for Planner)"""
        return {
            "name": self.name,
            "version": self.version,
            "events": []
        }
