# titan/cognition/memory_hygiene.py
"""
Memory Hygiene System (Titan v2.1)

Responsibilities:
- Periodically enforce retention policies on episodic_store (time-based and size-based)
- Compact / prune semantic vector_store (remove aged entries, low-quality duplicates)
- Provide a 'dry-run' mode and a 'run_once' for manual invocation
- Emit events: 'hygiene.run', 'hygiene.pruned', 'hygiene.compacted'
- Integrates with metrics_adapter and event_bus
- Conservative defaults for safety (won't delete unless configured to actually delete)
"""

from __future__ import annotations
import time
import logging
import asyncio
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("titan.cognition.memory_hygiene")


class MemoryHygieneConfig:
    # purge episodic events older than this (seconds)
    EPISODIC_RETENTION_SECONDS = 60 * 60 * 24 * 90  # 90 days
    # if episodic store grows beyond this many items, prune oldest (best-effort)
    EPISODIC_RETENTION_MAX = 50000
    # vector store pruning: remove vectors older than this (seconds)
    VECTOR_RETENTION_SECONDS = 60 * 60 * 24 * 365  # 1 year
    # duplicate similarity threshold below which duplicates will be merged / dropped
    VECTOR_DUPLICATE_SIMILARITY = 0.96
    # max items to scan per hygiene run to avoid heavy IO
    MAX_VECTOR_SCAN = 5000
    # whether to actually delete or run in dry-run mode
    DRY_RUN_DEFAULT = True
    # run interval seconds (if scheduled as a service)
    RUN_INTERVAL = 60 * 60 * 6  # every 6 hours


