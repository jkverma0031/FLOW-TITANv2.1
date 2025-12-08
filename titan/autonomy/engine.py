# titan/autonomy/engine.py
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Dict, Any, Callable, List

from .config import AutonomyConfig
from .intent_classifier import IntentClassifier
from .decision_policy import DecisionPolicy

logger = logging.getLogger(__name__)

class AutonomyEngine:
    """
    Full autonomous cognition loop (production-grade, extension friendly).

    Usage:
      engine = AutonomyEngine(app, config=AutonomyConfig())
      await engine.start()
      await engine.stop()

    The engine discovers kernel components from `app`, which is your application context
    (object exposing .get(key) and .register(key, value) — compatible with your startup wiring).
    """

    def __init__(self, app: Any, config: Optional[AutonomyConfig] = None):
        self.app = app
        self.config = config or AutonomyConfig()
        # kernel services (discovered lazily)
        self.event_bus = app.get("event_bus", None)
        self.context_store = app.get("context_store", None)
        self.episodic_store = app.get("episodic_store", None)
        self.provider_router = app.get("llm_provider_router", None) or app.get("provider_router", None)
        self.policy_engine = app.get("policy_engine", None) or app.get("negotiator", None)
        self.parser_adapter = app.get("parser_adapter", None)
        self.llm_dsl_generator = app.get("llm_dsl_generator", None)
        self.orchestrator = app.get("orchestrator", None)
        self.worker_pool = app.get("worker_pool", None)

        self.intent_classifier = IntentClassifier(provider_router=self.provider_router, config=self.config)
        self.decision_policy = DecisionPolicy(policy_engine=self.policy_engine, config=self.config)

        # internal state
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=self.config.event_queue_size)
        self._consumer_tasks: List[asyncio.Task] = []
        self._running = False

        # subscription handles (if needed)
        self._subscribed_event_types: List[str] = []
        self._known_perception_event_types = [
            "key_press", "key_release",
            "mouse_move", "mouse_click", "mouse_scroll",
            "active_window", "notification",
            "transcript", "wakeword_detected"
        ]

    # -------------------------
    # Subscription helpers
    # -------------------------
    def _subscribe_to_events(self):
        """
        Subscribe to perception events on the EventBus. We attempt multiple strategies
        for compatibility:
         - If EventBus supports wildcard subscribe('perception.*'), use it.
         - Else try subscribing to each common perception.<type> event.
         - Else try subscribe('perception'), subscribe('perception.event').
        """
        if not self.event_bus:
            logger.warning("AutonomyEngine: no event_bus available; perception will not be consumed")
            return

        subscribe = getattr(self.event_bus, "subscribe", None)
        if not subscribe:
            logger.warning("AutonomyEngine: event_bus has no subscribe method")
            return

        # Attempt wildcard subscription first (some implementations support)
        try:
            subscribe("perception.*", self._on_event)
            self._subscribed_event_types.append("perception.*")
            logger.info("AutonomyEngine: subscribed to perception.* (wildcard)")
            return
        except Exception:
            logger.debug("AutonomyEngine: wildcard subscription 'perception.*' not supported (continuing)")

        # Try common specific event types
        try:
            for t in self._known_perception_event_types:
                et = f"perception.{t}"
                subscribe(et, self._on_event)
                self._subscribed_event_types.append(et)
            logger.info("AutonomyEngine: subscribed to specific perception events")
            return
        except Exception:
            logger.debug("AutonomyEngine: specific event subscription failed; trying generic options")

        # fallback subscribe
        try:
            subscribe("perception", self._on_event)
            self._subscribed_event_types.append("perception")
            logger.info("AutonomyEngine: subscribed to 'perception' (fallback)")
            return
        except Exception:
            logger.warning("AutonomyEngine: failed to subscribe to EventBus; autonomy will not receive events")

    # -------------------------
    # Event handler (entrypoint)
    # -------------------------
    def _on_event(self, payload: Dict[str, Any]):
        """
        Called by EventBus when a perception event arrives.
        We push it into the local asyncio queue for processing by workers.
        """
        try:
            # normalize minimal metadata
            payload.setdefault("received_at", time.time())
            # drop if too old
            age = time.time() - float(payload.get("ts", payload.get("received_at", time.time())))
            if age > self.config.max_event_age_seconds:
                logger.debug("AutonomyEngine: dropping stale event age=%.2fs", age)
                return

            # enqueue (non-blocking best-effort)
            try:
                self._event_queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("AutonomyEngine: event queue full; dropping event")
        except Exception:
            logger.exception("AutonomyEngine._on_event failed")

    # -------------------------
    # Event processor
    # -------------------------
    async def _event_worker(self, worker_id: int):
        logger.info("AutonomyEngine: event_worker[%d] started", worker_id)
        while self._running:
            try:
                event = await self._event_queue.get()
                try:
                    await self._process_event(event)
                except Exception:
                    logger.exception("AutonomyEngine: processing event failed")
                finally:
                    self._event_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("AutonomyEngine: worker runtime error")
                await asyncio.sleep(0.5)
        logger.info("AutonomyEngine: event_worker[%d] stopped", worker_id)

    # -------------------------
    # Core event processing pipeline
    # -------------------------
    async def _process_event(self, event: Dict[str, Any]):
        """
        Steps:
        1) If it's a transcript -> classify intent via IntentClassifier
        2) Evaluate decision policy
        3) If decision == 'do' -> create plan via Planner and dispatch to Orchestrator
        4) If decision == 'ask' -> publish a 'ui.prompt' event (or 'autonomy.ask_user') so UI can ask the human
        5) Log episode + store in episodic_store
        """
        event_ts = event.get("ts", time.time())
        logger.debug("AutonomyEngine._process_event handling event type=%s sensor=%s", event.get("type"), event.get("sensor"))

        # 0. Short-circuit: ignore internal autonomy events
        if event.get("source") == "autonomy":
            return

        # compose context snapshot
        context_snapshot = {}
        try:
            if self.context_store:
                # attempt to read a small context (non-blocking)
                getter = getattr(self.context_store, "get", None)
                if getter:
                    try:
                        context_snapshot = getter("session_context") or {}
                    except Exception:
                        # fallback: try patch/read
                        context_snapshot = {}
        except Exception:
            logger.debug("AutonomyEngine: context snapshot failed", exc_info=True)

        # 1) classify intent for transcripts and notifications; for other events create synthetic intent
        intent = {"intent": "noop", "confidence": 0.0, "params": {}}
        raw_input_text = None
        if event.get("type") in ("transcript", "notification", "wakeword_detected"):
            # get the textual payload
            raw_input_text = event.get("text") or (event.get("payload") and event["payload"].get("body")) or event.get("payload") or ""
            try:
                intent = await self.intent_classifier.classify_async({"event": event, "text": raw_input_text}, context_snapshot)
            except Exception:
                logger.exception("AutonomyEngine: intent classification failed; using fallback")
                intent = {"intent": "noop", "confidence": 0.0, "params": {}}
        else:
            # heuristics for UI events / active_window
            if event.get("type") == "active_window":
                title = (event.get("window") or {}).get("title", "")
                if title:
                    intent = {"intent": "context_change", "confidence": 0.6, "params": {"title": title}}
            elif event.get("type", "").startswith("mouse_") or event.get("type", "").startswith("key_"):
                intent = {"intent": "user_activity", "confidence": 0.9, "params": {"type": event.get("type")}}
            else:
                intent = {"intent": "noop", "confidence": 0.0, "params": {}}

        # 2) decision policy
        policy_decision = {"decision": "ignore", "reason": "default", "confidence": 0.0}
        try:
            actor = event.get("user_id", "system")
            trust_level = (event.get("trust_level") or "low")
            policy_decision = await self.decision_policy.evaluate(actor=actor, trust_level=trust_level, intent=intent, event=event)
        except Exception:
            logger.exception("AutonomyEngine: decision policy evaluation failed; default ignore")
            policy_decision = {"decision": "ignore", "reason": "policy_error", "confidence": 0.0}

        # 3) act based on decision
        if policy_decision.get("decision") == "ignore":
            await self._record_episode(event, intent, policy_decision, outcome={"status": "ignored"})
            return

        if policy_decision.get("decision") == "ask":
            # publish an autonomy.request_confirmation event for the UI
            try:
                ask_payload = {
                    "source": "autonomy",
                    "type": "ask_user_confirmation",
                    "event": event,
                    "intent": intent,
                    "policy": policy_decision,
                    "ts": time.time()
                }
                # EventBus publish (use event_bus if available)
                if self.event_bus and hasattr(self.event_bus, "publish"):
                    try:
                        # topic: autonomy.ask_user_confirmation (or perception.autonomy.*)
                        self.event_bus.publish("autonomy.ask_user_confirmation", ask_payload, block=False)
                    except Exception:
                        logger.exception("AutonomyEngine: event_bus.publish failed for ask_user_confirmation")
                else:
                    logger.info("AutonomyEngine ASK: %s", ask_payload)
            except Exception:
                logger.exception("AutonomyEngine: failed to publish ask_user_confirmation")
            await self._record_episode(event, intent, policy_decision, outcome={"status": "ask"})
            return

        if policy_decision.get("decision") == "do":
            # 4) generate plan via Planner (try parser_adapter then llm_dsl_generator)
            plan = None
            plan_raw = None
            try:
                planner_kwargs = {"max_tokens": getattr(self.config, "planner_max_tokens", 512), "temperature": getattr(self.config, "planner_temperature", 0.0)}
                # create a short human-friendly instruction for planning
                prompt = self._build_planning_prompt(event=event, intent=intent, context=context_snapshot)
                # try parser_adapter
                if self.parser_adapter and hasattr(self.parser_adapter, "generate_plan") and callable(self.parser_adapter.generate_plan):
                    plan = await _await_maybe(self.parser_adapter.generate_plan(prompt, **planner_kwargs))
                elif self.llm_dsl_generator and hasattr(self.llm_dsl_generator, "generate_dsl_async"):
                    res = await self.llm_dsl_generator.generate_dsl_async(prompt, **planner_kwargs)
                    plan = res.get("dsl")
                    plan_raw = res.get("raw")
                else:
                    # last fallback: try provider to get a small plan textual steps, then wrap heuristically
                    logger.warning("AutonomyEngine: no planner found; cannot create plan")
                    plan = {"nodes": []}
            except Exception:
                logger.exception("AutonomyEngine: planner failed")
                plan = {"nodes": []}

            # 5) dispatch plan to orchestrator
            outcome = {"status": "failed", "reason": None}
            try:
                if not plan or (isinstance(plan, dict) and not plan.get("nodes")):
                    outcome = {"status": "no_plan"}
                    await self._record_episode(event, intent, policy_decision, outcome)
                    return

                # Orchestrator usually exposes run_plan/execute_plan/execute methods; attempt common names.
                orch = self.orchestrator or self.app.get("orchestrator", None)
                executed = False
                if orch:
                    try:
                        if hasattr(orch, "execute_plan"):
                            res = await _await_maybe(orch.execute_plan(plan, actor=actor, timeout=self.config.execution_timeout_seconds))
                            executed = True
                            outcome = {"status": "dispatched", "result": res}
                        elif hasattr(orch, "run"):
                            res = await _await_maybe(orch.run(plan, actor=actor, timeout=self.config.execution_timeout_seconds))
                            executed = True
                            outcome = {"status": "dispatched", "result": res}
                        elif hasattr(orch, "execute"):
                            res = await _await_maybe(orch.execute(plan, actor=actor, timeout=self.config.execution_timeout_seconds))
                            executed = True
                            outcome = {"status": "dispatched", "result": res}
                        else:
                            # fallback: push to worker_pool directly
                            wp = self.worker_pool or self.app.get("worker_pool", None)
                            if wp and hasattr(wp, "submit"):
                                wp.submit(lambda: orch)  # a placeholder; implementer should map correctly
                                executed = True
                                outcome = {"status": "queued_to_worker_pool"}
                    except Exception:
                        logger.exception("AutonomyEngine: orchestrator dispatch failed")
                        outcome = {"status": "dispatch_error"}
                else:
                    logger.warning("AutonomyEngine: no orchestrator available; cannot execute plan")
                    outcome = {"status": "no_orchestrator"}
            except Exception:
                logger.exception("AutonomyEngine: dispatch error")
                outcome = {"status": "dispatch_exception"}

            await self._record_episode(event, intent, policy_decision, outcome)
            return

    def _build_planning_prompt(self, *, event: Dict[str, Any], intent: Dict[str, Any], context: Dict[str, Any]) -> str:
        """
        Build a short instruction for the DSL generator / planner.
        Keep it deterministic and include intent and top-level context.
        """
        try:
            lines = []
            lines.append("You are TITAN's autonomous planner. Create a JSON DSL plan for the given user intent.")
            lines.append("Intent:")
            lines.append(str(intent))
            lines.append("Event:")
            lines.append(str(event))
            if context:
                lines.append("Context snapshot:")
                lines.append(str(context))
            lines.append("Return a single JSON object describing nodes and metadata for execution. Keep nodes explicit.")
            return "\n\n".join(lines)
        except Exception:
            return f"Intent: {intent}\nEvent: {event}"

    # -------------------------
    # Episode logging
    # -------------------------
    async def _record_episode(self, event: Dict[str, Any], intent: Dict[str, Any], policy_decision: Dict[str, Any], outcome: Dict[str, Any]):
        """
        Store an episodic record (best-effort). Uses episodic_store if available, else logs.
        """
        try:
            record = {
                "ts": time.time(),
                "event": event,
                "intent": intent,
                "policy": policy_decision,
                "outcome": outcome,
            }
            # episodic_store API may be `write(record)` or `append(record)`
            if self.episodic_store:
                try:
                    if hasattr(self.episodic_store, "append"):
                        self.episodic_store.append(record)
                    elif hasattr(self.episodic_store, "write"):
                        self.episodic_store.write(record)
                    else:
                        # fallback to storing in context_store as last_episode
                        if self.context_store and hasattr(self.context_store, "set"):
                            self.context_store.set("last_episode", record)
                except Exception:
                    logger.exception("AutonomyEngine: episodic_store write failed")
            else:
                # fallback: update context_store
                if self.context_store and hasattr(self.context_store, "set"):
                    try:
                        self.context_store.set("last_episode", record)
                    except Exception:
                        logger.exception("AutonomyEngine: context_store set last_episode failed")
                else:
                    logger.info("AutonomyEngine episode: %s", record)
        except Exception:
            logger.exception("AutonomyEngine._record_episode failed")

    # -------------------------
    # Lifecycle
    # -------------------------
    async def start(self):
        if self._running:
            return
        self._running = True
        # (re-)discover kernel components at start time
        self.event_bus = self.event_bus or self.app.get("event_bus", None)
        self.context_store = self.context_store or self.app.get("context_store", None)
        self.episodic_store = self.episodic_store or self.app.get("episodic_store", None)
        self.parser_adapter = self.parser_adapter or self.app.get("parser_adapter", None)
        self.llm_dsl_generator = self.llm_dsl_generator or self.app.get("llm_dsl_generator", None)
        self.orchestrator = self.orchestrator or self.app.get("orchestrator", None)
        self.worker_pool = self.worker_pool or self.app.get("worker_pool", None)

        # subscribe to perception events
        self._subscribe_to_events()

        # start worker tasks
        for i in range(max(1, self.config.event_processing_concurrency)):
            t = asyncio.create_task(self._event_worker(i))
            self._consumer_tasks.append(t)
        logger.info("AutonomyEngine started with %d workers", len(self._consumer_tasks))

    async def stop(self):
        if not self._running:
            return
        self._running = False
        # cancel consumer tasks
        for t in list(self._consumer_tasks):
            try:
                t.cancel()
                await t
            except Exception:
                pass
        self._consumer_tasks.clear()
        # unsubscribe if possible
        try:
            if self.event_bus and hasattr(self.event_bus, "unsubscribe"):
                for et in self._subscribed_event_types:
                    try:
                        self.event_bus.unsubscribe(et, self._on_event)
                    except Exception:
                        pass
        except Exception:
            logger.exception("AutonomyEngine: unsubscribe failed")
        logger.info("AutonomyEngine stopped")

    async def health(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "queue_size": self._event_queue.qsize(),
            "workers": len(self._consumer_tasks),
        }


# -------------------------
# Helpers
# -------------------------
async def _await_maybe(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
