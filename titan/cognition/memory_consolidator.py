# titan/cognition/memory_consolidator.py
"""
Memory Consolidation Service (enterprise-grade)

Responsibilities:
- Read recent episodic events (episodic_store) and short-term context (context_store)
- Filter & normalize events into candidate "memories"
- Create compact summaries and embeddings via the project's `embeddings` service
- Upsert embeddings into vector memory (memory / vector_store)
- Periodically run (tick), supports manual invocation
- Emits events to EventBus for observability: 'memory.consolidation.started',
  'memory.consolidation.completed', 'memory.consolidation.error'

Design notes:
- This service intentionally stays low-privilege: it only writes semantic facts
  to the vector memory and metadata into session_manager context.
- It does light clustering to compress similar stream-of-consciousness into one
  memory record.
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger("titan.cognition.memory_consolidator")


@dataclass
class ConsolidationConfig:
    tick_interval: float = 60.0  # how often consolidation runs
    lookback_seconds: int = 60 * 60 * 24  # how far back in episodic log to consolidate
    min_events_for_memory: int = 3
    cluster_similarity_threshold: float = 0.78  # cosine similarity threshold for merging
    batch_size: int = 64
    max_new_memories_per_run: int = 50
    embed_timeout_seconds: int = 10


class MemoryConsolidator:
    def __init__(self, app: Dict[str, Any], config: Optional[ConsolidationConfig] = None):
        self.app = app
        self.config = config or ConsolidationConfig()
        self.loop = asyncio.get_event_loop()
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # service backends (may be None)
        self.episodic_store = app.get("episodic_store")
        self.vector_store = app.get("vector_store") or app.get("memory")
        self.embeddings = app.get("embeddings")
        self.session_manager = app.get("session_manager")
        self.context_store_factory = app.get("context_store_factory")
        self.event_bus = app.get("event_bus")

        # internal watermark so repeated runs don't re-process same events
        self._last_processed_ts = 0.0
        # persisted watermark storage key
        self._watermark_key = "cognition.memory_consolidator.last_ts"
        # try load watermark from session_manager if available
        try:
            if self.session_manager and getattr(self.session_manager, "get", None):
                sess = self.session_manager.get(self.app.get("default_session_id", None))
                if sess:
                    ctx = sess.get("context", {}) or {}
                    self._last_processed_ts = float(ctx.get(self._watermark_key, 0.0))
        except Exception:
            logger.debug("Could not load watermark from session_manager")

    # ------------------------
    # Lifecycle
    # ------------------------
    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("MemoryConsolidator started (tick=%ss)", self.config.tick_interval)

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            try:
                self._task.cancel()
                await self._task
            except Exception:
                pass
        logger.info("MemoryConsolidator stopped")

    async def _run_loop(self):
        while self._running:
            try:
                await self.consolidate_once()
            except Exception:
                logger.exception("MemoryConsolidator tick failed")
            await asyncio.sleep(self.config.tick_interval)

    # ------------------------
    # Consolidation logic
    # ------------------------
    async def consolidate_once(self) -> Dict[str, Any]:
        """
        One pass of consolidation:
          - query episodic store for events newer than last watermark and within lookback window
          - filter usable event types
          - group similar events by simple heuristics then embed & upsert into vector store
        Returns a summary dict (for logs / metrics)
        """
        result = {"new_memories": 0, "skipped": 0, "errors": 0}
        start_ts = time.time()
        try:
            if not self.episodic_store:
                logger.warning("MemoryConsolidator: no episodic_store configured")
                return result

            # get events since watermark and within lookback
            query_since = max(self._last_processed_ts, time.time() - self.config.lookback_seconds)
            events = await self._fetch_events_since(query_since)
            if not events:
                return result

            # normalize and filter events to textual candidates
            candidates = self._filter_and_normalize(events)
            if len(candidates) < self.config.min_events_for_memory:
                logger.debug("Not enough candidates (%d) to form a memory", len(candidates))
                return result

            # cluster candidates by semantic hashing or naive grouping
            clusters = await self._cluster_candidates(candidates)

            # embed cluster summaries and upsert into vector store
            inserted = 0
            for cl in clusters[: self.config.max_new_memories_per_run]:
                try:
                    summary_text = cl["summary"]
                    metadata = cl.get("metadata", {})
                    emb = await self._embed_text(summary_text)
                    if emb is None:
                        result["errors"] += 1
                        continue
                    # upsert into vector_store
                    if self.vector_store and getattr(self.vector_store, "upsert", None):
                        # create a stable id
                        mem_id = f"mem_{int(time.time() * 1000)}_{inserted}"
                        self.vector_store.upsert(mem_id, emb, metadata)
                        inserted += 1
                    elif self.vector_store and getattr(self.vector_store, "add", None):
                        # fallback for other store shapes
                        mem_id = f"mem_{int(time.time() * 1000)}_{inserted}"
                        self.vector_store.add(mem_id, emb, metadata)
                        inserted += 1
                    else:
                        logger.debug("No vector_store upsert available; skipping")
                        result["skipped"] += 1
                except Exception:
                    logger.exception("Failed upserting cluster")
                    result["errors"] += 1

            # update watermark (highest event ts)
            max_ts = max(e.get("ts", 0) for e in events) if events else self._last_processed_ts
            self._last_processed_ts = max(self._last_processed_ts, max_ts)
            await self._persist_watermark(self._last_processed_ts)
            result["new_memories"] = inserted

            # emit eventbus signal
            if self.event_bus and getattr(self.event_bus, "publish", None):
                try:
                    self.event_bus.publish("memory.consolidation.completed", {"new_memories": inserted, "duration": time.time() - start_ts})
                except Exception:
                    logger.debug("EventBus publish failed for consolidation.completed")
            return result
        except Exception:
            logger.exception("MemoryConsolidator.consolidate_once failed")
            result["errors"] += 1
            if self.event_bus and getattr(self.event_bus, "publish", None):
                try:
                    self.event_bus.publish("memory.consolidation.error", {"error": "exception"})
                except Exception:
                    pass
            return result

    # ------------------------
    # Helpers
    # ------------------------
    async def _fetch_events_since(self, since_ts: float) -> List[Dict[str, Any]]:
        """
        Best-effort read from episodic_store. Support multiple shapes:
         - episodic_store.query({"ts": {"$gte": since_ts}})
         - episodic_store.iter(from_ts)
         - episodic_store.read_range(since_ts, now)
         - episodic_store.get_recent(limit)
        """
        try:
            store = self.episodic_store
            if hasattr(store, "query"):
                try:
                    res = store.query({"ts": {"$gte": since_ts}})
                    return list(res or [])
                except Exception:
                    pass
            if hasattr(store, "iter"):
                out = []
                it = store.iter(since_ts)
                for r in it:
                    out.append(r)
                return out
            if hasattr(store, "read_range"):
                return store.read_range(since_ts, time.time())
            if hasattr(store, "get_recent"):
                return store.get_recent(1000)
            # last resort: if store is a list-like in memory
            if isinstance(store, list):
                return [e for e in store if e.get("ts", 0) >= since_ts]
        except Exception:
            logger.exception("Failed to fetch events from episodic_store")
        return []

    def _filter_and_normalize(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert raw events into textual candidate dicts:
        {'text': ..., 'ts': ..., 'source': ..., 'metadata': {...}}
        """
        out = []
        for e in events:
            try:
                # ignore autonomy-originated or internal debug events
                if e.get("source") == "autonomy":
                    continue
                text = None
                # common shapes
                if e.get("type") == "transcript":
                    text = e.get("text") or (e.get("payload") or {}).get("text")
                else:
                    payload = e.get("payload") or {}
                    # prioritize body/title
                    text = payload.get("body") or payload.get("text") or payload.get("title")
                if not text:
                    continue
                entry = {"text": str(text).strip(), "ts": e.get("ts", time.time()), "source": e.get("type") or e.get("source"), "metadata": {"event": e}}
                out.append(entry)
            except Exception:
                logger.debug("Normalization of event failed")
        return out

    async def _cluster_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Simple cluster by embedding similarity:
         - embed each candidate
         - greedily group candidates with cosine similarity > threshold
         - produce cluster summaries (concatenate representative texts and short metadata)
        This is intentionally simple and memory-friendly; you can replace with agglomerative clustering later.
        """
        if not candidates:
            return []

        texts = [c["text"] for c in candidates]
        # embed in batches
        embeddings = []
        for i in range(0, len(texts), self.config.batch_size):
            batch = texts[i : i + self.config.batch_size]
            try:
                emb_batch = await asyncio.wait_for(self._embed_texts(batch), timeout=self.config.embed_timeout_seconds)
                embeddings.extend(emb_batch)
            except Exception:
                logger.exception("Embedding batch failed; filling with None")
                embeddings.extend([None] * len(batch))

        # fallback: if embeddings missing, fallback to naive grouping by first 140 chars
        clusters = []
        if any(e is None for e in embeddings):
            buckets = {}
            for idx, c in enumerate(candidates):
                key = (c["text"][:140]).strip()
                buckets.setdefault(key, []).append(c)
            for key, items in buckets.items():
                clusters.append({"summary": " ".join(i["text"] for i in items[:3]), "metadata": {"count": len(items)}})
            return clusters

        # compute cosine similarity pairwise (greedy clustering)
        import math

        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            if na == 0 or nb == 0:
                return 0.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)

        used = set()
        for i, emb in enumerate(embeddings):
            if i in used or emb is None:
                continue
            cluster = [candidates[i]]
            used.add(i)
            for j in range(i + 1, len(embeddings)):
                if j in used or embeddings[j] is None:
                    continue
                try:
                    score = cos(emb, embeddings[j])
                except Exception:
                    score = 0.0
                if score >= self.config.cluster_similarity_threshold:
                    cluster.append(candidates[j])
                    used.add(j)
            # build summary: take top-k texts
            texts_for_summary = [x["text"] for x in cluster][:5]
            summary = " — ".join(texts_for_summary)
            clusters.append({"summary": summary, "metadata": {"count": len(cluster), "representative_ts": cluster[0]["ts"]}})
        return clusters

    async def _embed_texts(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Bulk embed using the configured embeddings service.
        Expects embeddings.embed_batch(texts) -> List[List[float]]
        """
        if not self.embeddings:
            return [None] * len(texts)
        try:
            if hasattr(self.embeddings, "embed_batch"):
                res = self.embeddings.embed_batch(texts)
                if asyncio.iscoroutine(res):
                    res = await res
                return res
            elif hasattr(self.embeddings, "embed"):
                out = []
                for t in texts:
                    r = self.embeddings.embed(t)
                    if asyncio.iscoroutine(r):
                        r = await r
                    out.append(r)
                return out
        except Exception:
            logger.exception("Embedding service failed")
        return [None] * len(texts)

    async def _embed_text(self, text: str) -> Optional[List[float]]:
        res = await self._embed_texts([text])
        return res[0] if res else None

    async def _persist_watermark(self, ts: float):
        """
        Save watermark into session_manager.session.context
        """
        if not self.session_manager:
            return
        try:
            sid = self.app.get("default_session_id")
            if not sid:
                return
            # Using session_manager.update API if available
            try:
                self.session_manager.update(sid, context={self._watermark_key: ts})
            except Exception:
                # fallback: direct get/modify/save
                s = self.session_manager.get(sid) or {}
                ctx = s.get("context", {}) or {}
                ctx[self._watermark_key] = ts
                try:
                    self.session_manager._enqueue_save(sid, s)
                except Exception:
                    logger.debug("Fallback persist watermark failed")
        except Exception:
            logger.exception("Persist watermark failed")
