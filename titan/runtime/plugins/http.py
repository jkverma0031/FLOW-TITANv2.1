# titan/runtime/plugins/http.py
from __future__ import annotations
from typing import Dict, Any, Optional
import logging
import httpx
import asyncio

from .base import BasePlugin, PluginError

logger = logging.getLogger(__name__)

class HTTPPlugin(BasePlugin):
    """
    Async HTTP client plugin.
    Actions:
      - get: args {url, params?, headers?}
      - post: args {url, json?, data?, headers?}
    """

    def __init__(self, *, default_timeout: int = 10, **kwargs):
        super().__init__(name="http", version="1.0.0", description="Async HTTP client plugin")
        self.default_timeout = default_timeout

    def get_manifest(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "actions": {
                "get": {"description": "HTTP GET", "args": {"url": {"type": "string", "required": True}, "params": {"type": "object"}, "headers": {"type": "object"}}},
                "post": {"description": "HTTP POST", "args": {"url": {"type": "string", "required": True}, "json": {"type": "object"}, "data": {"type": "object"}, "headers": {"type": "object"}}}
            }
        }

    async def execute_async(self, action: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if context is None:
            context = {}
        if "user_id" not in context:
            context["user_id"] = "system"
        if "trust_level" not in context:
            context["trust_level"] = "low"

        try:
            if action == "get":
                return await self._get(args)
            if action == "post":
                return await self._post(args)
            raise PluginError(f"Unsupported HTTP action: {action}")
        except PluginError as pe:
            logger.warning("HTTPPlugin error: %s", pe)
            return {"status": "error", "error": str(pe)}
        except Exception as e:
            logger.exception("HTTPPlugin unexpected exception")
            return {"status": "error", "error": str(e)}

    async def _get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        url = args.get("url")
        if not url:
            raise PluginError("get requires 'url'")
        params = args.get("params")
        headers = args.get("headers")
        timeout = args.get("timeout", self.default_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
            return {"status": "ok", "result": self._format_response(resp)}

    async def _post(self, args: Dict[str, Any]) -> Dict[str, Any]:
        url = args.get("url")
        if not url:
            raise PluginError("post requires 'url'")
        headers = args.get("headers")
        timeout = args.get("timeout", self.default_timeout)
        json_payload = args.get("json")
        data = args.get("data")
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=json_payload, data=data, headers=headers)
            return {"status": "ok", "result": self._format_response(resp)}

    def _format_response(self, resp: httpx.Response) -> Dict[str, Any]:
        j = None
        text = None
        try:
            j = resp.json()
        except Exception:
            try:
                text = resp.text
            except Exception:
                text = None
        if text is None and j is not None:
            import json as _json
            text = _json.dumps(j)
        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "json": j,
            "text": text,
        }
