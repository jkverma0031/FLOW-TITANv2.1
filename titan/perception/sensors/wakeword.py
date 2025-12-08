# titan/perception/sensors/wakeword.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Callable, Any

from .base import BaseSensor

logger = logging.getLogger(__name__)

class WakewordSensor(BaseSensor):
    """
    Lightweight keyword detector (string match). For production integrate Porcupine/Picovoice.
    Emits Option C event: perception.wakeword_detected
    """

    name = "wakeword"
    version = "0.1.0"

    def __init__(self, keyword: str = "hey titan", sensitivity: float = 0.5, *, loop=None):
        super().__init__(name="wakeword", loop=loop)
        self.keyword = keyword.lower()
        self.sensitivity = sensitivity
        self._callback = None

    def set_wake_callback(self, cb: Callable[[], Any]):
        self._callback = cb

    async def start(self):
        self._running = True
        self._health = {"status": "running"}
        logger.info("WakewordSensor running (keyword=%s)", self.keyword)

    async def stop(self):
        self._running = False
        self._health = {"status": "stopped"}

    async def process_transcript(self, transcript: str):
        if not self._running:
            return
        try:
            if self.keyword in transcript.lower():
                evt = {"sensor": "wakeword", "type": "wakeword_detected", "keyword": self.keyword, "ts": asyncio.get_event_loop().time()}
                self._emit(evt)
                if self._callback:
                    res = self._callback()
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
        except Exception:
            logger.exception("WakewordSensor processing failed")

    def get_manifest(self):
        m = super().get_manifest()
        m["events"] = [{"name": "wakeword_detected", "description": "Wake-word detected", "schema": {"keyword":"string"}}]
        return m
