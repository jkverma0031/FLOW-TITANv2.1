# Path: titan/memory/embeddings.py
from __future__ import annotations
import hashlib
import logging
from typing import List, Optional, Iterable

logger = logging.getLogger(__name__)

# Try optional backends
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
    Pluggable embedder.
    Methods:
      - embed_text(text: str) -> List[float]
      - embed_batch(texts: Iterable[str]) -> List[List[float]]
    Backends:
      - sentence-transformers (if available & configured)
      - openai (if configured)
      - deterministic fallback (fast, reproducible)
    """

    def __init__(self, backend: Optional[str] = None, model_name: Optional[str] = None, openai_api_key: Optional[str] = None):
        self.backend = backend
        self.model_name = model_name or "all-MiniLM-L6-v2"
        self._sent_model = None
        if backend is None:
            # choose best available
            if _HAS_SENTENCE_TRANSFORMERS:
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
                logger.exception("Failed to load sentence-transformers, falling back")
                self.backend = "fallback"

        if self.backend == "openai":
            if not _HAS_OPENAI:
                logger.warning("OpenAI SDK not available; falling back")
                self.backend = "fallback"
            else:
                if openai_api_key:
                    openai.api_key = openai_api_key
                logger.info("Embedder using OpenAI (key provided=%s)", bool(openai_api_key))

    def embed_text(self, text: str) -> List[float]:
        """
        Return a vector for a single string.
        Fallback produces deterministic 128-d vector from SHA256.
        """
        if self.backend == "sentence-transformers" and self._sent_model:
            vec = self._sent_model.encode([text], show_progress_bar=False)[0]
            return vec.tolist() if hasattr(vec, "tolist") else list(vec)
        if self.backend == "openai":
            try:
                # user must have set openai.api_key externally or via constructor
                resp = openai.Embedding.create(model=self.model_name, input=text)
                vec = resp["data"][0]["embedding"]
                return list(vec)
            except Exception:
                logger.exception("OpenAI embed failed; falling back")
                self.backend = "fallback"
        # fallback deterministic hashing -> vector
        return self._fallback_embed(text)

    def embed_batch(self, texts):
        return [self.embed_text(t) for t in texts]

    def _fallback_embed(self, text: str, dim: int = 128) -> List[float]:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        out = []
        # expand digest to dim floats deterministically
        i = 0
        while len(out) < dim:
            chunk = hashlib.sha256(h + bytes([i])).digest()
            # take 8 bytes -> float in [0,1)
            for j in range(0, len(chunk), 8):
                if len(out) >= dim:
                    break
                v = int.from_bytes(chunk[j:j+8], "big", signed=False)
                out.append((v % 10**8) / 10**8)  # normalized pseudo-float
            i += 1
        return out[:dim]

    def health(self) -> dict:
        return {"backend": self.backend, "model": self.model_name}
