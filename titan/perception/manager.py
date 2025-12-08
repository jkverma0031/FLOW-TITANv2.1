# titan/perception/manager.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Dict, Any, List

from .config import PerceptionConfig
from .bridges.event_bridge import EventBridge
from .sensors.keyboard import KeyboardSensor
from .sensors.mouse import MouseSensor
from .sensors.window_monitor import WindowMonitorSensor
from .sensors.notifications import NotificationsSensor
from .sensors.microphone import MicrophoneSensor
from .sensors.wakeword import WakewordSensor

logger = logging.getLogger(__name__)

class PerceptionManager:
    """
    High-level manager for perception sensors.
    - start/stop lifecycle
    - sensor registry
    - event routing into EventBridge (and ContextStore)
    - health checks
    """

    def __init__(self, config: Optional[PerceptionConfig] = None, event_bridge: Optional[EventBridge] = None):
        self.config = config or PerceptionConfig()
        self.event_bridge = event_bridge or EventBridge()
        self.loop = asyncio.get_event_loop()
        self._sensors: Dict[str, Any] = {}
        self._running = False

    def _sensor_event_cb(self, event: Dict[str, Any]):
        """
        Normalize event and forward to EventBridge.publish (async).
        The EventBridge handles policy and ContextStore integration.
        """
        try:
            # Attach canonical fields if missing
            event.setdefault("source", event.get("sensor"))
            # Ensure sensor exists
            if "sensor" not in event:
                event["sensor"] = "unknown"
            # event already contains 'type' defined by each sensor using Option C naming
            # forward to bridge
            # EventBridge.publish is async — schedule it
            coro = self.event_bridge.publish(event)
            if asyncio.iscoroutine(coro):
                asyncio.create_task(coro)
            else:
                # If sync, call in executor
                asyncio.get_event_loop().run_in_executor(None, lambda: coro)
        except Exception:
            logger.exception("PerceptionManager.sensor_event_cb failed")

    async def start(self):
        logger.info("PerceptionManager starting sensors")
        cfg = self.config

        # Keyboard
        if cfg.enable_keyboard:
            k = KeyboardSensor(loop=self.loop)
            k.set_event_callback(self._sensor_event_cb)
            await k.start()
            self._sensors["keyboard"] = k

        # Mouse
        if cfg.enable_mouse:
            m = MouseSensor(loop=self.loop)
            m.set_event_callback(self._sensor_event_cb)
            await m.start()
            self._sensors["mouse"] = m

        # Window monitor
        if cfg.enable_window_monitor:
            w = WindowMonitorSensor(loop=self.loop)
            w.set_event_callback(self._sensor_event_cb)
            await w.start()
            self._sensors["window_monitor"] = w

        # Notifications
        if cfg.enable_notifications:
            n = NotificationsSensor(loop=self.loop)
            n.set_event_callback(self._sensor_event_cb)
            await n.start()
            self._sensors["notifications"] = n

        # Microphone (+ optional wakeword wiring)
        if cfg.enable_microphone:
            mic = MicrophoneSensor(sample_rate=cfg.sample_rate, channels=cfg.channels, chunk_ms=cfg.chunk_ms, vad_mode=cfg.vad_mode, stt_backend=cfg.stt_backend, stt_model_path=cfg.stt_model_path, loop=self.loop)
            mic.set_event_callback(self._sensor_event_cb)
            await mic.start()
            self._sensors["microphone"] = mic

            # Optional wakeword — if enabled, create wakeword sensor and wire transcripts
            if cfg.enable_wakeword:
                ww = WakewordSensor(keyword="hey titan", loop=self.loop)
                ww.set_event_callback(self._sensor_event_cb)
                async def on_transcript(text, meta):
                    await ww.process_transcript(text)
                # set microphone transcript callback to forward to wakeword processing
                mic.set_transcript_callback(lambda txt, meta: asyncio.create_task(on_transcript(txt, meta)))
                await ww.start()
                self._sensors["wakeword"] = ww

        self._running = True
        logger.info("PerceptionManager started with sensors: %s", list(self._sensors.keys()))

    async def stop(self):
        logger.info("PerceptionManager stopping sensors")
        for name, s in list(self._sensors.items()):
            try:
                await s.stop()
            except Exception:
                logger.exception("Failed to stop sensor %s", name)
        self._sensors.clear()
        self._running = False
        logger.info("PerceptionManager stopped")

    def list_sensors(self) -> List[str]:
        return list(self._sensors.keys())

    async def health(self) -> Dict[str, Any]:
        out = {}
        for name, s in self._sensors.items():
            try:
                out[name] = await s.health()
            except Exception:
                out[name] = {"status": "error"}
        return out
