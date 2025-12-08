# titan/runtime/plugins/browser_plugin.py
from __future__ import annotations
import asyncio
import logging
import json
import time
import os
from typing import Dict, Any, Optional, List

# Base plugin import
try:
    from titan.runtime.plugins.base import BasePlugin, PluginError
except Exception:
    from abc import ABC, abstractmethod
    class PluginError(Exception): pass
    class BasePlugin(ABC):
        def get_manifest(self) -> Dict[str, Any]:
            raise NotImplementedError
        async def execute_async(self, action: str, args: Dict[str,Any], context: Optional[Dict]=None) -> Dict[str,Any]:
            raise NotImplementedError
        def execute(self, action, args, context=None):
            return asyncio.get_event_loop().run_until_complete(self.execute_async(action, args, context))

# Playwright imports (async)
_HAS_PLAYWRIGHT = False
try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    _HAS_PLAYWRIGHT = True
except Exception:
    _HAS_PLAYWRIGHT = False

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class BrowserPlugin(BasePlugin):
    """
    Browser automation plugin using Playwright (async).
    Actions:
      - open(url)
      - goto(page_id or 'default', url)
      - new_page(name)
      - click(selector, page='default')
      - type(selector, text, page='default')
      - screenshot(selector=None, path=None, page='default')
      - evaluate(selector, expression, page='default')  -> returns evaluated JS value
      - extract_text(selector, page='default')
      - close_page(name)
      - list_pages()
      - wait_for_selector(selector, timeout)
    Note: Playwright must be installed and `playwright install` executed.
    """

    name = "browser"
    version = "1.0.0"

    def __init__(self, headless: bool = True, *, loop: Optional[asyncio.AbstractEventLoop] = None, default_storage_dir: Optional[str] = None):
        self.loop = loop or asyncio.get_event_loop()
        self.headless = bool(headless)
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._pages: Dict[str, Page] = {}
        self._storage_dir = default_storage_dir or os.path.join(os.getcwd(), ".titan_browser_profiles")
        os.makedirs(self._storage_dir, exist_ok=True)
        if not _HAS_PLAYWRIGHT:
            logger.warning("playwright not available; BrowserPlugin disabled until playwright is installed.")

    def get_manifest(self) -> Dict[str, Any]:
        return {
            "name": "browser",
            "version": self.version,
            "description": "Browser automation using Playwright.",
            "actions": {
                "open": {"args": {"url": {"type": "string"}}},
                "new_page": {"args": {"name": {"type": "string"}}},
                "goto": {"args": {"page": {"type": "string", "default": "default"}, "url": {"type": "string"}}},
                "click": {"args": {"page": {"type": "string", "default": "default"}, "selector": {"type": "string"}}},
                "type": {"args": {"page": {"type": "string", "default": "default"}, "selector": {"type": "string"}, "text": {"type": "string"}}},
                "screenshot": {"args": {"page": {"type": "string", "default": "default"}, "selector": {"type": ["string","null"]}, "path": {"type": ["string","null"]}}},
                "extract_text": {"args": {"page": {"type": "string", "default": "default"}, "selector": {"type": "string"}}},
                "evaluate": {"args": {"page": {"type": "string", "default": "default"}, "expression": {"type": "string"}}},
                "close_page": {"args": {"page": {"type": "string"}}},
                "list_pages": {"args": {}},
                "wait_for_selector": {"args": {"page": {"type": "string", "default": "default"}, "selector": {"type": "string"}, "timeout": {"type": "int", "default": 5000}}}
            }
        }

    # -------------------------
    # Lifecycle helpers
    # -------------------------
    async def _ensure_playwright(self):
        if not _HAS_PLAYWRIGHT:
            raise PluginError("Playwright not installed (pip install playwright && playwright install)")
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None:
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
        if self._context is None:
            self._context = await self._browser.new_context()
        # ensure default page
        if "default" not in self._pages:
            p = await self._context.new_page()
            self._pages["default"] = p

    async def _cleanup_playwright(self):
        try:
            for p in list(self._pages.values()):
                try:
                    await p.close()
                except Exception:
                    pass
            self._pages.clear()
            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    pass
                self._context = None
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
        except Exception:
            logger.exception("BrowserPlugin cleanup failed")

    async def execute_async(self, action: str, args: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ctx = context or {}
        try:
            # Map to method
            fn = getattr(self, f"_action_{action}", None)
            if fn is None:
                return {"success": False, "error": f"Unknown browser action '{action}'"}
            await self._ensure_playwright()
            result = await fn(args, ctx)
            return result
        except PluginError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("BrowserPlugin action failed")
            return {"success": False, "error": str(e)}

    # -------------------------
    # Actions (async)
    # -------------------------
    async def _action_open(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        url = args.get("url")
        if not url:
            return {"success": False, "error": "url required"}
        try:
            page = self._pages.get("default")
            if not page:
                page = await self._context.new_page()
                self._pages["default"] = page
            await page.goto(url)
            return {"success": True}
        except Exception as e:
            logger.exception("open failed")
            return {"success": False, "error": str(e)}

    async def _action_new_page(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        name = args.get("name")
        if not name:
            return {"success": False, "error": "name required"}
        try:
            p = await self._context.new_page()
            self._pages[name] = p
            return {"success": True, "page": name}
        except Exception as e:
            logger.exception("new_page failed")
            return {"success": False, "error": str(e)}

    async def _get_page(self, page_name: str = "default"):
        p = self._pages.get(page_name)
        if p is None:
            # try creating a new page with that name
            p = await self._context.new_page()
            self._pages[page_name] = p
        return p

    async def _action_goto(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        url = args.get("url")
        if not url:
            return {"success": False, "error": "url required"}
        try:
            p = await self._get_page(page)
            await p.goto(url)
            return {"success": True}
        except Exception as e:
            logger.exception("goto failed")
            return {"success": False, "error": str(e)}

    async def _action_click(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        selector = args.get("selector")
        if not selector:
            return {"success": False, "error": "selector required"}
        try:
            p = await self._get_page(page)
            await p.click(selector)
            return {"success": True}
        except Exception as e:
            logger.exception("click failed")
            return {"success": False, "error": str(e)}

    async def _action_type(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        selector = args.get("selector")
        text = args.get("text", "")
        if not selector:
            return {"success": False, "error": "selector required"}
        try:
            p = await self._get_page(page)
            await p.fill(selector, text)
            return {"success": True}
        except Exception as e:
            logger.exception("type failed")
            return {"success": False, "error": str(e)}

    async def _action_screenshot(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        selector = args.get("selector")
        path = args.get("path")
        if not path:
            # store in storage dir
            path = os.path.join(self._storage_dir, f"page_screenshot_{int(time.time()*1000)}.png")
        try:
            p = await self._get_page(page)
            if selector:
                el = await p.query_selector(selector)
                if not el:
                    return {"success": False, "error": "selector not found"}
                await el.screenshot(path=path)
            else:
                await p.screenshot(path=path, full_page=True)
            return {"success": True, "path": path}
        except Exception as e:
            logger.exception("screenshot failed")
            return {"success": False, "error": str(e)}

    async def _action_extract_text(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        selector = args.get("selector")
        if not selector:
            return {"success": False, "error": "selector required"}
        try:
            p = await self._get_page(page)
            el = await p.query_selector(selector)
            if not el:
                return {"success": False, "error": "selector not found"}
            txt = await el.inner_text()
            return {"success": True, "text": txt}
        except Exception as e:
            logger.exception("extract_text failed")
            return {"success": False, "error": str(e)}

    async def _action_evaluate(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        expr = args.get("expression")
        if not expr:
            return {"success": False, "error": "expression required"}
        try:
            p = await self._get_page(page)
            res = await p.evaluate(expr)
            return {"success": True, "result": res}
        except Exception as e:
            logger.exception("evaluate failed")
            return {"success": False, "error": str(e)}

    async def _action_close_page(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page")
        if not page:
            return {"success": False, "error": "page required"}
        try:
            p = self._pages.get(page)
            if not p:
                return {"success": False, "error": "page not found"}
            await p.close()
            del self._pages[page]
            return {"success": True}
        except Exception as e:
            logger.exception("close_page failed")
            return {"success": False, "error": str(e)}

    async def _action_list_pages(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return {"success": True, "pages": list(self._pages.keys())}
        except Exception as e:
            logger.exception("list_pages failed")
            return {"success": False, "error": str(e)}

    async def _action_wait_for_selector(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        page = args.get("page", "default")
        selector = args.get("selector")
        timeout = int(args.get("timeout", 5000))
        if not selector:
            return {"success": False, "error": "selector required"}
        try:
            p = await self._get_page(page)
            await p.wait_for_selector(selector, timeout=timeout)
            return {"success": True}
        except Exception as e:
            logger.exception("wait_for_selector failed")
            return {"success": False, "error": str(e)}

    # -------------------------
    # Cleanup on interpreter exit
    # -------------------------
    async def __aexit__(self, exc_type, exc, tb):
        await self._cleanup_playwright()

    async def __aenter__(self):
        await self._ensure_playwright()
        return self

    async def cleanup(self):
        await self._cleanup_playwright()
