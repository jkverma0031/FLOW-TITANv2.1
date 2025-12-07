# Path: FLOW/titan/augmentation/hostbridge/hostbridge_service.py
from __future__ import annotations
import json
import os
import subprocess
import shlex
import logging
from typing import Dict, Any
from string import Template

from titan.schemas.action import Action, ActionType

# Observability imports
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
    def __init__(self, manifests_dir="titan/augmentation/hostbridge/manifests"):
        self.manifests_dir = manifests_dir
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
        for k in action.args.keys():
            if allowed_args and k not in allowed_args:
                raise HostBridgeError(
                    f"Argument '{k}' is not allowed for module {action.module}"
                )

        allowed_paths = manifest.get("allowed_paths", [])
        for k, v in action.args.items():
            if isinstance(v, str) and (v.startswith("/") or v.startswith("..")):
                if allowed_paths and not _is_path_allowed(v, allowed_paths):
                    raise HostBridgeError(
                        f"Path '{v}' not allowed for module {action.module}"
                    )

    def _safe_format_cmd(self, template: str, args: Dict[str, Any], allowed_args: list) -> str:
        safe_args = {k: str(v) for k, v in args.items() if k in (allowed_args or [])}
        try:
            return template.format(**safe_args)
        except Exception:
            try:
                t = Template(template)
                return t.safe_substitute(**safe_args)
            except Exception as e:
                raise HostBridgeError(f"Failed to render command template safely: {e}")

    def execute(self, action: Action) -> Dict[str, Any]:
        """
        Execute a host-level capability safely.
        Now instrumented with:
        - tracing
        - metrics
        - structured logging
        """
        self.validate(action)
        manifest = self._manifests[action.module]
        cmd_template = manifest["exec"]["cmd"]
        use_shell = manifest["exec"].get("shell", False)
        timeout = manifest["exec"].get("timeout", 10)
        allowed_args = manifest.get("allowed_args", [])

        # Prepare safe final command
        cmd = self._safe_format_cmd(cmd_template, action.args or {}, allowed_args)

        # --- Observability: start ---
        metrics.counter("hostbridge.calls").inc()

        logger.info(
            "HostBridge executing command",
            extra={
                "module": action.module,
                "args": action.args,
                "trace_id": tracer.current_trace_id(),
                "span_id": tracer.current_span_id(),
            },
        )

        with tracer.span(f"hostbridge.{action.module}"):
            with metrics.timer("hostbridge.exec_duration"):

                try:
                    if use_shell:
                        proc = subprocess.Popen(
                            ["/bin/sh", "-lc", cmd],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                    else:
                        proc = subprocess.Popen(
                            shlex.split(cmd),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )

                    out, err = proc.communicate(timeout=timeout)

                    result = {
                        "success": proc.returncode == 0,
                        "stdout": out.decode("utf-8", errors="replace"),
                        "stderr": err.decode("utf-8", errors="replace"),
                        "exit_code": proc.returncode,
                    }

                    logger.info(
                        "HostBridge execution completed",
                        extra={
                            "module": action.module,
                            "success": result["success"],
                            "exit_code": result["exit_code"],
                            "trace_id": tracer.current_trace_id(),
                            "span_id": tracer.current_span_id(),
                        },
                    )
                    return result

                except subprocess.TimeoutExpired:
                    logger.warning(
                        "HostBridge execution timed out",
                        extra={
                            "module": action.module,
                            "timeout": timeout,
                            "trace_id": tracer.current_trace_id(),
                            "span_id": tracer.current_span_id(),
                        },
                    )
                    return {"success": False, "error": "timeout"}

                except Exception as e:
                    logger.exception(
                        "HostBridge execution error",
                        extra={
                            "module": action.module,
                            "trace_id": tracer.current_trace_id(),
                            "span_id": tracer.current_span_id(),
                        },
                    )
                    return {"success": False, "error": str(e)}
