# titan/autonomy/skills/integration.py
from __future__ import annotations
import asyncio
import logging
from typing import Iterable, Optional, Any, List, Type

from .manager import SkillManager
from .registry import get_registered_skill, list_registered_skills, register_from_module

logger = logging.getLogger("titan.skills.integration")

async def _maybe_await(maybe):
    if asyncio.iscoroutine(maybe):
        return await maybe
    return maybe

def _safe_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        # create loop if called from sync code without running loop (rare)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

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

    This function intentionally does not start the SkillManager; it returns the manager
    so the caller can start it at the appropriate point in the engine lifecycle (e.g. after kernel startup).

    engine is expected to provide at least some of:
      - loop (asyncio loop) or None
      - event_bus
      - planner
      - orchestrator
      - policy_engine
      - memory
      - runtime_api
      - app_context

    Examples:
        manager = attach_skill_manager_to_engine(engine)
        await manager.start()
    """
    loop = getattr(engine, "loop", None) or _safe_loop()
    event_bus = getattr(engine, "event_bus", None) or getattr(engine, "app", {}).get("event_bus", None) if hasattr(engine, "app") else getattr(engine, "event_bus", None)
    planner = getattr(engine, "planner", None) or getattr(engine, "app", {}).get("planner", None) if hasattr(engine, "app") else None
    orchestrator = getattr(engine, "orchestrator", None)
    policy_engine = getattr(engine, "policy_engine", None) or getattr(engine, "app", {}).get("policy_engine", None) if hasattr(engine, "app") else None
    memory = getattr(engine, "memory", None) or getattr(engine, "app", {}).get("memory", None) if hasattr(engine, "app") else None
    runtime_api = getattr(engine, "runtime_api", None) or getattr(engine, "app", {}).get("runtime_api", None) if hasattr(engine, "app") else None
    app_context = getattr(engine, "app", None) or getattr(engine, "app_context", None)

    manager = SkillManager(
        loop=loop,
        event_bus=event_bus,
        planner=planner,
        orchestrator=orchestrator,
        policy_engine=policy_engine,
        memory=memory,
        runtime_api=runtime_api,
        app_context=app_context,
        default_session_id=default_session_id or getattr(engine, "default_session_id", None),
    )

    # Allow auto-register of modules (e.g., 'titan.autonomy.skills.desktop_awareness')
    if auto_register_modules:
        for mod in auto_register_modules:
            try:
                register_from_module(mod)
            except Exception:
                logger.exception("auto register module failed: %s", mod)

    # If no explicit classes passed, load all in registry
    if skill_classes:
        for cls in skill_classes:
            try:
                manager.register_skill_type(cls)
                manager.load_skill(cls)
            except Exception:
                logger.exception("failed to load skill class %s", cls)
    else:
        reg = list_registered_skills()
        for name, cls in reg.items():
            try:
                manager.register_skill_type(cls)
                manager.load_skill(cls)
            except Exception:
                logger.exception("failed to instantiate registered skill %s", name)

    # Attach manager into engine/app_context for discovery
    try:
        if app_context is not None:
            manager.attach_to_app_context(app_context, name="skill_manager")
        elif hasattr(engine, "app_context") and engine.app_context is not None:
            manager.attach_to_app_context(engine.app_context, name="skill_manager")
        else:
            # best-effort attach to engine
            try:
                setattr(engine, "skill_manager", manager)
            except Exception:
                pass
    except Exception:
        logger.exception("failed to attach SkillManager to app context")

    logger.info("SkillManager attached (skills=%d)", len(manager._skills))
    return manager
