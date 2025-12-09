# titan/cognition/hygiene_integration.py
"""
Integration helper: attach MemoryHygiene to the system and (optionally) schedule via temporal_scheduler.

Functions:
 - attach_memory_hygiene(app, schedule=True, dry_run=True)
 - run_hygiene_now(app, dry_run=True)
"""

from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any

from .memory_hygiene import MemoryHygiene

logger = logging.getLogger("titan.cognition.hygiene_integration")

def attach_memory_hygiene(app: Dict[str, Any], schedule: bool = True, dry_run: bool = True):
    mh = app.get("memory_hygiene")
    if not mh:
        mh = MemoryHygiene(app)
        app["memory_hygiene"] = mh

    # override default dry-run config if specified
    mh.config.DRY_RUN_DEFAULT = bool(dry_run)

    # if a temporal scheduler exists, schedule regular runs
    sched = app.get("temporal_scheduler")
    if schedule and sched:
        try:
            # schedule id specific so duplicate attach doesn't add multiple
            job_id = "memory_hygiene_heartbeat"
            start_ts = float(__import__("time").time()) + 5.0
            # schedule runs at mh.config.RUN_INTERVAL
            if getattr(sched, "schedule", None):
                sched.schedule(start_ts, {"type": "hygiene.run.request", "dry_run": mh.config.DRY_RUN_DEFAULT}, recurrence=mh.config.RUN_INTERVAL, job_id=job_id)
        except Exception:
            logger.exception("Failed to schedule memory_hygiene with temporal_scheduler")

    # subscribe to hygiene.run.request events to trigger run
    eb = app.get("event_bus")
    if eb and getattr(eb, "subscribe", None):
        def _on_request(evt):
            try:
                payload = evt if isinstance(evt, dict) else getattr(evt, "payload", {})
                dry = payload.get("dry_run", mh.config.DRY_RUN_DEFAULT)
                # fire run in background
                asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, mh.run_once(dry_run=dry))
            except Exception:
                logger.exception("hygiene.run.request handler failed")
        try:
            eb.subscribe("hygiene.run.request", _on_request)
        except Exception:
            try:
                eb.subscribe("hygiene.run", _on_request)
            except Exception:
                pass

    # start service loop non-blocking
    try:
        asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, mh.start())
    except Exception:
        logger.exception("Failed to start MemoryHygiene service loop")

    logger.info("MemoryHygiene attached (schedule=%s dry_run=%s)", schedule, dry_run)
    return mh

def run_hygiene_now(app: Dict[str, Any], dry_run: bool = True):
    mh = app.get("memory_hygiene")
    if not mh:
        mh = attach_memory_hygiene(app, schedule=False, dry_run=dry_run)
    try:
        return asyncio.get_event_loop().run_until_complete(mh.run_once(dry_run=dry_run))
    except Exception:
        # fallback: schedule in background
        asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, mh.run_once(dry_run=dry_run))
        return {"status": "scheduled"}
