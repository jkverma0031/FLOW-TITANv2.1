# titan/cognition/load_integration.py
"""
Load Balancer Integration helpers

Provides a single entrypoint `attach_load_balancer(app, engine, skill_manager, *, sensitivity='moderate')`
that:

- Creates CognitiveLoadBalancer (if not present)
- Attaches lb to app
- Monkey-patches SkillManager with should_run(skill_name) method that consults lb
- Wraps skill proposal flow by subscribing to event_bus and filtering proposals through lb
- Wraps ReflectionEngine and MemoryConsolidator to consult lb.allow_service before running heavy cycles
- Exposes safe detach helper

This integration is non-destructive: it does not replace core logic, only extends at runtime.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable

from .load_balancer import CognitiveLoadBalancer, CognitiveLoadBalancerConfig

logger = logging.getLogger("titan.cognition.load_integration")

# store original references so we can detach
_INTEGRATION_STATE = {
    "patched_skill_manager": False,
    "subscribed_eventbus": False,
    "patched_reflection": False,
    "patched_memory": False,
    "lb": None,
    "handlers": [],
}


def attach_load_balancer(app: Dict[str, Any], engine: Optional[Any] = None, skill_manager: Optional[Any] = None, *,
                         sensitivity: str = "moderate") -> CognitiveLoadBalancer:
    """
    Attach and wire the CognitiveLoadBalancer into your runtime.
    - app: kernel application dict
    - engine: AutonomyEngine instance (optional)
    - skill_manager: SkillManager instance (optional)
    Returns the created load balancer instance.
    """
    # create lb if not present
    lb = app.get("load_balancer")
    if not lb:
        cfg = CognitiveLoadBalancerConfig()
        # sensitivity mapping -> tune thresholds
        if sensitivity == "light":
            cfg.THRESHOLD_WARN = 0.7
            cfg.THRESHOLD_HIGH = 0.85
            cfg.SPREAD = 8.0
        elif sensitivity == "moderate":
            cfg.THRESHOLD_WARN = 0.6
            cfg.THRESHOLD_HIGH = 0.8
            cfg.SPREAD = 6.0
        elif sensitivity == "aggressive":
            cfg.THRESHOLD_WARN = 0.5
            cfg.THRESHOLD_HIGH = 0.7
            cfg.SPREAD = 4.5
        lb = CognitiveLoadBalancer(app, config=cfg)
        app["load_balancer"] = lb

    # attach skill_manager method if provided
    if skill_manager and not _INTEGRATION_STATE["patched_skill_manager"]:
        def should_run(skill_name: str) -> bool:
            try:
                st = skill_manager.get_skill_state(skill_name)
                # if disabled by state, disallow
                if st and getattr(st, "enabled", True) is False:
                    return False
                # respect skill-specific metadata cooldown override if present
                md_cooldown = None
                try:
                    md_cooldown = float(st.metadata.get("cooldown")) if st and isinstance(st.metadata, dict) and "cooldown" in st.metadata else None
                except Exception:
                    md_cooldown = None
                if md_cooldown and md_cooldown > 0:
                    # consult persistent last_action_at to ensure cooldown respected
                    import time as _t
                    last = getattr(st, "last_action_at", 0) if st else 0
                    if _t.time() - float(last) < md_cooldown:
                        return False
                # consult global load balancer with a simple light proposal for fairness
                # craft a minimal proposal to test
                p = {"skill": skill_name, "risk": "low", "confidence": 0.8, "priority": getattr(skill_manager._skill_types.get(skill_name, {}), "PRIORITY", 50)}
                return lb.allow_proposal(p)
            except Exception:
                return True

        # attach method (monkey patch)
        try:
            setattr(skill_manager, "should_run", should_run)
            _INTEGRATION_STATE["patched_skill_manager"] = True
            logger.debug("SkillManager patched with should_run")
        except Exception:
            logger.exception("Failed to patch SkillManager")

    # intercept proposals on event_bus if available: filter through lb.allow_proposal
    eb = app.get("event_bus")
    if eb and not _INTEGRATION_STATE["subscribed_eventbus"]:
        def _proposal_filter(evt):
            try:
                # evt may be dict or custom event object
                payload = getattr(evt, "payload", None) or evt.get("proposal") or evt.get("payload") or evt
                # proposal shape may be nested
                prop = payload.get("proposal") if isinstance(payload, dict) and payload.get("proposal") else payload
                # ensure dict
                if not isinstance(prop, dict):
                    return
                # consult lb
                allow = lb.allow_proposal(prop)
                if not allow:
                    # publish a truncated event instead to indicate throttling
                    try:
                        if getattr(eb, "publish", None):
                            eb.publish("cognition.proposal.throttled", {"ts": time.time(), "proposal": prop, "reason": "load_throttle", "load": lb.get_load()})
                        # drop the proposal by returning without forwarding
                        return
                    except Exception:
                        pass
                # otherwise forward as usual (assuming original pipeline uses the same event object)
            except Exception:
                logger.exception("proposal_filter failed")

        # subscribe - support both sync and async subscribe APIs
        try:
            if hasattr(eb, "subscribe"):
                eb.subscribe("skill.proposal", _proposal_filter)
            else:
                # fallback: if eventbus has on method
                if hasattr(eb, "on"):
                    eb.on("skill.proposal", _proposal_filter)
            _INTEGRATION_STATE["subscribed_eventbus"] = True
            _INTEGRATION_STATE["handlers"].append(("eventbus", _proposal_filter))
            logger.debug("LoadBalancer subscribed to event_bus.skill.proposal")
        except Exception:
            logger.exception("Failed to subscribe to event_bus skill.proposal")

    # patch ReflectionEngine.run_once or loop to check allow_service
    refl = app.get("reflection_engine")
    if refl and not _INTEGRATION_STATE["patched_reflection"]:
        try:
            orig_run_once = getattr(refl, "run_once", None)

            async def wrapped_run_once(*args, **kwargs):
                try:
                    # ask lb whether reflection should run now
                    if not lb.allow_service("reflection_engine"):
                        # emit a light event and skip
                        if hasattr(lb, "event_bus") and getattr(lb.event_bus, "publish", None):
                            lb.event_bus.publish("cognition.reflection.skipped", {"ts": time.time(), "load": lb.get_load()})
                        return {"skipped": True, "reason": "load_high", "load": lb.get_load()}
                    # otherwise run original
                    if asyncio.iscoroutinefunction(orig_run_once):
                        return await orig_run_once(*args, **kwargs)
                    else:
                        return orig_run_once(*args, **kwargs)
                except Exception:
                    logger.exception("wrapped_run_once failed")
                    # fallback to original to avoid dead path
                    if asyncio.iscoroutinefunction(orig_run_once):
                        return await orig_run_once(*args, **kwargs)
                    else:
                        return orig_run_once(*args, **kwargs)

            setattr(refl, "run_once", wrapped_run_once)
            _INTEGRATION_STATE["patched_reflection"] = True
            logger.debug("Patched ReflectionEngine.run_once with load checks")
        except Exception:
            logger.exception("Failed to patch ReflectionEngine")

    # patch MemoryConsolidator.consolidate_once or loop
    mem = app.get("memory_consolidator") or app.get("memory_consolidator_service") or app.get("memory_consolidator")
    if mem and not _INTEGRATION_STATE["patched_memory"]:
        try:
            orig_consolidate = getattr(mem, "consolidate_once", None)
            async def wrapped_consolidate_once(*args, **kwargs):
                try:
                    if not lb.allow_service("memory_consolidator"):
                        if hasattr(lb, "event_bus") and getattr(lb.event_bus, "publish", None):
                            lb.event_bus.publish("cognition.memory_consolidator.skipped", {"ts": time.time(), "load": lb.get_load()})
                        return {"skipped": True, "reason": "load_high", "load": lb.get_load()}
                    if asyncio.iscoroutinefunction(orig_consolidate):
                        return await orig_consolidate(*args, **kwargs)
                    else:
                        return orig_consolidate(*args, **kwargs)
                except Exception:
                    logger.exception("wrapped_consolidate_once failed")
                    if asyncio.iscoroutinefunction(orig_consolidate):
                        return await orig_consolidate(*args, **kwargs)
                    else:
                        return orig_consolidate(*args, **kwargs)

            setattr(mem, "consolidate_once", wrapped_consolidate_once)
            _INTEGRATION_STATE["patched_memory"] = True
            logger.debug("Patched MemoryConsolidator.consolidate_once with load checks")
        except Exception:
            logger.exception("Failed to patch MemoryConsolidator")

    # optionally attach a periodic sampler that updates lb from engine metrics (if engine provides health)
    if engine and hasattr(engine, "health"):
        async def _sampler():
            while True:
                try:
                    health = await engine.health() if asyncio.iscoroutinefunction(engine.health) else engine.health()
                    # map busy_indicator or cpu usage to a record_event
                    cpu = (health.get("cpu_percent") if isinstance(health, dict) else None) or 0.0
                    # heavier weighting when CPU usage high
                    if cpu > 0.0:
                        w = min(3.0, cpu / 20.0)
                        lb.record_event("io", w)
                except Exception:
                    pass
                await asyncio.sleep(5.0)
        try:
            asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, _sampler())
        except Exception:
            pass

    logger.info("LoadBalancer attached (sensitivity=%s)", sensitivity)
    _INTEGRATION_STATE["lb"] = lb
    return lb


def detach_load_balancer():
    """
    Attempt to undo patches and unsubscribe handlers.
    Best-effort only.
    """
    try:
        handlers = _INTEGRATION_STATE.get("handlers", [])
        eb = _INTEGRATION_STATE.get("lb").event_bus if _INTEGRATION_STATE.get("lb") else None
        for typ, h in handlers:
            try:
                if eb and getattr(eb, "unsubscribe", None):
                    eb.unsubscribe("skill.proposal", h)
            except Exception:
                pass
    except Exception:
        pass
    # reset state
    _INTEGRATION_STATE.update({
        "patched_skill_manager": False,
        "subscribed_eventbus": False,
        "patched_reflection": False,
        "patched_memory": False,
        "lb": None,
        "handlers": [],
    })
    logger.info("LoadBalancer detached (best-effort)")
