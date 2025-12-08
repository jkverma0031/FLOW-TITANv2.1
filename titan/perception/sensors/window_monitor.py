# titan/perception/sensors/window_monitor.py
from __future__ import annotations
import asyncio
import logging
import platform
import subprocess
from typing import Dict, Any, Optional

from .base import BaseSensor

logger = logging.getLogger(__name__)

_HAS_WIN = False
try:
    if platform.system() == "Windows":
        import win32gui  # type: ignore
        import win32process  # type: ignore
        _HAS_WIN = True
except Exception:
    _HAS_WIN = False

class WindowMonitorSensor(BaseSensor):
    name = "window_monitor"
    version = "1.0.0"

    def __init__(self, poll_interval: float = 0.5, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        super().__init__(name="window_monitor", loop=loop)
        self.poll_interval = poll_interval
        self._task = None
        self._last_active = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll_active_window())
        self._health = {"status": "running"}
        logger.info("WindowMonitorSensor running")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        self._health = {"status": "stopped"}

    async def _poll_active_window(self):
        while self._running:
            try:
                active = await self._get_active_window()
                if active and active != self._last_active:
                    self._last_active = active
                    # Option C event type
                    event = {"sensor": "window_monitor", "type": "active_window", "window": active, "ts": asyncio.get_event_loop().time()}
                    self._emit(event)
            except Exception:
                logger.exception("WindowMonitorSensor polling error")
            await asyncio.sleep(self.poll_interval)

    async def _get_active_window(self) -> Optional[Dict[str, Any]]:
        try:
            if _HAS_WIN:
                hwnd = win32gui.GetForegroundWindow()
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                title = win32gui.GetWindowText(hwnd)
                return {"title": title, "pid": pid, "platform": "windows"}
        except Exception:
            logger.debug("WindowMonitorSensor windows path failed", exc_info=True)
        # macOS/Linux best-effort
        try:
            if platform.system() == "Darwin":
                script = 'tell application "System Events" to get name of (processes where frontmost is true)'
                out = subprocess.check_output(["osascript", "-e", script], stderr=subprocess.DEVNULL)
                title = out.decode("utf-8").strip()
                return {"title": title, "platform": "macos"}
            else:
                out = subprocess.check_output(["xdotool", "getwindowfocus", "getwindowname"], stderr=subprocess.DEVNULL)
                title = out.decode("utf-8").strip()
                return {"title": title, "platform": "linux"}
        except Exception:
            return None

    def get_manifest(self):
        m = super().get_manifest()
        m["events"] = [{"name": "active_window", "description": "Active window changed", "schema": {"title":"string","pid":"int"}}]
        return m
