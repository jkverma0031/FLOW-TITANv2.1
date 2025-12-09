# titan/autonomy/skills/integration.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Iterable, Type, Any
from .manager import SkillManager
from .registry import list_registered_skills, register_from_module

logger = logging.getLogger("titan.skills.integration")

def _default_context_store_factory(session_id: Optional[str]):
    """
    Default factory used by SkillManager to create a ContextStore per session.
    Expects that kernel/app provides a 'context_store_factory' or 'context_store' class.
    """
    # We expect the kernel to provide a callable at app['context_store_factory'] or app.get(...)
    raise RuntimeError("No context_store_factory provided; pass one into attach_skill_manager_to_engine")

def attach_skill_manager_to_engine(
    engine: Any,
    *,
    skill_classes: Optional[Iterable[Type]] = None,
    auto_register_modules: Optional[Iterable[str]] = None,
    persist_state: bool = True,
    default_session_id: Optional[str] = None,
) -> SkillManager:
    """
    Create and attach a SkillManager instance to the given `engine`.
    This version wires session_manager and context_store_factory from engine.app if available,
    and auto-loads registered skills and their persisted states.
    """
    loop = getattr(engine, "loop", None) or asyncio.get_event_loop()
    app = getattr(engine, "app", None) or {}
    event_bus = getattr(engine, "event_bus", None) or app.get("event_bus", None)
    session_manager = getattr(engine, "session_manager", None) or app.get("session_manager", None)
    context_store_factory = getattr(engine, "context_store_factory", None) or app.get("context_store_factory", None)

    if not context_store_factory:
        # if app provides session_manager, create a trivial factory that wraps ContextStore class stored at app
        ctx_fact = app.get("context_store_factory")
        if ctx_fact:
            context_store_factory = ctx_fact

    manager = SkillManager(
        loop=loop,
        event_bus=event_bus,
        session_manager=session_manager,
        context_store_factory=context_store_factory,
        default_session_id=default_session_id or getattr(engine, "default_session_id", None),
    )

    # Auto register modules if requested
    if auto_register_modules:
        for mod in auto_register_modules:
            try:
                register_from_module(mod)
            except Exception:
                logger.exception("Auto register failed for module %s", mod)

    # Register skill classes explicitly if provided, else register all known from registry
    if skill_classes:
        for cls in skill_classes:
            try:
                manager.register_skill_type(cls)
            except Exception:
                logger.exception("register_skill_type failed for %s", cls)
    else:
        for name, cls in list_registered_skills().items():
            try:
                manager.register_skill_type(cls)
            except Exception:
                logger.exception("Failed registering skill %s", name)

    # Load persisted states and instantiate skills (manager.start will do this too but we can pre-load)
    try:
        # set default session id on manager if engine provided one
        if getattr(engine, "default_session_id", None):
            manager.default_session_id = getattr(engine, "default_session_id")

        # instantiate skills (non-blocking)
        coro = manager.start()
        if asyncio.iscoroutine(coro):
            try:
                # schedule but don't block startup indefinitely
                asyncio.create_task(coro)
            except Exception:
                logger.exception("Failed scheduling manager.start()")

    except Exception:
        logger.exception("Failed starting SkillManager")

    # attach to engine/app for discoverability
    try:
        if hasattr(engine, "app") and isinstance(engine.app, dict):
            engine.app["skill_manager"] = manager
        else:
            setattr(engine, "skill_manager", manager)
    except Exception:
        logger.exception("Failed to attach skill_manager to engine")

    logger.info("SkillManager attached (registered=%d)", len(manager._skill_types))
    return manager
