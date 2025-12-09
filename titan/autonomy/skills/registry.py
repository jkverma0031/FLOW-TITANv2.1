# titan/autonomy/skills/registry.py
from __future__ import annotations
from typing import Type, Dict, List, Callable, Any
import importlib
import logging

logger = logging.getLogger("titan.skills.registry")

_SKILL_REGISTRY: Dict[str, Type[Any]] = {}

def register_skill(skill_cls: Type[Any]) -> Type[Any]:
    """
    Decorator to register a Skill class into the in-process registry.
    Usage:
        @register_skill
        class MySkill(BaseSkill): ...
    """
    name = getattr(skill_cls, "NAME", skill_cls.__name__)
    _SKILL_REGISTRY[name] = skill_cls
    logger.debug("register_skill: registered %s -> %s", name, skill_cls)
    return skill_cls

def get_registered_skill_names() -> List[str]:
    return list(_SKILL_REGISTRY.keys())

def get_registered_skill(name: str):
    return _SKILL_REGISTRY.get(name)

def list_registered_skills() -> Dict[str, Type[Any]]:
    return dict(_SKILL_REGISTRY)

def register_from_module(module_name: str):
    """
    Import `module_name` and register all BaseSkill subclasses found.
    Useful for dynamic discovery (e.g. 'titan.autonomy.skills.desktop_awareness').
    """
    try:
        m = importlib.import_module(module_name)
    except Exception:
        logger.exception("register_from_module failed to import %s", module_name)
        return

    # register any attribute that looks like a skill
    for attr in dir(m):
        obj = getattr(m, attr)
        try:
            if isinstance(obj, type):
                # lazy check for NAME attribute or tick/event methods -> treat as skill
                if hasattr(obj, "NAME") or hasattr(obj, "on_event") or hasattr(obj, "tick"):
                    register_skill(obj)
        except Exception:
            pass
