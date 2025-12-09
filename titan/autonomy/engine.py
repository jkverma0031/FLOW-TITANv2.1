# titan/autonomy/engine.py
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List, Callable

from .config import AutonomyConfig
from .intent_classifier import IntentClassifier
from .decision_policy import DecisionPolicy

# optional skill helpers (our skill package)
try:
    from titan.autonomy.skills.integration import attach_skill_manager_to_engine
    _HAS_SKILL_INTEGRATION = True
except Exception:
    attach_skill_manager_to_engine = None
    _HAS_SKILL_INTEGRATION = False

# optional observability
try:
    from titan.observability.metrics import metrics  # type: ignore
except Exception:
    metrics = None

try:
    from titan.observability.tracing import tracer  # type: ignore
except Exception:
    tracer = None

logger = logging.getLogger("titan.autonomy.engine")


def _now() -> float:
    return time.time()


async def _await_maybe(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


class AutonomyEngine:
    """
    Improved Autonomy Engine — forwards all perception events to Skills (Option 1),
    safely classifies intents, evaluates decision policy, and dispatches plans to
    the orchestrator. Designed to be robust against missing subsystems.
    """

    def __init__(self, app: Any, config: Optional[AutonomyConfig] = None):
        self.app = app
        self.config = config or AutonomyConfig()

        # resolve kernel services (lazy-safe references)
        self.event_bus = app.get("event_bus", None)
        self.context_store = app.get("context_store", None)
        self.episodic_store = app.get("episodic_store", None)
        self.provider_router = app.get("llm_provider_router", None) or app.get("provider_router", None)
        self.policy_engine = app.get("policy_engine", None)
        self.parser_adapter = app.get("parser_adapter", None)
        self.llm_dsl_generator = app.get("llm_dsl_generator", None)
        self.orchestrator = app.get("orchestrator", None)
        self.worker_pool = app.get("worker_pool", None)
        self.planner = app.get("planner", None)
        self.memory = app.get("memory", None)
        self.runtime_api = app.get("runtime_api", None)

        # core modules
        self.intent_classifier = IntentClassifier(provider_router=self.provider_router, config=self.config)
        self.decision_policy = DecisionPolicy(policy_engine=self.policy_engine, config=self.config)

        # event queue & workers
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=self.config.event_queue_size)
        self._consumer_tasks: List[asyncio.Task] = []
        self._running = False

        # skill manager placeholder
        self.skill_manager = None

        # perception event types for best-effort subscriptions
        self._known_perception_event_types = [
            "key_press", "key_release",
            "mouse_move", "mouse_click", "mouse_scroll",
            "active_window", "notification",
            "transcript", "wakeword_detected",
        ]
        self._subscribed_event_types: List[str] = []

        # small tuning
        self._skill_event_timeout = getattr(self.config, "skill_event_timeout_seconds", 0.5)
        self._intent_timeout = getattr(self.config, "intent_timeout_seconds", 2.0)
        self._planner_timeout = getattr(self.config, "planner_timeout_seconds", 10.0)
        self._orch_timeout = getattr(self.config, "execution_timeout_seconds", 60.0)

    # ----------------------------
    # subscription helpers
    # ----------------------------
    def _subscribe_to_events(self) -> None:
        if not self.event_bus or not hasattr(self.event_bus, "subscribe"):
            logger.debug("No event_bus or subscribe method; skipping subscriptions")
            return

        subscribe = getattr(self.event_bus, "subscribe")
        # try wildcard first
        try:
            subscribe("perception.*", self._on_event)
            self._subscribed_event_types.append("perception.*")
            logger.info("Subscribed to perception.*")
            return
        except Exception:
            logger.debug("Wildcard subscription not supported; attempting fine-grained subscribe")

        # subscribe to known keys
        for e in self._known_perception_event_types:
            topic = f"perception.{e}"
            try:
                subscribe(topic, self._on_event)
                self._subscribed_event_types.append(topic)
            except Exception:
                # best-effort: don't fail startup
                logger.debug("Failed to subscribe to %s", topic)

    # ----------------------------
    # EventBus callback (sync)
    # ----------------------------
    def _on_event(self, payload: Dict[str, Any]) -> None:
        """
        EventBus callback (runs in calling thread). Minimal work here:
         - normalize received_at
         - drop stale events
         - enqueue for async processing
         - forward to SkillManager queue non-blocking (Option 1)
        """
        try:
            payload.setdefault("received_at", _now())
            # drop stale
            age = _now() - float(payload.get("ts", payload.get("received_at", _now())))
            if age > getattr(self.config, "max_event_age_seconds", 10.0):
                logger.debug("Dropping stale event (age %.2fs)", age)
                return

            # enqueue (non-blocking)
            try:
                self._event_queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("Event queue full; dropping event")
                return

            # forward to skill manager (best-effort, thread-safe)
            try:
                sm = getattr(self, "skill_manager", None)
                if sm and hasattr(sm, "_event_queue") and hasattr(sm, "loop"):
                    try:
                        # use call_soon_threadsafe to push into skill manager queue
                        sm.loop.call_soon_threadsafe(sm._event_queue.put_nowait, payload)
                    except Exception:
                        # if that fails, fallback to schedule a coroutine
                        try:
                            asyncio.create_task(sm.handle_event(payload))
                        except Exception:
                            logger.debug("Failed to forward event to skill manager")
            except Exception:
                logger.debug("Skill forwarding failed quietly")
        except Exception:
            logger.exception("AutonomyEngine._on_event unexpected error")

    # ----------------------------
    # Event worker
    # ----------------------------
    async def _event_worker(self, wid: int) -> None:
        logger.info("AutonomyEngine worker[%d] started", wid)
        while self._running:
            try:
                event = await self._event_queue.get()
                try:
                    await self._process_event(event)
                except Exception:
                    logger.exception("Error while processing event")
                finally:
                    try:
                        self._event_queue.task_done()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Event worker runtime error")
                await asyncio.sleep(0.25)
        logger.info("AutonomyEngine worker[%d] stopped", wid)

    # ----------------------------
    # Core pipeline
    # ----------------------------
    async def _process_event(self, event: Dict[str, Any]) -> None:
        """
        Pipeline:
          1. classify intent when appropriate
          2. evaluate decision policy
          3. if 'do' -> plan + orchestrate
          4. if 'ask' -> publish ask_user_confirmation
          5. record episodic outcome
        Skills already received the raw event (Option 1).
        """

        # quick guard: ignore self-originated autonomy events
        if event.get("source") == "autonomy":
            return

        # 1) classify intent (only for textual / high-value events)
        intent = {"intent": "noop", "params": {}, "confidence": 0.0}
        try:
            if event.get("type") in ("transcript", "notification", "wakeword_detected"):
                text = event.get("text") or (event.get("payload") or {}).get("body", "") or ""
                try:
                    coro = self.intent_classifier.classify_async({"event": event, "text": text}, {})
                    intent = await asyncio.wait_for(coro, timeout=self._intent_timeout)
                except asyncio.TimeoutError:
                    logger.warning("Intent classification timeout")
                except Exception:
                    logger.exception("Intent classification failed")
            elif event.get("type") == "active_window":
                win = event.get("window") or {}
                intent = {"intent": "context_change", "params": {"title": win.get("title")}, "confidence": 0.6}
            elif str(event.get("type", "")).startswith(("mouse_", "key_")):
                intent = {"intent": "user_activity", "params": {"type": event.get("type")}, "confidence": 0.9}
        except Exception:
            logger.exception("Intent classifier top-level error")

        # 2) decision policy
        policy_decision = {"decision": "ignore", "reason": "default"}
        try:
            actor = event.get("user_id", "system")
            trust_level = event.get("trust_level", "low")
            dec_coro = self.decision_policy.evaluate(actor=actor, trust_level=trust_level, intent=intent, event=event)
            policy_decision = await _await_maybe(dec_coro)
        except Exception:
            logger.exception("Decision policy evaluation failed; defaulting to ignore")

        # 3) apply decision
        if policy_decision.get("decision") == "ignore":
            await self._record_episode(event, intent, policy_decision, {"status": "ignored"})
            return

        if policy_decision.get("decision") == "ask":
            await self._publish_ask_user(event, intent, policy_decision)
            await self._record_episode(event, intent, policy_decision, {"status": "ask"})
            return

        if policy_decision.get("decision") == "do":
            outcome = await self._handle_do_decision(event, intent, policy_decision)
            await self._record_episode(event, intent, policy_decision, outcome)
            return

    # ----------------------------
    # DO handling: planner + orchestrator
    # ----------------------------
    async def _handle_do_decision(self, event: Dict[str, Any], intent: Dict[str, Any], policy_decision: Dict[str, Any]) -> Dict[str, Any]:
        outcome = {"status": "failed"}
        # build a compact context snapshot (best-effort)
        context_snapshot = {}
        try:
            if self.context_store and hasattr(self.context_store, "get"):
                try:
                    context_snapshot = await _await_maybe(self.context_store.get("session_context"))
                except Exception:
                    context_snapshot = {}
        except Exception:
            context_snapshot = {}

        # build prompt for planner
        prompt = self._build_planning_prompt(event=event, intent=intent, context=context_snapshot)

        # 1) generate DSL / plan via parser_adapter or llm_dsl_generator or planner
        plan_obj = None
        try:
            if self.parser_adapter and hasattr(self.parser_adapter, "generate_plan"):
                gen = self.parser_adapter.generate_plan(prompt)
                plan_obj = await _await_maybe(gen)
            elif self.llm_dsl_generator and hasattr(self.llm_dsl_generator, "generate_dsl_async"):
                gen = self.llm_dsl_generator.generate_dsl_async(prompt)
                res = await asyncio.wait_for(gen, timeout=self._planner_timeout)
                # res may be {'dsl': '...'} or raw text
                plan_obj = res.get("dsl") if isinstance(res, dict) else res
            elif self.planner and hasattr(self.planner, "plan_from_dsl"):
                plan_obj = await _await_maybe(self.planner.plan_from_dsl(prompt))
            else:
                logger.warning("No planner available to produce a plan")
        except asyncio.TimeoutError:
            logger.exception("Planner timed out")
        except Exception:
            logger.exception("Planner generation failed")

        if not plan_obj:
            return {"status": "no_plan"}

        # 2) compile/convert plan via provided helper or pass-through
        try:
            # allow plan_obj to be either a DSL string or a compiled plan object
            compiled_plan = None
            if isinstance(plan_obj, str):
                # attempt to use planner/compiler hooks
                if self.planner and hasattr(self.planner, "plan_from_dsl"):
                    compiled_plan = await _await_maybe(self.planner.plan_from_dsl(plan_obj))
                else:
                    # if skill manager was provided a compiler earlier, it will call engine._wrap_plan_with_dsl (but we keep pass-through)
                    compiled_plan = await self._wrap_plan_with_dsl(plan_obj)
            else:
                compiled_plan = plan_obj
        except Exception:
            logger.exception("Plan compilation failed")
            return {"status": "compile_failed"}

        # 3) dispatch to orchestrator
        try:
            orch = self.orchestrator or self.app.get("orchestrator", None)
            if not orch:
                logger.error("No orchestrator available to execute plan")
                return {"status": "no_orchestrator"}

            # try common method names; allow sync or async
            for fn_name in ("execute_plan", "run", "execute"):
                if hasattr(orch, fn_name):
                    fn = getattr(orch, fn_name)
                    try:
                        res_coro = fn(compiled_plan, actor=event.get("user_id", "system"))
                        # await with timeout
                        res = await asyncio.wait_for(_await_maybe(res_coro), timeout=self._orch_timeout)
                        return {"status": "dispatched", "result": res}
                    except asyncio.TimeoutError:
                        logger.exception("Orchestrator timed out executing plan")
                        return {"status": "orch_timeout"}
                    except Exception:
                        logger.exception("Orchestrator.%s failed", fn_name)
                        # continue trying other names
            # fallback: queue to worker_pool if available
            if self.worker_pool and hasattr(self.worker_pool, "submit"):
                try:
                    self.worker_pool.submit(lambda: orch)  # best-effort placeholder
                    return {"status": "queued_to_worker_pool"}
                except Exception:
                    logger.exception("Failed to queue plan to worker_pool")
                    return {"status": "queue_failed"}

            return {"status": "no_execution_path"}
        except Exception:
            logger.exception("Dispatch error")
            return {"status": "dispatch_exception"}

    # ----------------------------
    # Build prompts
    # ----------------------------
    def _build_planning_prompt(self, *, event: Dict[str, Any], intent: Dict[str, Any], context: Dict[str, Any]) -> str:
        try:
            lines = [
                "You are Titan's autonomous planner. Produce a DSL or JSON plan for execution.",
                "Intent:",
                str(intent),
                "Event:",
                str(event),
            ]
            if context:
                lines.extend(["Context:", str(context)])
            lines.append("Return a single DSL or JSON plan.")
            return "\n\n".join(lines)
        except Exception:
            return f"Intent:{intent}\nEvent:{event}"

    # ----------------------------
    # Fallback DSL -> Plan (exposed for SkillManager)
    # ----------------------------
    async def _wrap_plan_with_dsl(self, dsl_text: str):
        """
        Attempt to compile DSL into the Plan object the orchestrator expects.
        This method is intentionally permissive and returns either a Plan model or a plan-like dict.
        """
        # First attempt: planner compile helpers
        try:
            if self.planner:
                for name in ("compile_dsl", "plan_from_dsl", "dsl_to_plan"):
                    if hasattr(self.planner, name):
                        fn = getattr(self.planner, name)
                        try:
                            res = fn(dsl_text)
                            return await _await_maybe(res)
                        except Exception:
                            logger.debug("planner.%s failed", name)
        except Exception:
            logger.exception("Planner wrapper top-level error")

        # LLM dsl generator may already return a compiled plan
        try:
            if self.llm_dsl_generator and hasattr(self.llm_dsl_generator, "generate_dsl_async"):
                res = await _await_maybe(self.llm_dsl_generator.generate_dsl_async(dsl_text))
                return res
        except Exception:
            logger.exception("llm_dsl_generator failed in wrap")

        # last-resort: return DSL as raw plan payload
        return {"id": f"skill_plan_{int(_now())}", "raw_dsl": dsl_text, "source": "skill"}

    # ----------------------------
    # ASK USER publisher
    # ----------------------------
    async def _publish_ask_user(self, event: Dict[str, Any], intent: Dict[str, Any], decision: Dict[str, Any]) -> None:
        payload = {
            "source": "autonomy",
            "type": "ask_user_confirmation",
            "event": event,
            "intent": intent,
            "decision": decision,
            "ts": _now(),
        }
        try:
            if self.event_bus and hasattr(self.event_bus, "publish"):
                # best-effort non-blocking publish
                try:
                    self.event_bus.publish("autonomy.ask_user_confirmation", payload, block=False)
                except TypeError:
                    # older API shapes
                    try:
                        self.event_bus.publish("autonomy.ask_user_confirmation", payload)
                    except Exception:
                        logger.exception("EventBus publish fallback failed")
            else:
                logger.info("ASK_USER: %s", payload)
        except Exception:
            logger.exception("publish ask user failed")

    # ----------------------------
    # Episodic logging
    # ----------------------------
    async def _record_episode(self, event: Dict[str, Any], intent: Dict[str, Any], policy: Dict[str, Any], outcome: Dict[str, Any]) -> None:
        record = {"ts": _now(), "event": event, "intent": intent, "policy": policy, "outcome": outcome}
        try:
            if self.episodic_store:
                if hasattr(self.episodic_store, "append"):
                    try:
                        self.episodic_store.append(record)
                        return
                    except Exception:
                        logger.exception("episodic_store.append failed")
                if hasattr(self.episodic_store, "write"):
                    try:
                        self.episodic_store.write(record)
                        return
                    except Exception:
                        logger.exception("episodic_store.write failed")
            # fallback: store small record in context_store if available
            if self.context_store and hasattr(self.context_store, "set"):
                try:
                    self.context_store.set("last_episode", record)
                    return
                except Exception:
                    logger.exception("context_store.set failed")
            # last fallback: log
            logger.debug("Episode: %s", record)
        except Exception:
            logger.exception("record episode top-level failure")

    # ----------------------------
    # Lifecycle: start / stop
    # ----------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # subscribe
        try:
            self._subscribe_to_events()
        except Exception:
            logger.exception("Subscription failed")

        # try to attach skill manager (if integration available)
        if _HAS_SKILL_INTEGRATION and attach_skill_manager_to_engine and self.skill_manager is None:
            try:
                self.skill_manager = attach_skill_manager_to_engine(self, auto_register_modules=None, persist_state=True)
                # kick off skill manager start in background but await a short readiness window
                if hasattr(self.skill_manager, "start"):
                    start_res = self.skill_manager.start()
                    if asyncio.iscoroutine(start_res):
                        # schedule but do not block engine start indefinitely
                        task = asyncio.create_task(start_res)
                        try:
                            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
                        except asyncio.TimeoutError:
                            logger.info("SkillManager start scheduled in background")
                        except Exception:
                            logger.exception("SkillManager.start raised")
            except Exception:
                logger.exception("SkillManager attach/start failed; continuing without skills")

        # start workers
        concurrency = max(1, getattr(self.config, "event_processing_concurrency", 2))
        for i in range(concurrency):
            t = asyncio.create_task(self._event_worker(i))
            self._consumer_tasks.append(t)

        logger.info("AutonomyEngine started with %d workers; skills attached=%s", len(self._consumer_tasks), bool(self.skill_manager))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        # stop accepting new events: unsubscribe
        try:
            if self.event_bus and hasattr(self.event_bus, "unsubscribe"):
                for topic in self._subscribed_event_types:
                    try:
                        self.event_bus.unsubscribe(topic, self._on_event)
                    except Exception:
                        logger.debug("unsubscribe failed for %s", topic)
        except Exception:
            logger.exception("unsubscribe loop failed")

        # stop worker tasks
        for t in list(self._consumer_tasks):
            try:
                t.cancel()
                await t
            except Exception:
                pass
        self._consumer_tasks.clear()

        # stop skill manager (best-effort)
        try:
            sm = getattr(self, "skill_manager", None)
            if sm and hasattr(sm, "stop"):
                maybe = sm.stop()
                if asyncio.iscoroutine(maybe):
                    try:
                        await asyncio.wait_for(maybe, timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning("SkillManager.stop timed out")
                    except Exception:
                        logger.exception("SkillManager.stop failed")
        except Exception:
            logger.exception("Error stopping SkillManager")

        logger.info("AutonomyEngine stopped")

    # ----------------------------
    # Health / diagnostics
    # ----------------------------
    async def health(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "queue_size": self._event_queue.qsize(),
            "workers": len(self._consumer_tasks),
            "skills_attached": bool(self.skill_manager),
        }
