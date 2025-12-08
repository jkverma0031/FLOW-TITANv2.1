# titan/runtime/plugins/desktop_plugin.py
from __future__ import annotations
import asyncio
import logging
import tempfile
import os
from typing import Dict, Any, Optional
from abc import ABC

# Local plugin base import — adjust path if your project places it elsewhere
try:
    from titan.runtime.plugins.base import BasePlugin, PluginError
except Exception:
    # Best-effort fallback: provide a minimal BasePlugin if import fails (so file loads in tests)
    from abc import ABC, abstractmethod
    class PluginError(Exception): pass
    class BasePlugin(ABC):
        def get_manifest(self) -> Dict[str, Any]:
            raise NotImplementedError
        async def execute_async(self, action: str, args: Dict[str, Any], context: Optional[Dict[str,Any]] = None) -> Dict[str,Any]:
            raise NotImplementedError
        def execute(self, action, args, context=None):
            # naive wrapper
            return asyncio.get_event_loop().run_until_complete(self.execute_async(action, args, context))

# Third-party imports (pyautogui is synchronous)
try:
    import pyautogui
    _HAS_PYA = True
except Exception:
    _HAS_PYA = False

# Optional window control
try:
    import pygetwindow as gw
    _HAS_PYGET = True
except Exception:
    _HAS_PYGET = False

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DesktopPlugin(BasePlugin):
    """
    DesktopAutomation plugin (async wrapper over pyautogui).
    Actions:
      - move_mouse(x:int, y:int, duration:float=0.0)
      - click(x:int=None, y:int=None, button:str="left", clicks:int=1, interval:float=0.0)
      - double_click(...)
      - right_click(...)
      - type_text(text:str, interval:float=0.0)
      - press_key(key:str)
      - hotkey(keys:List[str])
      - screenshot(path:Optional[str]=None, region:Optional[tuple]=None)
      - open_app(path_or_command:str)  (uses os.startfile / subprocess)
      - list_windows() -> list window titles (if pygetwindow available)
      - focus_window(title_substring:str)
    Manifest: returned by get_manifest() describing actions + arg schemas.
    """

    name = "desktop"
    version = "1.0.0"

    def __init__(self, sandbox_dir: Optional[str] = None, *, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.loop = loop or asyncio.get_event_loop()
        self.sandbox_dir = sandbox_dir or tempfile.gettempdir()
        self._ensure_safety_dir()
        if not _HAS_PYA:
            logger.warning("pyautogui not available. DesktopPlugin will be disabled until pyautogui is installed.")

    def _ensure_safety_dir(self):
        try:
            os.makedirs(self.sandbox_dir, exist_ok=True)
        except Exception:
            pass

    def get_manifest(self) -> Dict[str, Any]:
        """Return JSON-like manifest for LLM planner / function-calling."""
        return {
            "name": "desktop",
            "version": self.version,
            "description": "Desktop automation (mouse/keyboard/app control, screenshots).",
            "actions": {
                "move_mouse": {"args": {"x": {"type": "int"}, "y": {"type": "int"}, "duration": {"type": "float", "default": 0.0}}},
                "click": {"args": {"x": {"type": ["int","null"]}, "y": {"type": ["int","null"]}, "button": {"type": "string", "default": "left"}, "clicks": {"type": "int", "default": 1}}},
                "type_text": {"args": {"text": {"type": "string"}, "interval": {"type": "float", "default": 0.0}}},
                "press_key": {"args": {"key": {"type": "string"}}},
                "hotkey": {"args": {"keys": {"type": "array"}}},
                "screenshot": {"args": {"path": {"type": ["string","null"]}, "region": {"type": ["array","null"], "description": "[x,y,w,h]"}}},
                "open_app": {"args": {"command": {"type": "string"}}},
                "list_windows": {"args": {}},
                "focus_window": {"args": {"title_substring": {"type": "string"}}}
            }
        }

    async def execute_async(self, action: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Async entrypoint. Runs blocking pyautogui calls in a threadpool to avoid blocking event loop.
        """
        ctx = context or {}
        # Basic policy/trust gating (example): require at least 'low' trust. Higher-risk commands can be gated externally.
        trust = ctx.get("trust_level", "low")
        user = ctx.get("user_id", "system")
        logger.debug("DesktopPlugin.execute_async action=%s user=%s trust=%s", action, user, trust)

        # Map actions to methods
        fn = getattr(self, f"_action_{action}", None)
        if fn is None:
            return {"success": False, "error": f"Unknown action '{action}'"}

        # Run fn in executor if it is blocking
        try:
            if asyncio.iscoroutinefunction(fn):
                return await fn(args, ctx)
            else:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: fn(args, ctx))
        except Exception as e:
            logger.exception("DesktopPlugin action failed")
            return {"success": False, "error": str(e)}

    # -----------------------
    # Action implementations (blocking)
    # -----------------------
    def _action_move_mouse(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYA:
            return {"success": False, "error": "pyautogui not installed"}
        x = int(args.get("x"))
        y = int(args.get("y"))
        duration = float(args.get("duration", 0.0))
        pyautogui.moveTo(x, y, duration=duration)
        return {"success": True}

    def _action_click(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYA:
            return {"success": False, "error": "pyautogui not installed"}
        x = args.get("x", None)
        y = args.get("y", None)
        button = args.get("button", "left")
        clicks = int(args.get("clicks", 1))
        interval = float(args.get("interval", 0.0)) if args.get("interval") is not None else 0.0
        if x is None or y is None:
            pyautogui.click(button=button, clicks=clicks, interval=interval)
        else:
            pyautogui.click(x=int(x), y=int(y), button=button, clicks=clicks, interval=interval)
        return {"success": True}

    def _action_type_text(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYA:
            return {"success": False, "error": "pyautogui not installed"}
        text = str(args.get("text", ""))
        interval = float(args.get("interval", 0.0))
        pyautogui.write(text, interval=interval)
        return {"success": True}

    def _action_press_key(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYA:
            return {"success": False, "error": "pyautogui not installed"}
        key = str(args.get("key"))
        pyautogui.press(key)
        return {"success": True}

    def _action_hotkey(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYA:
            return {"success": False, "error": "pyautogui not installed"}
        keys = args.get("keys", [])
        if not isinstance(keys, (list, tuple)):
            return {"success": False, "error": "keys must be a list"}
        pyautogui.hotkey(*keys)
        return {"success": True}

    def _safe_path(self, path: str) -> str:
        # ensure screenshot path is within sandbox_dir
        try:
            # if relative path, join with sandbox_dir
            if not os.path.isabs(path):
                path = os.path.join(self.sandbox_dir, path)
            # normalize
            norm = os.path.abspath(path)
            if not norm.startswith(os.path.abspath(self.sandbox_dir)):
                raise PluginError("Path outside sandbox")
            # ensure parent exists
            os.makedirs(os.path.dirname(norm), exist_ok=True)
            return norm
        except Exception as e:
            raise PluginError(str(e))

    def _action_screenshot(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYA:
            return {"success": False, "error": "pyautogui not installed"}

        path = args.get("path")
        region = args.get("region")  # [x,y,w,h]
        if path:
            out = self._safe_path(path)
        else:
            out = os.path.join(self.sandbox_dir, f"screenshot_{int(asyncio.get_event_loop().time()*1000)}.png")
        try:
            if region and isinstance(region, (list, tuple)) and len(region) == 4:
                im = pyautogui.screenshot(region=tuple(region))
            else:
                im = pyautogui.screenshot()
            im.save(out)
            return {"success": True, "path": out}
        except Exception as e:
            logger.exception("screenshot failed")
            return {"success": False, "error": str(e)}

    def _action_open_app(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        cmd = args.get("command")
        if not cmd:
            return {"success": False, "error": "no command provided"}
        try:
            # platform-specific safe launching
            if os.name == "nt":
                os.startfile(cmd)
            else:
                # try using xdg-open or open for mac
                import subprocess
                if os.path.isdir(cmd) or os.path.isfile(cmd):
                    subprocess.Popen([cmd])
                else:
                    # assume a shell command
                    subprocess.Popen(cmd, shell=True)
            return {"success": True}
        except Exception as e:
            logger.exception("open_app failed")
            return {"success": False, "error": str(e)}

    def _action_list_windows(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYGET:
            return {"success": False, "error": "pygetwindow not installed"}
        try:
            wins = gw.getAllTitles()
            return {"success": True, "windows": wins}
        except Exception as e:
            logger.exception("list_windows failed")
            return {"success": False, "error": str(e)}

    def _action_focus_window(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        if not _HAS_PYGET:
            return {"success": False, "error": "pygetwindow not installed"}
        title_sub = str(args.get("title_substring", ""))
        try:
            wins = gw.getWindowsWithTitle(title_sub)
            if not wins:
                return {"success": False, "error": "no window matched"}
            w = wins[0]
            w.activate()
            return {"success": True, "title": w.title}
        except Exception as e:
            logger.exception("focus_window failed")
            return {"success": False, "error": str(e)}
