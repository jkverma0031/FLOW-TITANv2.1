# Path: titan/memory/embeddings.py
from __future__ import annotations
import hashlib
import logging
from typing import List, Optional, Iterable, Any
import asyncio

logger = logging.getLogger(__name__)

# Optional backends (same as original)
_HAS_SENTENCE_TRANSFORMERS = False
_HAS_OPENAI = False
try:
    from sentence_transformers import SentenceTransformer  # type: ignore
    _HAS_SENTENCE_TRANSFORMERS = True
except Exception:
    _HAS_SENTENCE_TRANSFORMERS = False

try:
    import openai  # type: ignore
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False


class Embedder:
    """
    UPGRADED Embedder (backward compatible):

    Supports:
      • embed_text(text)                  → sync
      • embed_batch(texts)                → sync
      • embed_async(text)                 → async
      • embed_batch_async(texts)          → async
      • Automatic delegation to LLM provider (ProviderRouter, GroqProvider, etc.)
      • Optional backends (sentence-transformers, OpenAI SDK)
      • Deterministic fallback (unchanged)

    'provider' can be:
      • ProviderRouter
      • Any provider implementing embed_async or embed
      • None (falls back to original behavior)
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        model_name: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        provider: Optional[Any] = None,      # NEW: LLM embedding provider
    ):
        self.backend = backend
        self.model_name = model_name or "all-MiniLM-L6-v2"
        self.provider = provider             # NEW: ProviderRouter or provider instance
        self._sent_model = None

        # ---- ORIGINAL BACKEND SELECTION LOGIC (unchanged) ----
        if backend is None:
            if provider:
                # If LLM provider exists, use provider-first embedding approach.
                self.backend = "provider"
            elif _HAS_SENTENCE_TRANSFORMERS:
                self.backend = "sentence-transformers"
            elif _HAS_OPENAI:
                self.backend = "openai"
            else:
                self.backend = "fallback"

        if self.backend == "sentence-transformers" and _HAS_SENTENCE_TRANSFORMERS:
            try:
                self._sent_model = SentenceTransformer(self.model_name)
                logger.info("Embedder using sentence-transformers %s", self.model_name)
            except Exception:
                logger.exception("Failed to load sentence-transformers; falling back")
                self.backend = "fallback"

        if self.backend == "openai":
            if not _HAS_OPENAI:
                logger.warning("OpenAI SDK not available; falling back to deterministic")
                self.backend = "fallback"
            else:
                if openai_api_key:
                    openai.api_key = openai_api_key
                logger.info("Embedder using OpenAI embeddings")

    # -----------------------------------------------------------
    #  ASYNC EMBEDDING API (NEW)
    # -----------------------------------------------------------
    async def embed_async(self, text: str) -> List[float]:
        """
        Fully async embedding method.
        Priority:
          1) ProviderRouter or provider with embed_async(text)
          2) SentenceTransformers in threadpool (CPU-bound)
          3) OpenAI embedding via threadpool (network-bound)
          4) Deterministic fallback
        """
        # ----- Provider-based embedding -----
        if self.provider:
            try:
                # ProviderRouter or provider implementing embed_async
                if hasattr(self.provider, "embed_async") and asyncio.iscoroutinefunction(self.provider.embed_async):
                    return await self.provider.embed_async(text)

                # Provider has sync embedding → run in threadpool
                if hasattr(self.provider, "embed"):
                    loop = asyncio.get_event_loop()
                    return await loop.run_in_executor(None, lambda: self.provider.embed(text))
            except Exception:
                logger.exception("Embedder: provider embedding failed; falling back")

        # ----- sentence-transformers (sync → threadpool) -----
        if self.backend == "sentence-transformers" and self._sent_model:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._sent_model.encode([text], show_progress_bar=False)[0].tolist(),
            )

        # ----- OpenAI fallback (sync → threadpool) -----
        if self.backend == "openai":
            async def _openai_call():
                try:
                    resp = openai.Embedding.create(model=self.model_name, input=text)
                    return list(resp["data"][0]["embedding"])
                except Exception:
                    logger.exception("OpenAI embed failed; falling back")
                    return None

            loop = asyncio.get_event_loop()
            vec = await loop.run_in_executor(None, _openai_call)
            if vec is not None:
                return vec

        # ----- deterministic fallback -----
        return self._fallback_embed(text)

    async def embed_batch_async(self, texts: Iterable[str]) -> List[List[float]]:
        """
        Fully async batch embedding.
        """
        return [await self.embed_async(t) for t in texts]

    # -----------------------------------------------------------
    #  SYNC API (BACKWARD COMPATIBLE, UNCHANGED SIGNATURES)
    # -----------------------------------------------------------
    def embed_text(self, text: str) -> List[float]:
        """
        Original synchronous API.
        Internally runs async method for provider support.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(self.embed_async(text), loop).result()
        except RuntimeError:
            pass  # no running loop

        return asyncio.run(self.embed_async(text))

    def embed_batch(self, texts: Iterable[str]) -> List[List[float]]:
        """
        Original synchronous batch embedding.
        """
        return [self.embed_text(t) for t in texts]

    # -----------------------------------------------------------
    #  ORIGINAL DETERMINISTIC FALLBACK (UNCHANGED)
    # -----------------------------------------------------------
    def _fallback_embed(self, text: str, dim: int = 128) -> List[float]:
        """
        100% deterministic embedding based on SHA256 — identical to original.
        """
        h = hashlib.sha256(text.encode("utf-8")).digest()
        out = []
        i = 0
        while len(out) < dim:
            chunk = hashlib.sha256(h + bytes([i])).digest()
            for j in range(0, len(chunk), 8):
                if len(out) >= dim:
                    break
                v = int.from_bytes(chunk[j:j+8], "big", signed=False)
                out.append((v % 10**8) / 10**8)
            i += 1
        return out[:dim]

    def health(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model_name,
            "provider_enabled": bool(self.provider),
        }
