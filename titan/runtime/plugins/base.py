# titan/runtime/plugins/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging
import asyncio
import concurrent.futures

logger = logging.getLogger(__name__)

class PluginError(Exception):
    """Generic plugin error used by plugin implementations."""
    pass

class BasePlugin(ABC):
    """
    Async-first base plugin contract.

    Implementations MUST provide:
      - def get_manifest(self) -> Dict[str, Any]
      - async def execute_async(self, action: str, args: dict, context: dict) -> dict

    A synchronous `execute()` wrapper is provided for backward compatibility,
    implemented using asyncio.run_coroutine_threadsafe to avoid busy-wait polling.
    """

    name: str = "base"
    version: str = "0.0.1"
    description: str = "Abstract base plugin"

    def __init__(self, *, name: Optional[str] = None, version: Optional[str] = None, description: Optional[str] = None):
        if name:
            self.name = name
        if version:
            self.version = version
        if description:
            self.description = description

    # ---------------------------------------------------------------------
    # Manifest - required
    # ---------------------------------------------------------------------
    @abstractmethod
    def get_manifest(self) -> Dict[str, Any]:
        """
        Return a JSON-serializable manifest describing the plugin and its actions.
        Example:
        {
            "name": "filesystem",
            "version": "1.0.0",
            "actions": {
                "read_file": {"description": "...", "args": {"path": {"type":"string","required":True}}}
            }
        }
        """
        raise NotImplementedError

    # ---------------------------------------------------------------------
    # Async execution - required
    # ---------------------------------------------------------------------
    @abstractmethod
    async def execute_async(self, action: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Primary asynchronous execution entrypoint.
        Return a dict with at least a "status" key: "ok" or "error".
        """
        raise NotImplementedError

    # ---------------------------------------------------------------------
    # Sync wrapper for backward compatibility (threadsafe)
    # ---------------------------------------------------------------------
    def execute(self, action: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Synchronous wrapper that dispatches to execute_async safely from blocking threads.
        Uses asyncio.run() when no event loop exists, otherwise uses
        asyncio.run_coroutine_threadsafe to submit to the running loop.
        """
        if context is None:
            context = {}

        # Ensure required context keys exist; set conservative defaults
        if "user_id" not in context:
            logger.debug("Plugin.execute: context missing 'user_id', setting to 'system'")
            context["user_id"] = context.get("user", "system")
        if "trust_level" not in context:
            logger.debug("Plugin.execute: context missing 'trust_level', setting to 'low'")
            context["trust_level"] = "low"

        coro = self.execute_async(action, args or {}, context)

        # If no running loop, run with asyncio.run (safe)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None or not loop.is_running():
            try:
                return asyncio.run(coro)
            except Exception as e:
                logger.exception("Plugin.execute failed inside asyncio.run")
                return {"status": "error", "error": str(e)}

        # Running event loop present: use run_coroutine_threadsafe to avoid busy-wait
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            # Wait for result (bounded)
            try:
                return future.result(timeout=60)
            except concurrent.futures.TimeoutError:
                future.cancel()
                return {"status": "error", "error": "plugin execution timeout (threadsafe)"}
        except Exception as e:
            logger.exception("Plugin.execute failed in threadsafe submission")
            return {"status": "error", "error": str(e)}

    # ---------------------------------------------------------------------
    # Optional healthcheck
    # ---------------------------------------------------------------------
    def healthcheck(self) -> Dict[str, Any]:
        return {"name": self.name, "version": self.version, "status": "ok"}
