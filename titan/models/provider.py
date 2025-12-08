# titan/models/provider.py
from __future__ import annotations
import asyncio
import logging
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)

class ProviderRouter:
    """
    Simple router for multiple LLM providers.
    Register providers by name and role. Provides:
      - register(name, provider, roles=[])
      - get(name)
      - choose(role="dsl"|"reasoning"|"embed")
      - complete_async(..., provider_name=None, role=None)
      - embed_async(text, provider_name=None)
    Provider objects must implement:
      - complete_async(prompt, tools=None, max_tokens=..., temperature=...)
      - embed_async(text)
    """

    def __init__(self):
        self._providers: Dict[str, Any] = {}
        self._roles: Dict[str, str] = {}  # role -> provider_name
        self._lock = asyncio.Lock()

    async def register(self, name: str, provider: Any, roles: Optional[List[str]] = None, overwrite: bool = False):
        async with self._lock:
            if name in self._providers and not overwrite:
                raise ValueError(f"Provider already registered: {name}")
            self._providers[name] = provider
            if roles:
                for r in roles:
                    self._roles[r] = name
            logger.info("ProviderRouter: registered provider %s (roles=%s)", name, roles)

    def register_sync(self, name: str, provider: Any, roles: Optional[List[str]] = None, overwrite: bool = False):
        """
        Convenience sync wrapper for startup wiring.
        """
        loop = None
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # schedule
                asyncio.run_coroutine_threadsafe(self.register(name, provider, roles, overwrite), loop).result()
                return
        except RuntimeError:
            pass
        # no running loop
        import asyncio as _asyncio
        _asyncio.run(self.register(name, provider, roles, overwrite))

    def get(self, name: str) -> Optional[Any]:
        return self._providers.get(name)

    def choose(self, role: Optional[str] = None) -> Optional[Any]:
        if role and role in self._roles:
            pname = self._roles[role]
            return self._providers.get(pname)
        # fallback: return any provider (first)
        for p in self._providers.values():
            return p
        return None

    async def complete_async(self, prompt: str, *, provider_name: Optional[str] = None, role: Optional[str] = None, tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        provider = None
        if provider_name:
            provider = self._providers.get(provider_name)
        elif role:
            provider = self.choose(role)
        else:
            provider = self.choose(None)
        if provider is None:
            raise RuntimeError("No provider registered")
        # provider must implement complete_async
        if hasattr(provider, "complete_async") and asyncio.iscoroutinefunction(provider.complete_async):
            return await provider.complete_async(prompt, tools=tools, **kwargs)
        # provider might have sync complete (fallback in threadpool)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: provider.complete(prompt, tools=tools, **kwargs))

    async def embed_async(self, text: str, *, provider_name: Optional[str] = None, role: Optional[str] = None) -> Any:
        provider = None
        if provider_name:
            provider = self._providers.get(provider_name)
        elif role:
            provider = self.choose(role)
        else:
            provider = self.choose("embed") or self.choose(None)
        if provider is None:
            raise RuntimeError("No provider registered")
        if hasattr(provider, "embed_async") and asyncio.iscoroutinefunction(provider.embed_async):
            return await provider.embed_async(text)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: provider.embed(text))

    # Sync wrappers (convenience)
    def complete(self, *args, **kwargs) -> Dict[str, Any]:
        coro = self.complete_async(*args, **kwargs)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            pass
        return asyncio.run(coro)

    def embed(self, *args, **kwargs):
        coro = self.embed_async(*args, **kwargs)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            pass
        return asyncio.run(coro)
