# titan/cognition/predictive_context.py
"""
Predictive Context Engine (enterprise-grade)

Responsibilities:
- Build and persist habit models from consolidated memories + episodic logs
- Provide an API to ask "what's next?" given a short context snapshot
- Offer scoring of candidate next actions (probabilities, confidence)
- Support subscription: emits 'predictive_context.recommendation' to EventBus
- Uses lightweight statistical + embedding nearest-neighbor heuristics by default
- Can be extended with an ML model trainer endpoint later (not included)
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("titan.cognition.predictive_context")


class PredictiveContextEngine:
    def __init__(self, app: Dict[str, Any], *, history_depth: int = 500):
        self.app = app
        self.vector_store = app.get("vector_store") or app.get("memory")
        self.episodic_store = app.get("episodic_store")
        self.session_manager = app.get("session_manager")
        self.event_bus = app.get("event_bus")
        self.history_depth = history_depth
        self._model_store_key = "cognition.predictive_context.model"  # placeholder key for persisted stats
        self._cache = {}  # runtime cache for quick responses

    # ------------------------
    # Public API
    # ------------------------
    async def recommend(self, context_snapshot: Dict[str, Any], top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Given a context snapshot (active window, recent events, last actions), return ranked next-actions.
        Each recommendation is a dict:
          {"action": "summarize_page", "confidence": 0.72, "reason": "...", "score": 0.72, "metadata": {...}}
        Approach:
        - Look up similar memories (vector store) by embedding the context snapshot
        - Use frequency counts from episodic_store to propose likely actions
        - Combine semantic similarity + frequency to score candidates
        """
        out = []
        try:
            # form a compact query text from context
            query_text = self._serialize_context(context_snapshot)
            emb = None
            if self.app.get("embeddings"):
                try:
                    emb = self.app["embeddings"].embed(query_text)
                    if asyncio.iscoroutine(emb):
                        emb = await emb
                except Exception:
                    logger.debug("Embeddings failed for predictive query")
            # semantic retrieval
            candidates = []
            if emb and self.vector_store and getattr(self.vector_store, "query", None):
                try:
                    qres = self.vector_store.query(emb, top_k=top_k * 4)
                    for item in qres:
                        meta = getattr(item, "metadata", None) or (item[2] if isinstance(item, (list, tuple)) and len(item) > 2 else {})
                        # infer possible actions from metadata or stored event
                        candidate_action = self._infer_action_from_metadata(meta)
                        if candidate_action:
                            candidates.append((candidate_action, getattr(item, "score", 1.0)))
                except Exception:
                    logger.debug("Vector store query failed in predictive.recommend")
            # fallback: scan episodic_store for most common events
            if not candidates and self.episodic_store and getattr(self.episodic_store, "query", None):
                try:
                    res = self.episodic_store.query({"limit": min(200, self.history_depth)})
                    freq = {}
                    for r in res:
                        t = r.get("type") or r.get("event_type") or r.get("source")
                        if not t:
                            continue
                        freq[t] = freq.get(t, 0) + 1
                    for k, v in sorted(freq.items(), key=lambda x: -x[1])[:top_k]:
                        candidates.append((k, v))
                except Exception:
                    logger.debug("Episodic scan failed")
            # score & normalize
            scored = {}
            for (act, score) in candidates:
                scored.setdefault(act, 0.0)
                scored[act] += float(score or 1.0)
            # convert to ranked list
            items = sorted(scored.items(), key=lambda x: -x[1])[:top_k]
            total = float(sum(v for _, v in items) or 1.0)
            for act, scr in items:
                out.append({"action": act, "score": float(scr), "confidence": float(scr) / total, "reason": "semantic+frequency", "metadata": {}})
            # emit event for observability
            try:
                if self.event_bus and getattr(self.event_bus, "publish", None):
                    self.event_bus.publish("predictive_context.recommendation", {"context": context_snapshot, "recommendations": out, "ts": time.time()})
            except Exception:
                logger.debug("predictive_context.publish failed")
        except Exception:
            logger.exception("predictive.recommend failed")
        return out

    # ------------------------
    # Helpers
    # ------------------------
    def _serialize_context(self, ctx: Dict[str, Any]) -> str:
        """
        Create a short query string representing the current context snapshot.
        Keep it compact to work with existing embedders.
        """
        parts = []
        aw = ctx.get("active_window")
        if aw:
            parts.append(str(aw.get("title", "")))
            parts.append(str(aw.get("app", "")))
        recent = ctx.get("recent_events", [])
        if recent:
            # include up to 3 recent textual snippets
            for r in recent[-3:]:
                txt = r.get("text") or r.get("summary") or r.get("title") or ""
                parts.append(str(txt)[:200])
        return " | ".join([p for p in parts if p])

    def _infer_action_from_metadata(self, meta: Dict[str, Any]) -> Optional[str]:
        """
        Best-effort extraction: if metadata contains 'intent' or 'action' keys from consolidated memories, return it.
        """
        if not meta:
            return None
        intent = meta.get("intent") or meta.get("action") or meta.get("proposed_intent")
        if intent:
            return str(intent)
        # fallback: look into the event object
        ev = meta.get("event")
        if isinstance(ev, dict):
            t = ev.get("type") or ev.get("event_type")
            if t:
                return t
        return None