class MemoryHygiene:
    def __init__(self, app: Dict[str, Any], config: Optional[MemoryHygieneConfig] = None):
        self.app = app or {}
        self.config = config or MemoryHygieneConfig()
        self.episodic = self.app.get("episodic_store")
        self.vector = self.app.get("vector_store") or self.app.get("memory")
        self.embeddings = self.app.get("embeddings")
        self.event_bus = self.app.get("event_bus")
        self.metrics = self.app.get("metrics_adapter")
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Keep in app for easy access
        try:
            self.app["memory_hygiene"] = self
        except Exception:
            pass

    # ------------------------
    # lifecycle
    # ------------------------
    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("MemoryHygiene started (interval=%s)", self.config.RUN_INTERVAL)

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

    async def _loop(self):
        while self._running:
            try:
                await self.run_once(dry_run=self.config.DRY_RUN_DEFAULT)
            except Exception:
                logger.exception("MemoryHygiene loop run failed")
            await asyncio.sleep(self.config.RUN_INTERVAL)

    # ------------------------
    # Public API
    # ------------------------
    async def run_once(self, *, dry_run: Optional[bool] = None) -> Dict[str, Any]:
        """
        Run one hygiene pass. If dry_run is True (default coming from config), actions are only reported, not performed.
        Returns a report dict.
        """
        if dry_run is None:
            dry_run = self.config.DRY_RUN_DEFAULT
        report: Dict[str, Any] = {"ts": time.time(), "pruned_episodic": 0, "pruned_vector": 0, "merged_vectors": 0, "errors": 0, "dry_run": dry_run}
        try:
            if self.event_bus and getattr(self.event_bus, "publish", None):
                self.event_bus.publish("hygiene.run", {"ts": time.time(), "dry_run": dry_run})

            # 1) Episodic pruning
            try:
                pe, pi = await self._prune_episodic(dry_run=dry_run)
                report["pruned_episodic"] = pe
                report["episodic_prune_info"] = pi
            except Exception:
                logger.exception("Episodic pruning failed")
                report["errors"] += 1

            # 2) Vector pruning and compaction
            try:
                pv, mv = await self._prune_vector_store(dry_run=dry_run)
                report["pruned_vector"] = pv
                report["merged_vectors"] = mv
            except Exception:
                logger.exception("Vector pruning failed")
                report["errors"] += 1

            # metrics
            try:
                if self.metrics:
                    self.metrics.counter("hygiene_runs").inc()
                    self.metrics.gauge("hygiene_last_run_ts").set(time.time())
            except Exception:
                pass

            if self.event_bus and getattr(self.event_bus, "publish", None):
                self.event_bus.publish("hygiene.completed", report)
        except Exception:
            logger.exception("MemoryHygiene.run_once top-level error")
            report["errors"] += 1
        return report

    # ------------------------
    # Episodic pruning
    # ------------------------
    async def _prune_episodic(self, *, dry_run: bool = True) -> Tuple[int, Dict[str, Any]]:
        """
        Remove old items from episodic store. Supports different store shapes:
        - episodic.delete_older_than(ts)
        - episodic.remove_older(ts)
        - episodic.purge_older(ts)
        - or manual scanning via get_recent / iter
        Returns (pruned_count, info)
        """
        now = time.time()
        cutoff = now - self.config.EPISODIC_RETENTION_SECONDS
        pruned = 0
        info = {"cutoff_ts": cutoff}
        if not self.episodic:
            return pruned, info

        # best-effort deletion
        try:
            # fast path if store supports delete_older_than
            if getattr(self.episodic, "delete_older_than", None):
                if not dry_run:
                    n = self.episodic.delete_older_than(cutoff)
                    pruned += int(n or 0)
                else:
                    # if dry, try to estimate count via query
                    if getattr(self.episodic, "query", None):
                        res = list(self.episodic.query({"ts": {"$lt": cutoff}}) or [])
                        info["estimated_to_prune"] = len(res)
                return pruned, info

            # other path: iterate and remove old items
            if getattr(self.episodic, "iter", None):
                to_delete = []
                for e in self.episodic.iter(0):
                    if e.get("ts", 0) < cutoff:
                        to_delete.append(e)
                        if len(to_delete) >= 5000:
                            break
                info["estimated_to_prune"] = len(to_delete)
                if not dry_run:
                    # attempt deletion by id if supported
                    deleted = 0
                    for d in to_delete:
                        if getattr(self.episodic, "delete", None) and d.get("id"):
                            try:
                                self.episodic.delete(d.get("id"))
                                deleted += 1
                            except Exception:
                                continue
                    pruned += deleted
                return pruned, info

            # fallback: get_recent and trim oldest if length > EPISODIC_RETENTION_MAX
            if getattr(self.episodic, "get_recent", None):
                recent = list(self.episodic.get_recent(self.config.EPISODIC_RETENTION_MAX + 100) or [])
                if len(recent) > self.config.EPISODIC_RETENTION_MAX:
                    info["current_count"] = len(recent)
                    to_remove = len(recent) - self.config.EPISODIC_RETENTION_MAX
                    info["to_remove"] = to_remove
                    if not dry_run and getattr(self.episodic, "delete", None):
                        # delete oldest by timestamp
                        sorted_items = sorted(recent, key=lambda x: x.get("ts", 0))
                        removed = 0
                        for item in sorted_items[:to_remove]:
                            try:
                                self.episodic.delete(item.get("id"))
                                removed += 1
                            except Exception:
                                continue
                        pruned += removed
                return pruned, info

        except Exception:
            logger.exception("prune episodic encountered an error")
        return pruned, info

    # ------------------------
    # Vector pruning & compaction
    # ------------------------
    async def _prune_vector_store(self, *, dry_run: bool = True) -> Tuple[int, int]:
        """
        Strategy:
        - If vector store exposes search by metadata timestamp or list, find old items and delete
        - If not, sample top-N items and attempt duplicate detection by embedding similarity
        - Merge / delete duplicates where similarity > VECTOR_DUPLICATE_SIMILARITY
        Returns (pruned_count, merged_count)
        """
        pruned = 0
        merged = 0
        if not self.vector:
            return pruned, merged

        # 1) Try store-level pruning
        try:
            now = time.time()
            cutoff = now - self.config.VECTOR_RETENTION_SECONDS
            if getattr(self.vector, "delete_older_than", None):
                if not dry_run:
                    n = self.vector.delete_older_than(cutoff)
                    pruned += int(n or 0)
                else:
                    # try to estimate
                    if getattr(self.vector, "query_by_ts", None):
                        res = list(self.vector.query_by_ts(cutoff, limit=1000) or [])
                        # estimate scale by sample
                        pruned = len(res)
                return pruned, merged

            # 2) fallback sampling + duplicate detection
            # try to list or query entries (store-specific shapes)
            candidates = []
            if getattr(self.vector, "query_all", None):
                try:
                    candidates = list(self.vector.query_all(limit=self.config.MAX_VECTOR_SCAN) or [])
                except Exception:
                    candidates = []
            elif getattr(self.vector, "list_ids", None):
                try:
                    ids = list(self.vector.list_ids(limit=self.config.MAX_VECTOR_SCAN) or [])
                    # try to fetch batches
                    for _id in ids:
                        try:
                            item = self.vector.get(_id)
                            if item:
                                candidates.append(item)
                        except Exception:
                            continue
                except Exception:
                    candidates = []
            else:
                # attempt a sampling via query([]) if supported
                try:
                    if getattr(self.vector, "query", None):
                        # query with dummy vector if store allows top-k retrieval; fallback to empty
                        candidates = list(self.vector.query([], top_k=self.config.MAX_VECTOR_SCAN) or [])
                except Exception:
                    candidates = []

            # normalize items into list of tuples (id, embedding, metadata, ts)
            normalized = []
            for it in candidates:
                try:
                    # possible shapes: (id, emb, metadata, score) or object with .id, .metadata
                    if isinstance(it, (list, tuple)) and len(it) >= 3:
                        _id = it[0]
                        emb = it[1]
                        meta = it[2] if len(it) > 2 else {}
                        ts = meta.get("ts") or meta.get("created_at") or meta.get("timestamp") or 0
                        normalized.append((_id, emb, meta, ts))
                    elif isinstance(it, dict):
                        _id = it.get("id")
                        emb = it.get("embedding") or it.get("vector")
                        meta = it.get("metadata", {})
                        ts = meta.get("ts") or meta.get("created_at") or 0
                        normalized.append((_id, emb, meta, ts))
                    else:
                        # best-effort
                        _id = getattr(it, "id", None)
                        emb = getattr(it, "embedding", None) or getattr(it, "vector", None)
                        meta = getattr(it, "metadata", {}) or {}
                        ts = meta.get("ts", 0)
                        normalized.append((_id, emb, meta, ts))
                except Exception:
                    continue

            # prune by age (if metadata ts present)
            now = time.time()
            to_delete_ids = []
            for _id, emb, meta, ts in normalized:
                if ts and ts < (now - self.config.VECTOR_RETENTION_SECONDS):
                    to_delete_ids.append(_id)
            if to_delete_ids:
                if not dry_run:
                    for _id in to_delete_ids:
                        try:
                            if getattr(self.vector, "delete", None):
                                self.vector.delete(_id)
                                pruned += 1
                        except Exception:
                            continue
                else:
                    pruned += len(to_delete_ids)

            # duplicate detection (very conservative and sampling-limited)
            if self.embeddings and getattr(self.embeddings, "cosine_sim", None):
                # use store embeddings where available; compare pairs greedily
                sims = getattr(self.embeddings, "cosine_sim")
                seen = set()
                for i in range(len(normalized)):
                    if i in seen:
                        continue
                    id_i, emb_i, meta_i, ts_i = normalized[i]
                    if not emb_i:
                        continue
                    for j in range(i + 1, len(normalized)):
                        if j in seen:
                            continue
                        id_j, emb_j, meta_j, ts_j = normalized[j]
                        if not emb_j:
                            continue
                        try:
                            sim = float(sims(emb_i, emb_j))
                        except Exception:
                            # fallback to simple dot if emb arrays
                            try:
                                sim = sum(a * b for a, b in zip(emb_i, emb_j)) / (max(1e-9, sum(a*a for a in emb_i)**0.5) * max(1e-9, sum(b*b for b in emb_j)**0.5))
                            except Exception:
                                sim = 0.0
                        if sim >= self.config.VECTOR_DUPLICATE_SIMILARITY:
                            # mark one for deletion: older one gets removed
                            keep = id_i if (ts_i or 0) >= (ts_j or 0) else id_j
                            remove = id_j if keep == id_i else id_i
                            # prefer to merge metadata if possible (best-effort)
                            if not dry_run:
                                try:
                                    if getattr(self.vector, "merge", None):
                                        # some stores provide merge semantics
                                        self.vector.merge(keep, remove)
                                        merged += 1
                                        # then delete remove
                                        if getattr(self.vector, "delete", None):
                                            self.vector.delete(remove)
                                            pruned += 1
                                    else:
                                        # no merge support: delete older
                                        if getattr(self.vector, "delete", None):
                                            self.vector.delete(remove)
                                            pruned += 1
                                except Exception:
                                    continue
                            else:
                                merged += 1
                                pruned += 1
                            seen.add(j)
            return pruned, merged
        except Exception:
            logger.exception("vector pruning operation failed")
            return pruned, merged
