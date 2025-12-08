# titan/perception/sensors/notifications.py
from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional

from .base import BaseSensor

logger = logging.getLogger(__name__)

class NotificationsSensor(BaseSensor):
    name = "notifications"
    version = "1.0.0"

    def __init__(self, poll_interval: float = 1.0, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__(name="notifications", loop=loop)
        self.poll_interval = poll_interval
        self._task = None
        self._last = set()

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll_notifications())
        self._health = {"status": "running"}

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        self._health = {"status": "stopped"}

    async def _poll_notifications(self):
        """
        Best-effort cross-platform notification listener. On Linux/macOS you can hook into DBus/NSUserNotification;
        here we provide a polling fallback where supported.
        """
        while self._running:
            try:
                n = await self._get_system_notifications()
                for item in n:
                    key = f"{item.get('app')}:{item.get('title')}:{item.get('ts')}"
                    if key not in self._last:
                        self._last.add(key)
                        # Option C event type
                        event = {"sensor": "notifications", "type": "notification", "payload": item, "ts": asyncio.get_event_loop().time()}
                        self._emit(event)
                # prune last set
                if len(self._last) > 1000:
                    self._last = set(list(self._last)[-500:])
            except Exception:
                logger.exception("NotificationsSensor poll error")
            await asyncio.sleep(self.poll_interval)

    async def _get_system_notifications(self):
        # placeholder; return empty list if no integration available
        try:
            import dbus  # optional; platform-specific
            return []
        except Exception:
            return []

    def get_manifest(self):
        m = super().get_manifest()
        m["events"] = [{"name": "notification", "description": "System notifications", "schema": {"app":"string","title":"string","body":"string"}}]
        return m
