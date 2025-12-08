# titan/perception/bridges/event_bridge.py
from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

class EventBridge:
    """
    Adapter between perception sensors and the TITAN kernel's EventBus and ContextStore.

    - Uses your EventBus.publish(event_type: str, payload: dict, block=False)
    - Uses ContextStore.set/patch where available
    - Performs policy checks via policy_engine.allow_event_async or allow_action_async/allow_action
    - Emits fine-grained event types in Option C: "perception.<event_type>" (e.g. "perception.key_press")
    """

    def __init__(self, event_bus: Optional[Any] = None, context_store: Optional[Any] = None, policy_engine: Optional[Any] = None):
        self.event_bus = event_bus
        self.context_store = context_store
        self.policy_engine = policy_engine

    async def publish(self, event: Dict[str, Any]) -> None:
        """
        Publish a perception event into the kernel.
        The sensors should set event['type'] to a specific value like 'key_press', 'mouse_click', 'transcript', etc.
        This publishes event_type = f"perception.{event['type']}" to the EventBus.
        """
        try:
            event.setdefault("ts", asyncio.get_event_loop().time())
            # canonical event name (sensor-provided)
            evt_type = event.get("type") or event.get("event_type") or "perception.event"
            # final bus event type (Option C)
            bus_event_type = f"perception.{evt_type}"

            # Policy check: prefer allow_event_async
            if self.policy_engine:
                try:
                    if hasattr(self.policy_engine, "allow_event_async") and asyncio.iscoroutinefunction(self.policy_engine.allow_event_async):
                        allowed, reason = await self.policy_engine.allow_event_async(actor=event.get("user_id", "system"), trust=event.get("trust_level", "low"), event=event)
                        if not allowed:
                            logger.info("EventBridge: policy denied event %s reason=%s", bus_event_type, reason)
                            return
                    else:
                        # fallback to allow_action_async or allow_action
                        fn_async = getattr(self.policy_engine, "allow_action_async", None)
                        if fn_async and asyncio.iscoroutinefunction(fn_async):
                            allowed, reason = await fn_async(actor=event.get("user_id", "system"), trust_level=event.get("trust_level", "low"), action="perception.event", resource={"event_type": evt_type, "payload": event})
                            if not allowed:
                                logger.info("EventBridge: policy denied event %s reason=%s", bus_event_type, reason)
                                return
                        else:
                            fn_sync = getattr(self.policy_engine, "allow_action", None)
                            if fn_sync:
                                loop = asyncio.get_event_loop()
                                allowed, reason = await loop.run_in_executor(None, lambda: fn_sync(event.get("user_id", "system"), event.get("trust_level", "low"), "perception.event", {"event_type": evt_type, "payload": event}))
                                if not allowed:
                                    logger.info("EventBridge: policy denied event %s reason=%s", bus_event_type, reason)
                                    return
                except Exception:
                    logger.exception("EventBridge: policy check error; allowing event by default")

            # Update ContextStore minimally (non-blocking)
            try:
                if self.context_store:
                    try:
                        # Attempt to set last_perception_event
                        self.context_store.set("last_perception_event", {"type": evt_type, "ts": event.get("ts")})
                    except Exception:
                        try:
                            self.context_store.patch({"last_perception_event": {"type": evt_type, "ts": event.get("ts")}})
                        except Exception:
                            logger.debug("EventBridge: context_store.set/patch failed (non-fatal)")
            except Exception:
                logger.exception("EventBridge: context_store interaction failed")

            # Publish to EventBus (non-blocking)
            if self.event_bus and hasattr(self.event_bus, "publish"):
                try:
                    # event_bus.publish(event_type, payload, block=False)
                    self.event_bus.publish(bus_event_type, event, block=False)
                except Exception:
                    logger.exception("EventBridge: event_bus.publish raised")
            else:
                logger.info("EventBridge fallback log: %s %s", bus_event_type, {k: v for k, v in event.items() if k != "raw_audio"})

        except Exception:
            logger.exception("EventBridge.publish failed")

    def sync_publish(self, event: Dict[str, Any]):
        """Synchronous helper for sensors that cannot call async code"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self.publish(event), loop)
            else:
                asyncio.run(self.publish(event))
        except Exception:
            logger.exception("EventBridge.sync_publish failed")
