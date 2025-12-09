# titan/autonomy/skills/manager.py
"""
SkillManager - manages lifecycle, event subscriptions and tick scheduling for skills.

 - skills are Python classes (subclasses of BaseSkill)
 - SkillManager keeps an asyncio loop task to dispatch events & ticks
 - Skills can use SkillContext to call planner/memory/orchestrator safely
"""

from __future__ import annotations
import asyncio
import fnmatch
import inspect
import logging
import time
import types
from typing import Any, Dict, Iterable, List, Optional, Callable, Coroutine, Type

from .base import BaseSkill
from .context import make_skill_context

logger = logging.getLogger("titan.skills.manager")

class SkillManager:
    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        event_bus: Any = None,
        planner: Any = None,
        orchestrator: Any = None,
        policy_engine: Any = None,
        memory: Any = None,
        runtime_api: Any = None,
        app_context: Any = None,
        default_session_id: Optional[str] = None,
    ):
        self.loop = loop
        self.event_bus = event_bus
        self.planner = planner
        self.orchestrator = orchestrator
        self.policy_engine = policy_engine
        self.memory = memory
        self.runtime_api = runtime_api
        self.app_context = app_context
        self.default_session_id = default_session_id

        # internal
        self._skills: Dict[str, BaseSkill] = {}
        self._skill_types: Dict[str, Type[BaseSkill]] = {}
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._subscribed_patterns: List[str] = []
        self._bg_tasks: List[asyncio.Task] = []

        # register default helper methods
        self._publish_event = self._default_publish_event
        self._query_memory = self._default_query_memory
        self._plan_with_dsl = self._default_plan_with_dsl
        self._execute_plan = self._default_execute_plan
        self._runtime_get = lambda k, d=None: d
        self._runtime_set = lambda k, v: None

        # wire runtime get/set from runtime_api or app_context if available
        self._wire_runtime_getset(runtime_api=runtime_api, app_context=app_context)

        # if event_bus supports subscribe, connect dispatcher (supports multiple subscribe signatures)
        if self.event_bus is not None:
            self._try_subscribe_to_event_bus()

    # -------------------------
    # Runtime wiring helpers
    # -------------------------
    def _wire_runtime_getset(self, runtime_api: Any = None, app_context: Any = None) -> None:
        """
        Prefer runtime_api.get/set -> app_context.context_store.get/set -> session_manager state.
        This allows skills to persist small amounts of transient state across ticks.
        The bound functions are defensive wrappers that accept sync or async providers.
        """
        # runtime_api takes precedence
        try:
            if runtime_api is not None and hasattr(runtime_api, "get") and hasattr(runtime_api, "set"):
                self._runtime_get = self._wrap_sync_or_async_get(runtime_api.get)
                self._runtime_set = self._wrap_sync_or_async_set(runtime_api.set)
                logger.debug("SkillManager wired runtime_get/set from runtime_api")
                return
        except Exception:
            logger.exception("error wiring runtime_api")

        # fallback to app_context services (context_store or session_manager)
        try:
            if app_context is not None:
                cs = app_context.get("context_store", None)
                if cs and hasattr(cs, "get") and hasattr(cs, "set"):
                    self._runtime_get = self._wrap_sync_or_async_get(cs.get)
                    self._runtime_set = self._wrap_sync_or_async_set(cs.set)
                    logger.debug("SkillManager wired runtime_get/set from app_context.context_store")
                    return
                sm = app_context.get("session_manager", None)
                if sm and hasattr(sm, "get") and hasattr(sm, "set"):
                    self._runtime_get = self._wrap_sync_or_async_get(sm.get)
                    self._runtime_set = self._wrap_sync_or_async_set(sm.set)
                    logger.debug("SkillManager wired runtime_get/set from app_context.session_manager")
                    return
        except Exception:
            logger.exception("error wiring app_context runtime helpers")

        logger.debug("SkillManager uses default noop runtime_get/set")

    def _wrap_sync_or_async_get(self, fn):
        async def _getter(key, default=None):
            try:
                res = fn(key, default)
                if asyncio.iscoroutine(res):
                    return await res
                return res
            except Exception:
                logger.exception("runtime_get wrapper encountered exception")
                return default
        return lambda k, d=None: asyncio.get_event_loop().run_until_complete(_getter(k, d)) if not asyncio.get_event_loop().is_running() else _getter(k, d)

    def _wrap_sync_or_async_set(self, fn):
        async def _setter(key, val):
            try:
                res = fn(key, val)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.exception("runtime_set wrapper encountered exception")
        return lambda k, v: asyncio.get_event_loop().run_until_complete(_setter(k, v)) if not asyncio.get_event_loop().is_running() else _setter(k, v)

    # -------------------------
    # EventBus subscribe helpers
    # -------------------------
    def _try_subscribe_to_event_bus(self):
        try:
            subscribe_fn = getattr(self.event_bus, "subscribe", None)
            if subscribe_fn is None:
                logger.debug("event_bus has no subscribe method; SkillManager will not auto-subscribe")
                return

            def _cb(evt):
                try:
                    # thread-safe enqueue
                    self.loop.call_soon_threadsafe(self._event_queue.put_nowait, evt)
                except Exception:
                    logger.exception("skill manager failed to enqueue event")

            # inspect subscribe signature
            try:
                sig = inspect.signature(subscribe_fn)
                params = len(sig.parameters)
            except Exception:
                params = 1

            subscribed = False
            # try (topic, callback) signature
            try:
                if params == 2:
                    subscribe_fn("*", _cb)
                    subscribed = True
                elif params == 1:
                    subscribe_fn(_cb)
                    subscribed = True
                else:
                    # attempt common variants
                    try:
                        subscribe_fn("*", _cb)
                        subscribed = True
                    except TypeError:
                        subscribe_fn(_cb)
                        subscribed = True
            except Exception:
                # final fallback: attempt to call and swallow
                try:
                    subscribe_fn(_cb)
                    subscribed = True
                except Exception:
                    logger.exception("SkillManager failed to subscribe to event_bus using available signatures")

            if subscribed:
                logger.debug("SkillManager subscribed to event_bus via subscribe()")
        except Exception:
            logger.exception("while connecting to event_bus")

    # -------------------------
    # Registration / loading
    # -------------------------
    def register_skill_type(self, skill_cls: Type[BaseSkill]) -> None:
        name = getattr(skill_cls, "NAME", skill_cls.__name__)
        self._skill_types[name] = skill_cls
        logger.debug("Registered skill type %s", name)

    def instantiate_skill(self, name: str, skill_cls: Type[BaseSkill], **kwargs) -> BaseSkill:
        inst = skill_cls(self, **kwargs)
        self._skills[name] = inst
        return inst

    def load_skill(self, skill_cls: Type[BaseSkill]) -> BaseSkill:
        name = getattr(skill_cls, "NAME", skill_cls.__name__)
        inst = self.instantiate_skill(name, skill_cls)
        logger.info("Loaded skill %s", name)
        return inst

    def register_and_load(self, skill_classes: Iterable[Type[BaseSkill]]):
        for cls in skill_classes:
            self.register_skill_type(cls)
            self.load_skill(cls)

    def register_and_load_from_module(self, module: Any):
        """
        Convenience: given a module, register all BaseSkill subclasses found in it.
        """
        for attr in dir(module):
            v = getattr(module, attr)
            if isinstance(v, type) and issubclass(v, BaseSkill) and v is not BaseSkill:
                self.register_skill_type(v)
                self.load_skill(v)

    # -------------------------
    # wiring helpers (can be overridden)
    # -------------------------
    def set_publish_event(self, fn: Callable[[Dict[str, Any]], Coroutine[Any, Any, Any]]) -> None:
        self._publish_event = fn

    def set_query_memory(self, fn: Callable[[str, int], Coroutine[Any, Any, Any]]) -> None:
        self._query_memory = fn

    def set_plan_with_dsl(self, fn: Callable[[str], Coroutine[Any, Any, Any]]) -> None:
        self._plan_with_dsl = fn

    def set_execute_plan(self, fn: Callable[[Any], Coroutine[Any, Any, Any]]) -> None:
        self._execute_plan = fn

    def set_runtime_getset(self, get_fn: Callable[[str, Any], Any], set_fn: Callable[[str, Any], None]) -> None:
        self._runtime_get = get_fn
        self._runtime_set = set_fn

    # -------------------------
    # Default implementations (safe no-ops)
    # -------------------------
    async def _default_publish_event(self, event: Dict[str, Any]) -> None:
        if self.event_bus and hasattr(self.event_bus, "publish"):
            try:
                maybe_result = self.event_bus.publish(event)
                if asyncio.iscoroutine(maybe_result):
                    await maybe_result
            except Exception:
                logger.exception("publish_event failed")
        else:
            logger.debug("SkillManager publish_event (noop): %s", event)

    async def _default_query_memory(self, query: str, k: int = 3) -> Any:
        if self.memory and hasattr(self.memory, "query"):
            try:
                res = self.memory.query(query, k)
                if asyncio.iscoroutine(res):
                    return await res
                return res
            except Exception:
                logger.exception("memory query failed")
                return []
        return []

    async def _default_plan_with_dsl(self, dsl: str, skill_name: Optional[str] = None) -> Any:
        if self.planner and hasattr(self.planner, "plan_from_dsl"):
            try:
                plan = self.planner.plan_from_dsl(dsl)
                if asyncio.iscoroutine(plan):
                    return await plan
                return plan
            except Exception:
                logger.exception("planner.plan_from_dsl failed")
                return None
        raise RuntimeError("Planner not available")

    async def _default_execute_plan(self, plan: Any, skill: Optional[BaseSkill] = None) -> Any:
        """
        Executes a plan after performing a policy check if a PolicyEngine is available.
        `skill` is the originating skill instance (used for auditing / policy).
        """
        # policy check
        try:
            if self.policy_engine is not None:
                actor = getattr(skill, "NAME", "unknown_skill") if skill is not None else "unknown_skill"
                try:
                    trust = self._runtime_get("trust_level", "low")
                except Exception:
                    trust = "low"
                trust = (trust or "low").lower() if isinstance(trust, str) else "low"

                resource = {"subsystem": "skill", "skill": actor, "plan_id": getattr(plan, "id", None)}
                allowed, reason = self.policy_engine.allow_action(actor=actor, trust_level=trust, action="execute_plan", resource=resource)
                if not allowed:
                    logger.warning("PolicyEngine denied execution of plan %s by skill %s (%s)", getattr(plan, "id", None), actor, reason)
                    return None
        except Exception:
            logger.exception("policy check failed; proceeding according to PolicyEngine mode")

        # execute using orchestrator if present
        if self.orchestrator and hasattr(self.orchestrator, "execute_plan"):
            try:
                res = self.orchestrator.execute_plan(plan)
                if asyncio.iscoroutine(res):
                    return await res
                return res
            except Exception:
                logger.exception("orchestrator.execute_plan failed")
                return None

        raise RuntimeError("Orchestrator not available")

    # -------------------------
    # Event dispatching / pattern matching
    # -------------------------
    def _match_patterns(self, topic: str, patterns: Iterable[str]) -> bool:
        for p in patterns:
            if fnmatch.fnmatch(topic, p):
                return True
        return False

    async def _dispatch_event_to_skills(self, event: Dict[str, Any]) -> None:
        """
        For each skill that subscribed to patterns matching event['type'] (if present),
        call on_event asynchronously (fire-and-forget to avoid blocking).
        """
        topic = str(event.get("type") or event.get("topic") or "")
        for skill in list(self._skills.values()):
            try:
                patterns = getattr(skill, "SUBSCRIPTIONS", ())
                if not patterns:
                    continue
                if self._match_patterns(topic, patterns):
                    ctx = make_skill_context(
                        publish_event=self._publish_event,
                        query_memory=self._query_memory,
                        plan_with_dsl=lambda dsl, _skill=skill: self._plan_with_dsl(dsl),
                        execute_plan=lambda plan, _skill=skill: self._execute_plan(plan, skill=_skill),
                        runtime_get=self._runtime_get,
                        runtime_set=self._runtime_set,
                        session_id=self.default_session_id
                    )
                    try:
                        setattr(ctx, "skill_name", getattr(skill, "NAME", None))
                    except Exception:
                        pass
                    self._schedule_background(skill.on_event(event, ctx))
            except Exception:
                logger.exception("dispatch to skill failed for %s", skill)

    def _schedule_background(self, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            t = self.loop.create_task(coro)
            self._bg_tasks.append(t)
            def _on_done(fut):
                try:
                    self._bg_tasks.remove(t)
                except Exception:
                    pass
            t.add_done_callback(_on_done)
        except RuntimeError:
            logger.exception("Failed to schedule background task")

    # -------------------------
    # Tick loop & lifecycle
    # -------------------------
    async def _tick_loop(self):
        last_tick_at: Dict[str, float] = {}
        try:
            while self._running:
                try:
                    event = None
                    try:
                        event = await asyncio.wait_for(self._event_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        event = None

                    if event is not None:
                        await self._dispatch_event_to_skills(event)
                        continue

                    now = time.time()
                    for skill in list(self._skills.values()):
                        tick_interval = getattr(skill, "TICK_INTERVAL", None)
                        if not tick_interval:
                            continue
                        last = last_tick_at.get(skill.NAME, 0.0)
                        if (now - last) >= tick_interval:
                            ctx = make_skill_context(
                                publish_event=self._publish_event,
                                query_memory=self._query_memory,
                                plan_with_dsl=lambda dsl, _skill=skill: self._plan_with_dsl(dsl),
                                execute_plan=lambda plan, _skill=skill: self._execute_plan(plan, skill=_skill),
                                runtime_get=self._runtime_get,
                                runtime_set=self._runtime_set,
                                session_id=self.default_session_id
                            )
                            try:
                                setattr(ctx, "skill_name", getattr(skill, "NAME", None))
                            except Exception:
                                pass
                            self._schedule_background(skill.tick(ctx))
                            last_tick_at[skill.NAME] = now
                    await asyncio.sleep(0.1)
                except Exception:
                    logger.exception("SkillManager loop iteration failed")
        finally:
            logger.debug("SkillManager tick loop exiting")

    async def start(self):
        if self._running:
            return
        self._running = True
        for skill in list(self._skills.values()):
            try:
                await skill.on_start()
            except Exception:
                logger.exception("skill on_start failed for %s", skill)
        self._tasks.append(self.loop.create_task(self._tick_loop()))
        logger.info("SkillManager started with %d skills", len(self._skills))

    async def stop(self):
        if not self._running:
            return
        self._running = False
        # cancel tick loop and tasks
        for t in list(self._tasks):
            try:
                t.cancel()
            except Exception:
                pass

        # stop skills
        for skill in list(self._skills.values()):
            try:
                await skill.on_stop()
            except Exception:
                logger.exception("skill on_stop failed for %s", skill)

        # cancel and await bg tasks with a short timeout
        for t in list(self._bg_tasks):
            try:
                if not t.done():
                    t.cancel()
            except Exception:
                pass
        # give tasks a moment to finish cleanly
        try:
            await asyncio.sleep(0.2)
        except Exception:
            pass

        # purge remaining tasks
        self._bg_tasks = [t for t in self._bg_tasks if not t.done()]
        logger.info("SkillManager stopped")

    # -------------------------
    # App integration helpers
    # -------------------------
    def attach_to_app_context(self, app_context: Any, name: str = "skill_manager"):
        """
        Convenience: register this manager instance into the provided app_context (if it supports .set or dict semantics).
        """
        try:
            if hasattr(app_context, "set"):
                app_context.set(name, self)
            elif isinstance(app_context, dict):
                app_context[name] = self
            elif hasattr(app_context, "register"):
                app_context.register(name, self)
            logger.debug("SkillManager attached to app_context as '%s'", name)
        except Exception:
            logger.exception("Failed to attach SkillManager to app_context")
