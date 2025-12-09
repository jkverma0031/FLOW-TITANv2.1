# titan/stability/debug_mode.py
"""
Debug Mode helper and small utilities.

Features:
- toggle_debug(app, enable=True) -> sets verbose logging across cognitive modules
- context tracer: captures last N cycles/events for inspection (in-memory ring buffer)
- exposes helpers to dump recent cognition cycles and events

Use:
    from titan.stability.debug_mode import toggle_debug, get_tracer, add_trace
"""

from __future__ import annotations
import logging
import collections
import time
from typing import Dict, Any, Optional

logger = logging.getLogger("titan.stability.debug_mode")

# simple centralized tracer storage in app under key 'cognition_tracer'
TRACER_SIZE_DEFAULT = 200

def toggle_debug(app: Dict[str, Any], enable: bool = True, level: int = logging.DEBUG):
    """
    Globally toggle debug verbosity for Titan cognition modules.
    """
    root = logging.getLogger()
    if enable:
        root.setLevel(level)
    else:
        root.setLevel(logging.INFO)
    # also tag app state
    try:
        app["_debug_mode"] = bool(enable)
    except Exception:
        pass
    logger.info("toggle_debug set to %s", enable)

def get_tracer(app: Dict[str, Any]):
    t = app.get("cognition_tracer")
    if t is None:
        t = collections.deque(maxlen=TRACER_SIZE_DEFAULT)
        app["cognition_tracer"] = t
    return t

def add_trace(app: Dict[str, Any], kind: str, payload: Dict[str, Any]):
    t = get_tracer(app)
    t.append({"ts": time.time(), "kind": kind, "payload": payload})

def dump_traces(app: Dict[str, Any], limit: int = 100):
    t = get_tracer(app)
    return list(t)[-limit:]
