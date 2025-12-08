# titan/perception/sensors/mouse.py
from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional

from .base import BaseSensor

try:
    from pynput import mouse as _pynput_mouse
    _PYNPUT_MOUSE = True
except Exception:
    _PYNPUT_MOUSE = False

logger = logging.getLogger(__name__)

class MouseSensor(BaseSensor):
    name = "mouse"
    version = "1.0.0"

    def __init__(self, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__(name="mouse", loop=loop)
        self._listener = None

    async def start(self):
        if not _PYNPUT_MOUSE:
            logger.warning("pynput.mouse not available; MouseSensor disabled")
            self._health = {"status": "disabled", "reason": "pynput_missing"}
            return

        def on_move(x, y):
            event = {"sensor": "mouse", "type": "mouse_move", "x": x, "y": y, "ts": asyncio.get_event_loop().time()}
            self._emit(event)

        def on_click(x, y, button, pressed):
            event = {"sensor": "mouse", "type": "mouse_click", "x": x, "y": y, "button": str(button), "pressed": bool(pressed), "ts": asyncio.get_event_loop().time()}
            self._emit(event)

        def on_scroll(x, y, dx, dy):
            event = {"sensor": "mouse", "type": "mouse_scroll", "x": x, "y": y, "dx": dx, "dy": dy, "ts": asyncio.get_event_loop().time()}
            self._emit(event)

        self._listener = _pynput_mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
        self._listener.start()
        self._running = True
        self._health = {"status": "running"}
        logger.info("MouseSensor running")

    async def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                logger.exception("Failed to stop mouse listener")
        self._running = False
        self._health = {"status": "stopped"}

    def get_manifest(self):
        m = super().get_manifest()
        m["events"] = [
            {"name": "mouse_move", "schema": {"x": "float", "y":"float"}},
            {"name": "mouse_click", "schema": {"x": "float", "y":"float", "button":"string", "pressed":"bool"}},
            {"name": "mouse_scroll", "schema": {"dx": "float", "dy":"float"}},
        ]
        return m
