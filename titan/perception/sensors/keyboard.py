# titan/perception/sensors/keyboard.py
from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional

from .base import BaseSensor

try:
    from pynput import keyboard as _pynput_keyboard
    _PYNPUT_AVAILABLE = True
except Exception:
    _PYNPUT_AVAILABLE = False

logger = logging.getLogger(__name__)

class KeyboardSensor(BaseSensor):
    name = "keyboard"
    version = "1.0.0"

    def __init__(self, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__(name="keyboard", loop=loop)
        self._listener = None

    async def start(self):
        if not _PYNPUT_AVAILABLE:
            logger.warning("pynput not available; KeyboardSensor disabled")
            self._health = {"status": "disabled", "reason": "pynput_missing"}
            return
        def on_press(key):
            try:
                k = getattr(key, "char", None) or str(key)
            except Exception:
                k = repr(key)
            # Option C event type: perception.key_press
            event = {"sensor": "keyboard", "type": "key_press", "key": k, "ts": asyncio.get_event_loop().time()}
            self._emit(event)

        def on_release(key):
            try:
                k = getattr(key, "char", None) or str(key)
            except Exception:
                k = repr(key)
            event = {"sensor": "keyboard", "type": "key_release", "key": k, "ts": asyncio.get_event_loop().time()}
            self._emit(event)

        self._listener = _pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()
        self._health = {"status": "running"}
        self._running = True
        logger.info("KeyboardSensor running")

    async def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                logger.exception("Failed to stop keyboard listener")
        self._running = False
        self._health = {"status": "stopped"}

    def get_manifest(self):
        m = super().get_manifest()
        m["events"] = [
            {"name": "key_press", "description": "Key down events", "schema": {"key": "string"}},
            {"name": "key_release", "description": "Key release", "schema": {"key": "string"}},
        ]
        return m
