# titan/runtime/plugins/filesystem.py
from __future__ import annotations
from typing import Dict, Any, Optional
from pathlib import Path
import logging
import shutil
import aiofiles
import asyncio

from .base import BasePlugin, PluginError

logger = logging.getLogger(__name__)

class FilesystemPlugin(BasePlugin):
    """
    Async-first Filesystem plugin confined to a sandbox directory.

    Supported actions:
      - read_file
      - write_file
      - append_file
      - list_dir
      - delete
      - make_dir
    """

    def __init__(self, sandbox_dir: Optional[str] = None, **kwargs):
        super().__init__(name="filesystem", version="1.1.0", description="Async safe filesystem plugin")
        self.sandbox_dir = Path(sandbox_dir) if sandbox_dir else Path("/tmp/titan_workspace")
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)

    def get_manifest(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "actions": {
                "read_file": {
                    "description": "Read a file from the sandbox",
                    "args": {
                        "path": {"type": "string", "required": True},
                        "mode": {"type": "string", "required": False, "default": "r"}
                    }
                },
                "write_file": {
                    "description": "Write file to sandbox",
                    "args": {
                        "path": {"type": "string", "required": True},
                        "content": {"type": "string", "required": True},
                        "mode": {"type": "string", "required": False, "default": "w"}
                    }
                },
                "list_dir": {
                    "description": "List directory content",
                    "args": {
                        "path": {"type": "string", "required": False, "default": "."},
                        "recursive": {"type": "boolean", "required": False, "default": False}
                    }
                }
            }
        }

    # -----------------------------
    # Path Safety
    # -----------------------------
    def _resolve(self, user_path: str) -> Path:
        if user_path is None:
            raise PluginError("Path must be provided")
        candidate = (self.sandbox_dir / user_path).resolve()
        try:
            candidate.relative_to(self.sandbox_dir.resolve())
        except Exception:
            raise PluginError(f"Path traversal detected or outside sandbox: {user_path}")
        return candidate

    # -----------------------------
    # Async dispatcher
    # -----------------------------
    async def execute_async(self, action: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        context.setdefault("user_id", "system")
        context.setdefault("trust_level", "low")

        logger.debug("FilesystemPlugin.execute_async action=%s user=%s", action, context.get("user_id"))

        try:
            if action == "read_file":
                return await self._read_file(args)
            if action == "write_file":
                return await self._write_file(args)
            if action == "append_file":
                return await self._append_file(args)
            if action == "list_dir":
                return await self._list_dir(args)
            if action == "delete":
                return await self._delete(args)
            if action == "make_dir":
                return await self._make_dir(args)
            raise PluginError(f"Unsupported action: {action}")

        except PluginError as pe:
            logger.warning("FilesystemPlugin error: %s", pe)
            return {"status": "error", "error": str(pe)}
        except Exception as e:
            logger.exception("FilesystemPlugin unexpected exception")
            return {"status": "error", "error": str(e)}

    # -----------------------------
    # Operations
    # -----------------------------

    async def _read_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = self._resolve(args.get("path"))
        mode = args.get("mode", "r")
        if not p.exists():
            raise PluginError(f"File not found: {p}")

        if "b" in mode:
            async with aiofiles.open(p, "rb") as fh:
                data = await fh.read()
            return {"status": "ok", "result": data}

        async with aiofiles.open(p, "r", encoding=args.get("encoding", "utf-8")) as fh:
            data = await fh.read()
        return {"status": "ok", "result": data}

    async def _write_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = self._resolve(args.get("path"))
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = args.get("mode", "w")
        content = args.get("content", "")

        if "b" in mode:
            if isinstance(content, str):
                content = content.encode(args.get("encoding", "utf-8"))
            async with aiofiles.open(p, "wb") as fh:
                await fh.write(content)
        else:
            async with aiofiles.open(p, "w", encoding=args.get("encoding", "utf-8")) as fh:
                await fh.write(str(content))

        return {"status": "ok", "result": {"path": str(p)}}

    async def _append_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = self._resolve(args.get("path"))
        content = args.get("content", "")
        p.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(p, "a", encoding=args.get("encoding", "utf-8")) as fh:
            await fh.write(str(content))

        return {"status": "ok", "result": {"path": str(p)}}

    # -----------------------------
    # FIXED: async-safe recursive listing
    # -----------------------------
    async def _list_dir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = args.get("path", ".")
        recursive = bool(args.get("recursive", False))
        p = self._resolve(path)

        if not p.exists():
            raise PluginError(f"Path not found: {path}")

        loop = asyncio.get_event_loop()

        if recursive:
            # PREVIOUSLY BLOCKING: p.rglob("*")  ❌
            items_list = await loop.run_in_executor(
                None,
                lambda: list(p.rglob("*"))
            )
        else:
            items_list = await loop.run_in_executor(
                None,
                lambda: sorted(p.iterdir())
            )

        items = [
            {
                "path": str(sub.relative_to(self.sandbox_dir)),
                "is_dir": sub.is_dir()
            }
            for sub in items_list
        ]

        return {"status": "ok", "result": items}

    async def _delete(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = self._resolve(args.get("path"))
        if not p.exists():
            raise PluginError(f"Path not found: {p}")

        loop = asyncio.get_event_loop()
        if p.is_dir():
            await loop.run_in_executor(None, shutil.rmtree, p)
        else:
            await loop.run_in_executor(None, p.unlink)

        return {"status": "ok", "result": {"deleted": str(p)}}

    async def _make_dir(self, args: Dict[str, Any]) -> Dict[str, Any]:
        p = self._resolve(args.get("path"))
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, p.mkdir, True, True)
        return {"status": "ok", "result": {"path": str(p)}}
