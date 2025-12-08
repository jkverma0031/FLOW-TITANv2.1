# titan/models/groq_provider.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Dict, Any, List, Sequence
import json

try:
    import httpx
except Exception:
    httpx = None

logger = logging.getLogger(__name__)


class GroqProviderError(Exception):
    pass


class GroqProvider:
    """
    Async Groq provider wrapper.
    - complete_async(prompt, tools=None, temperature=0.0) -> dict / text
    - embed_async(text) -> List[float]

    This is written to be robust (falls back to sync if httpx is missing).
    Configure with api_url and api_key (or use env-driven).
    """

    def __init__(self, api_url: str, api_key: Optional[str] = None, model: str = "groq-alpha", timeout: int = 30):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    # ----------------------------
    # Completions
    # ----------------------------
    async def complete_async(self, prompt: str, *, tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 1024, temperature: float = 0.0, stop: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """
        Call the Groq completion API in an async fashion.
        The exact payload depends on the Groq API; we use a generic JSON body:
        {
            "model": self.model,
            "input": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools   # optional manifest/tool schemas
        }
        The function attempts to return a dict with keys: "text", "raw"
        """
        payload = {
            "model": self.model,
            "input": prompt,
            "max_tokens": max_tokens,
            "temperature": float(temperature),
        }
        if tools:
            payload["tools"] = tools
        if stop:
            payload["stop"] = list(stop)

        logger.debug("GroqProvider.complete_async: calling Groq model %s (tokens=%d)", self.model, max_tokens)

        if httpx is None:
            # fallback: synchronous blocking request using standard library (very rare)
            import requests
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            resp = requests.post(self.api_url + "/v1/completions", json=payload, headers=headers, timeout=self.timeout)
            if resp.status_code >= 400:
                raise GroqProviderError(f"Groq API error: {resp.status_code} {resp.text}")
            data = resp.json()
            return {"text": data.get("output") or data.get("text") or "", "raw": data}
        # async httpx
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            try:
                r = await client.post(self.api_url + "/v1/completions", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                return {"text": data.get("output") or data.get("text") or (data.get("choices") and data["choices"][0].get("text")), "raw": data}
            except Exception as e:
                logger.exception("GroqProvider.complete_async failed")
                raise GroqProviderError(str(e))

    def complete(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Sync wrapper around complete_async for compatibility.
        If called inside an event loop, uses run_coroutine_threadsafe.
        """
        coro = self.complete_async(*args, **kwargs)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None or not loop.is_running():
            return asyncio.run(coro)
        else:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result()

    # ----------------------------
    # Embeddings
    # ----------------------------
    async def embed_async(self, text: str) -> List[float]:
        """
        Ask Groq API for an embedding vector. Many LLM providers use a separate endpoint.
        Payload:
          {"model": "<embedding-model>", "input": text}
        Returns a list of floats.
        """
        payload = {"model": f"{self.model}-embed", "input": text}
        logger.debug("GroqProvider.embed_async: requesting embedding (len=%d)", len(text))
        if httpx is None:
            import requests
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            resp = requests.post(self.api_url + "/v1/embeddings", json=payload, headers=headers, timeout=self.timeout)
            if resp.status_code >= 400:
                raise GroqProviderError(f"Groq embedding error: {resp.status_code} {resp.text}")
            data = resp.json()
            # expected: {"embedding": [...] } or {"data":[{"embedding": [...]}]}
            if "embedding" in data:
                return data["embedding"]
            if "data" in data and isinstance(data["data"], list) and "embedding" in data["data"][0]:
                return data["data"][0]["embedding"]
            raise GroqProviderError("unexpected embedding response shape")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            try:
                r = await client.post(self.api_url + "/v1/embeddings", json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                if "embedding" in data:
                    return data["embedding"]
                if "data" in data and isinstance(data["data"], list) and "embedding" in data["data"][0]:
                    return data["data"][0]["embedding"]
                raise GroqProviderError("unexpected embedding response shape")
            except Exception as e:
                logger.exception("GroqProvider.embed_async failed")
                raise GroqProviderError(str(e))

    def embed(self, *args, **kwargs) -> List[float]:
        coro = self.embed_async(*args, **kwargs)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None or not loop.is_running():
            return asyncio.run(coro)
        else:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result()
