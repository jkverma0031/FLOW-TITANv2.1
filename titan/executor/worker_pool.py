# titan/executor/worker_pool.py
from __future__ import annotations
import asyncio
import concurrent.futures
import logging
from typing import Dict, Any, Optional, Callable, List
import inspect
import time

logger = logging.getLogger(__name__)

class WorkerPool:
    """
    Async-first WorkerPool / Task Scheduler.
    """

    def __init__(self, max_workers: int = 16, thread_workers: int = 8):
        self.max_workers = max_workers

        # FIX: remove unsafe loop init
        self._loop = None

        # Threadpool used for blocking or CPU tasks
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=thread_workers)

        self._tasks: Dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_workers)
        self._running = True

    # --------------------------
    # Public API: async-first
    # --------------------------
    async def run_async(self, action_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Core execution entrypoint with async provider dispatch.
        """
        async with self._semaphore:
            try:
                action = action_request.get("action")
                node = action_request.get("node")
                task_args = action_request.get("task_args") or (getattr(action, "args", None) or {})
                context = action_request.get("context") or {}
                negotiator = action_request.get("_negotiator")
                sandbox = action_request.get("_sandbox")
                hostbridge = action_request.get("_hostbridge")

                # Negotiator
                decision = None
                if negotiator is not None and action is not None:
                    try:
                        if inspect.iscoroutinefunction(negotiator.decide):
                            decision = await negotiator.decide(action, context=context)
                        else:
                            loop = asyncio.get_event_loop()
                            decision = await loop.run_in_executor(self._executor, lambda: negotiator.decide(action, context=context))
                    except Exception:
                        logger.exception("Negotiator.decide failed")
                        decision = None

                provider = decision.provider if decision else None

                # fallback provider
                if not provider and node:
                    metadata = node.get("metadata") or {}
                    provider = metadata.get("provider") or metadata.get("plugin") or metadata.get("task_provider")

                # type-based fallback
                if not provider:
                    atype = getattr(action, "type", None)
                    try:
                        if atype and getattr(atype, "name", "").upper() == "PLUGIN":
                            provider = action.module
                    except Exception:
                        provider = None

                if not provider:
                    provider = "sandbox"

                # -------------------------------------------------------
                # Provider Routing
                # -------------------------------------------------------

                # PLUGINS
                if provider not in ("sandbox", "hostbridge", "simulated", "denied"):
                    from titan.runtime.plugins.registry import get_plugin
                    plugin = get_plugin(provider)
                    if not plugin:
                        return {"status": "error", "error": f"plugin '{provider}' not registered"}

                    loop = asyncio.get_event_loop()

                    # Prefer async execution
                    if hasattr(plugin, "execute_async") and inspect.iscoroutinefunction(plugin.execute_async):
                        try:
                            result = await plugin.execute_async(
                                action=action.command if getattr(action, "command", None) else "run",
                                args=(getattr(action, "args", None) or task_args) or {},
                                context=context,
                            )
                            return {"status": "ok", "result": result}
                        except Exception:
                            logger.exception("plugin.execute_async failed; trying sync fallback")

                            # Sync fallback in threadpool
                            try:
                                sync_result = await loop.run_in_executor(
                                    self._executor,
                                    lambda: plugin.execute(
                                        action=action.command if getattr(action, "command", None) else "run",
                                        args=(getattr(action, "args", None) or task_args) or {},
                                        context=context,
                                    ),
                                )
                                return {"status": "ok", "result": sync_result}
                            except Exception as e:
                                logger.exception("plugin.sync fallback failed")
                                return {"status": "error", "error": str(e)}

                    # If plugin has no async implementation
                    loop = asyncio.get_event_loop()
                    try:
                        sync_result = await loop.run_in_executor(
                            self._executor,
                            lambda: plugin.execute(
                                action=action.command if getattr(action, "command", None) else "run",
                                args=(getattr(action, "args", None) or task_args) or {},
                                context=context,
                            ),
                        )
                        return {"status": "ok", "result": sync_result}
                    except Exception as e:
                        logger.exception("plugin.sync execution failed")
                        return {"status": "error", "error": str(e)}

                # SANDBOX
                if provider == "sandbox":
                    cmd = getattr(action, "command", None) or (getattr(action, "args", None) or {}).get("cmd")
                    metadata = getattr(action, "metadata", {}) or {}
                    timeout = metadata.get("timeout")

                    if cmd is None and node:
                        cmd = (node.get("metadata") or {}).get("command")

                    if not cmd:
                        return {"status": "error", "error": "Sandbox command missing"}

                    if hasattr(sandbox, "run_command_async") and inspect.iscoroutinefunction(sandbox.run_command_async):
                        out = await sandbox.run_command_async(cmd, timeout=timeout, context=context)
                        return {"status": "ok", "result": out}

                    # fallback: blocking run
                    loop = asyncio.get_event_loop()
                    out = await loop.run_in_executor(
                        self._executor,
                        lambda: sandbox.run_command(cmd, timeout=timeout, context=context),
                    )
                    return {"status": "ok", "result": out}

                # HOSTBRIDGE
                if provider == "hostbridge":
                    if hasattr(hostbridge, "execute_async") and inspect.iscoroutinefunction(hostbridge.execute_async):
                        out = await hostbridge.execute_async(action, context=context)
                        return {"status": "ok", "result": out}

                    loop = asyncio.get_event_loop()
                    out = await loop.run_in_executor(
                        self._executor,
                        lambda: hostbridge.execute(action, context=context),
                    )
                    return {"status": "ok", "result": out}

                # SIMulated
                if provider == "simulated":
                    return {"status": "ok", "result": {"message": "simulated"}}

                if provider == "denied":
                    return {"status": "error", "error": "action denied by policy"}

                return {"status": "error", "error": f"unknown provider: {provider}"}

            except Exception as e:
                logger.exception("WorkerPool.run_async fatal error")
                return {"status": "error", "error": str(e)}

    # --------------------------------
    # Sync wrapper
    # --------------------------------
    def run_sync(self, action_request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self.run_async(action_request), loop
                )
                return future.result()
            return asyncio.run(self.run_async(action_request))
        except Exception as e:
            logger.exception("WorkerPool.run_sync failed")
            return {"status": "error", "error": str(e)}

    # --------------------------------
    # Submit Task
    # --------------------------------
    def submit(self, action_request: Dict[str, Any]) -> "asyncio.Task":
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            raise RuntimeError("submit() requires a running event loop")
        return loop.create_task(self.run_async(action_request))

    # --------------------------------
    # Shutdown
    # --------------------------------
    async def shutdown(self):
        self._running = False
        await asyncio.sleep(0.01)
        self._executor.shutdown(wait=True)
