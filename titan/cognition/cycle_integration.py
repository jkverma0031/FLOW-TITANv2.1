# titan/cognition/cycle_integration.py
"""
Helper to attach the CognitiveLoop at startup.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any

from titan.cognition.cognitive_loop import CognitiveLoop

logger = logging.getLogger("titan.cognition.cycle_integration")

def attach_cognitive_loop(app: Dict[str, Any]):
    # Avoid duplicating
    if "cognitive_loop" in app:
        return app["cognitive_loop"]

    loop = CognitiveLoop(app)
    app["cognitive_loop"] = loop

    try:
        asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, loop.start())
        logger.info("CognitiveLoop attached and started")
    except Exception:
        logger.exception("Failed to start CognitiveLoop")

    return loop
