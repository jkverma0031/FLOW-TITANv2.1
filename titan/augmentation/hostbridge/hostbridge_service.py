# titan/augmentation/hostbridge/hostbridge_service.py
from __future__ import annotations
import json
import os
import subprocess
import shlex
import logging
import asyncio
import time
from typing import Dict, Any, Optional
from string import Template

from titan.schemas.action import Action, ActionType
from titan.observability.tracing import tracer
from titan.observability.metrics import metrics

logger = logging.getLogger(__name__)

class HostBridgeError(Exception):
    pass

def _is_path_allowed(path: str, allowed_prefixes: list) -> bool:
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    for p in allowed_prefixes:
        p_real = os.path.realpath(p)
        if real.startswith(p_real):
            return True
    return False

class HostBridgeService:
    """
    Async-first HostBridgeService. execute_async uses run_in_executor for blocking subprocess calls.
    A synchronous execute() wrapper is provided for compatibility.
    """

    def __init__(self, manifests_dir="titan/augmentation/hostbridge/manifests", policy_engine: Optional[Any] = None):
        self.manifests_dir = manifests_dir
        self.policy_engine = policy_engine
        self._manifests: Dict[str, Dict[str, Any]] = {}
        os.makedirs(self.manifests_dir, exist_ok=True)
        self._load_manifests()

    def _load_manifests(self):
        for fn in os.listdir(self.manifests_dir):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(self.manifests_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    m = json.load(f)
                    name = m.get("name")
                    if name:
                        self._manifests[name] = m
            except Exception:
                logger.exception("Failed loading hostbridge manifest %s", fn)

    def validate(self, action: Action):
        if action.type != ActionType.HOST:
            raise HostBridgeError("Action not a HOST action")
        manifest = self._manifests.get(action.module)
        if not manifest:
            raise HostBridgeError(f"No manifest for module {action.module}")
        allowed_args = manifest.get("allowed_args", [])
        for k in (action.args or {}).keys():
            if allowed_args and k not in allowed_args:
                raise HostBridgeError(f"Argument '{k}' is not allowed for module {action.module}")
        allowed_paths = manifest.get("allowed_paths", [])
        for k, v in (action.args or {}).items():
            if isinstance(v, str) and (v.startswith("/") or v.startswith("..")):
                if allowed_paths and not _is_path_allowed(v, allowed_paths):
                    raise HostBridgeError(f"Path '{v}' not allowed for module {action.module}")

    def _safe_format_cmd(self, template: str, args: Dict[str, Any], allowed_args: list) -> str:
        safe_args = {k: str(v) for k, v in (args or {}).items() if k in (allowed_args or [])}
        try:
            return template.format(**safe_args)
        except Exception:
            try:
                t = Template(template)
                return t.safe_substitute(**safe_args)
            except Exception as e:
                raise HostBridgeError(f"Failed to render command template safely: {e}")

    async def _policy_check(self, action: Action, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if self.policy_engine is None:
            return None
        try:
            if hasattr(self.policy_engine, "allow_action_async") and asyncio.iscoroutinefunction(self.policy_engine.allow_action_async):
                allowed, reason = await self.policy_engine.allow_action_async(actor=context.get("user_id","system"), trust_level=context.get("trust_level","low"), action="hostbridge.exec", resource={"module": action.module, "command": action.command})
            else:
                loop = asyncio.get_event_loop()
                allowed, reason = await loop.run_in_executor(None, lambda: self.policy_engine.allow_action(context.get("user_id","system"), context.get("trust_level","low"), "hostbridge.exec", {"module": action.module, "command": action.command}))
            if not allowed:
                return {"success": False, "error": f"policy_denied:{reason}"}
        except Exception:
            logger.exception("Policy check failed in hostbridge; allowing by default")
        return None

    def execute(self, action: Action, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # sync wrapper
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(self.execute_async(action, context=context), loop)
                return fut.result()
            else:
                return asyncio.run(self.execute_async(action, context=context))
        except Exception as e:
            logger.exception("HostBridge execute failed")
            return {"success": False, "error": str(e)}

    async def execute_async(self, action: Action, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # policy check
        denied = await self._policy_check(action, context or {})
        if denied is not None:
            return denied

        self.validate(action)
        manifest = self._manifests[action.module]
        cmd_template = manifest["exec"]["cmd"]
        use_shell = manifest["exec"].get("shell", False)
        timeout = manifest["exec"].get("timeout", 10)
        allowed_args = manifest.get("allowed_args", [])

        cmd = self._safe_format_cmd(cmd_template, action.args or {}, allowed_args)

        metrics.counter("hostbridge.calls").inc()
        logger.info("HostBridge executing command", extra={"module": action.module, "args": action.args, "trace_id": tracer.current_trace_id(), "span_id": tracer.current_span_id()})
        start_time = time.time()

        loop = asyncio.get_event_loop()
        try:
            if use_shell:
                proc = await loop.run_in_executor(None, lambda: subprocess.Popen(["/bin/sh","-lc",cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            else:
                proc = await loop.run_in_executor(None, lambda: subprocess.Popen(shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            try:
                stdout, stderr = await loop.run_in_executor(None, lambda: proc.communicate(timeout=timeout))
                exit_code = proc.returncode
                stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
                stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
                return {"success": exit_code == 0, "stdout": stdout_text, "stderr": stderr_text, "exit_code": exit_code, "duration": time.time() - start_time}
            except subprocess.TimeoutExpired:
                proc.kill()
                return {"success": False, "error": "timeout", "stderr": "timeout", "stdout": "", "exit_code": -1, "duration": time.time() - start_time}
        except Exception as e:
            logger.exception("HostBridge execution error")
            return {"success": False, "error": str(e), "stdout": "", "stderr": str(e), "exit_code": -1, "duration": time.time() - start_time}
