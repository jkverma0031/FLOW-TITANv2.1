# titan/autonomy/skills/__init__.py
from __future__ import annotations
from typing import List
import logging

from .registry import register_skill, get_registered_skill, list_registered_skills, register_from_module

# Import builtin skills so they register early when the package is imported.
# The DesktopAwarenessSkill file you provided will be imported here (if present).
try:
    # attempt relative import of desktop awareness skill
    from .desktop_awareness import DesktopAwarenessSkill  # type: ignore
    # optionally auto-register via decorator in desktop_awareness or register here:
    try:
        register_skill(DesktopAwarenessSkill)
    except Exception:
        logging.getLogger("titan.skills").debug("desktop_awareness registration skipped")
except Exception:
    logging.getLogger("titan.skills").debug("No builtin desktop_awareness skill available to auto-register")

__all__ = [
    "register_skill",
    "get_registered_skill",
    "list_registered_skills",
    "register_from_module",
]
