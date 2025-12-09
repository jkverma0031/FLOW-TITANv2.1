# titan/autonomy/skills/context.py
"""
Thin wrappers for SkillContext helpers to be used by SkillManager.
This file is a convenience factory so SkillContext code doesn't need to know internals.
"""

from __future__ import annotations
from typing import Any, Dict, Callable, Coroutine, Optional
from .base import SkillContext

def make_skill_context(
    publish_event: Callable[[Dict[str,Any]], Coroutine[Any,Any,Any]],
    query_memory: Callable[[str,int], Coroutine[Any,Any,Any]],
    plan_with_dsl: Callable[[str], Coroutine[Any,Any,Any]],
    execute_plan: Callable[[Any], Coroutine[Any,Any,Any]],
    runtime_get: Callable[[str,Any], Any],
    runtime_set: Callable[[str,Any], None],
    session_id: Optional[str] = None,
) -> SkillContext:
    """
    Factory that returns a SkillContext instance wired to the manager-provided functions.

    Note: the provided callables may be synchronous or asynchronous; the SkillManager's
    default wrappers handle both cases. Skills should await the coroutine-returning methods
    (plan_with_dsl, execute_plan, query_memory, publish_event) because SkillManager exposes
    them as async functions.
    """
    return SkillContext(
        publish_event=publish_event,
        query_memory=query_memory,
        plan_with_dsl=plan_with_dsl,
        execute_plan=execute_plan,
        runtime_get=runtime_get,
        runtime_set=runtime_set,
        session_id=session_id,
    )
