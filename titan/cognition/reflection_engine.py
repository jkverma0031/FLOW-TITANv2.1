# titan/cognition/reflection_engine.py
"""
Reflection Engine (v2.1)

Purpose
-------
- Periodically analyze recent episodes and outcomes.
- Compute metrics about skill proposals: acceptance rate, execution success, reverts, errors.
- Produce concise "reflection records" (summaries & lessons) and persist them to:
    - episodic_store (as reflection entries) and/or
    - vector_store (semantic memory) via embeddings.
- Emit structured events:
    - 'reflection.started'
    - 'reflection.completed'
    - 'reflection.lesson'  (a short actionable observation)
    - 'reflection.error'
- Expose APIs:
    - run_once(): perform one reflection cycle
    - summarize_range(start_ts, end_ts)
    - get_recent_reflections(limit)
- Provide lightweight hooks to feed observations back into predictive_context (if available).
- Safe-by-design: never modifies code or policy. Only writes "insights" that humans or controlled modules can review.

Design Notes
------------
- The engine is synchronous-friendly (async API) and uses configurable timeouts.
- All external dependencies are optional and handled defensively.
- Reflection records are kept compact (summary, signals, recommended actions).
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict

# Use pydantic only for optional validation (kept minimal to reduce dependency issues)
try:
    from pydantic import BaseModel, Field
    PydanticAvailable = True
except Exception:
    PydanticAvailable = False

logger = logging.getLogger("titan.cognition.reflection")


@dataclass
class ReflectionConfig:
    lookback_seconds: int = 60 * 60 * 24  # analyze last 24 hours by default
    min_episodes: int = 5
    tick_interval: int = 60 * 10  # default: run every 10 minutes when running as a service
    embedding_timeout: float = 8.0
    max_reflections_per_run: int = 10
    summary_max_chars: int = 800


if PydanticAvailable:
    class ReflectionRecord(BaseModel):
        ts: float = Field(default_factory=lambda: time.time())
        summary: str = Field(...)
        insights: Dict[str, Any] = Field(default_factory=dict)
        suggested_actions: List[Dict[str, Any]] = Field(default_factory=list)
        source_window: Optional[str] = None
        origin: str = Field("reflection_engine")
else:
    class ReflectionRecord(dict):
        def __init__(self, summary: str, insights: Dict[str, Any] = None, suggested_actions: List[Dict[str, Any]] = None, source_window: Optional[str] = None):
            super().__init__()
            self["ts"] = time.time()
            self["summary"] = summary
            self["insights"] = insights or {}
            self["suggested_actions"] = suggested_actions or []
            self["source_window"] = source_window
            self["origin"] = "reflection_engine"


class ReflectionEngine:
    def __init__(self, app: Dict[str, Any], config: Optional[ReflectionConfig] = None):
        """
        app: kernel app/context dict (should contain episodic_store, session_manager, vector_store/memory, embeddings, event_bus, predictive_context)
        """
        self.app = app or {}
        self.config = config or ReflectionConfig()
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # backends (optional)
        self.episodic_store = self.app.get("episodic_store")
        self.session_manager = self.app.get("session_manager")
        self.vector_store = self.app.get("vector_store") or self.app.get("memory")
        self.embeddings = self.app.get("embeddings")
        self.event_bus = self.app.get("event_bus")
        self.predictive_context = self.app.get("predictive_context")
        self.default_session_id = self.app.get("default_session_id")

        # internal watermark to avoid reprocessing same episodes repeatedly
        self._last_reflection_ts = 0.0
        self._watermark_key = "cognition.reflection.last_ts"
        self._load_watermark()

    # --------------------
    # lifecycle
    # --------------------
    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("ReflectionEngine started (tick=%ss)", self.config.tick_interval)

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
            self._task = None
        logger.info("ReflectionEngine stopped")

    async def _loop(self):
        while self._running:
            try:
                await self.run_once()
            except Exception:
                logger.exception("ReflectionEngine loop encountered an error")
            await asyncio.sleep(self.config.tick_interval)

    # --------------------
    # core API
    # --------------------
    async def run_once(self) -> Dict[str, Any]:
        """
        Perform a single reflection run:
          - fetch episodes since last watermark (bounded by lookback_seconds)
          - analyze acceptance/exec/success/failure patterns
          - build reflection records and persist them
          - publish reflection events
        Returns a summary dict with counts.
        """
        result = {"reflections_created": 0, "episodes_analyzed": 0, "errors": 0}
        start_ts = time.time()
        try:
            self._publish_event("reflection.started", {"ts": start_ts})
            # 1) fetch episodes
            episodes = await self._fetch_recent_episodes(since_ts=self._last_reflection_ts or (time.time() - self.config.lookback_seconds))
            result["episodes_analyzed"] = len(episodes)
            if not episodes or len(episodes) < self.config.min_episodes:
                # nothing to do
                self._update_watermark(time.time())
                self._publish_event("reflection.completed", {"reflections": [], "duration": time.time() - start_ts})
                return result

            # 2) analyze episodes into clusters / candidate issues
            clusters = self._cluster_episodes(episodes)
            # 3) for each cluster generate a compact reflection record
            reflections = []
            for cluster in clusters[: self.config.max_reflections_per_run]:
                try:
                    summary, insights = await self._summarize_cluster(cluster)
                    suggested_actions = self._propose_actions_from_insights(insights, cluster)
                    # limit summary size
                    if len(summary) > self.config.summary_max_chars:
                        summary = summary[: self.config.summary_max_chars] + "…"
                    rec = self._make_reflection_record(summary, insights, suggested_actions, source_window=cluster.get("common_window"))
                    reflections.append(rec)
                    # persist reflection record
                    await self._persist_reflection(rec)
                    result["reflections_created"] += 1
                    # feed to predictive_context (best-effort)
                    try:
                        if self.predictive_context:
                            # supply a compact snapshot
                            snapshot = {"summary": summary, "insights": insights, "ts": rec["ts"]}
                            # no await to avoid blocking heavy computations; schedule best-effort
                            asyncio.create_task(self.predictive_context.recommend({"active_window": cluster.get("common_window"), "recent_events": cluster.get("events")[:3]}))
                    except Exception:
                        logger.debug("predictive_context hook failed for reflection")
                except Exception:
                    logger.exception("Failed building reflection for cluster")
                    result["errors"] += 1

            # 4) set watermark to latest episode ts
            latest_ts = max((e.get("ts", 0) for e in episodes), default=time.time())
            self._update_watermark(latest_ts)
            self._publish_event("reflection.completed", {"reflections": [r.get("summary") for r in reflections], "duration": time.time() - start_ts})
            return result
        except Exception:
            logger.exception("ReflectionEngine.run_once top-level error")
            result["errors"] += 1
            self._publish_event("reflection.error", {"error": "exception"})
            return result

    # --------------------
    # helpers: fetch & clustering
    # --------------------
    async def _fetch_recent_episodes(self, since_ts: float) -> List[Dict[str, Any]]:
        """
        Best-effort fetch from episodic_store. Handles a variety of store APIs.
        """
        try:
            store = self.episodic_store
            if not store:
                logger.debug("No episodic_store; returning empty episodes")
                return []
            # Try query-like interface
            if hasattr(store, "query"):
                try:
                    res = store.query({"ts": {"$gte": since_ts}})
                    return list(res or [])
                except Exception:
                    pass
            # iterator-based
            if hasattr(store, "iter"):
                out = []
                try:
                    it = store.iter(since_ts)
                    for r in it:
                        out.append(r)
                    return out
                except Exception:
                    pass
            # other common helpers
            if hasattr(store, "get_recent"):
                try:
                    return store.get_recent(1000)
                except Exception:
                    pass
            # fallback
            logger.debug("episodic_store has no known interface; attempting attribute access")
            if isinstance(store, list):
                return [e for e in store if e.get("ts", 0) >= since_ts]
        except Exception:
            logger.exception("Failed reading episodes from episodic_store")
        return []

    def _cluster_episodes(self, episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Lightweight clustering:
         - group episodes by top-level 'type' or 'event' or by window title
         - produce clusters with a sample of events
        """
        if not episodes:
            return []
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for e in episodes:
            try:
                # avoid reflecting on internal autonomy signaling
                if e.get("source") == "autonomy":
                    continue
                key = e.get("type") or (e.get("payload") or {}).get("type") or (e.get("payload") or {}).get("app") or (e.get("window") or {}).get("title") or "misc"
                # normalize small keys
                key = str(key).lower()[:80]
                buckets.setdefault(key, []).append(e)
            except Exception:
                continue
        clusters = []
        for k, group in buckets.items():
            clusters.append({"key": k, "events": group, "size": len(group), "common_window": self._infer_common_window(group)})
        # sort clusters by size descending (focus on large patterns)
        clusters.sort(key=lambda c: -c["size"])
        return clusters

    def _infer_common_window(self, events: List[Dict[str, Any]]) -> Optional[str]:
        """
        Try to detect a recurring active_window title if present.
        """
        try:
            counts: Dict[str, int] = {}
            for e in events:
                w = None
                payload = e.get("payload") or {}
                if "window" in e:
                    w = (e.get("window") or {}).get("title")
                elif payload.get("window"):
                    w = payload.get("window", {}).get("title")
                if w:
                    w_str = str(w)[:400]
                    counts[w_str] = counts.get(w_str, 0) + 1
            if not counts:
                return None
            # return most common
            return max(counts.items(), key=lambda x: x[1])[0]
        except Exception:
            return None

    # --------------------
    # summarization & insights
    # --------------------
    async def _summarize_cluster(self, cluster: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Create a short textual summary and extract insights (acceptance rates, errors, repeated failures).
        Uses embeddings optionally to help compress content into a summary (LLM not invoked here to keep costs local).
        """
        events = cluster.get("events", [])[: 50]  # cap
        # simple heuristics
        total = len(events)
        accepted = 0
        executed = 0
        failed = 0
        errors = 0
        proposals = 0
        for e in events:
            ev_type = e.get("type") or e.get("event_type") or ""
            # detect skill proposals and autonomy decisions recorded in episodes
            if ev_type == "skill.proposal" or (e.get("payload") and e["payload"].get("type") == "skill.proposal"):
                proposals += 1
                # check outcome keys in payload or outcome
                out = e.get("outcome") or (e.get("payload") or {}).get("outcome")
                if out:
                    if out.get("status") in ("dispatched", "done", "success"):
                        executed += 1
                    if out.get("status") in ("ignored", "ask"):
                        # not executed
                        pass
                    if out.get("status") in ("failed", "error", "orch_timeout", "no_orchestrator"):
                        failed += 1
                        if out.get("error"):
                            errors += 1
            # generic goals: check for 'result' / 'error' keys
            res = e.get("result") or e.get("outcome")
            if isinstance(res, dict) and res.get("status") in ("failed", "error"):
                failed += 1
            # maybe user accepted: look for "user_accepted" flags
            if e.get("user_response") in ("accepted", "yes", True):
                accepted += 1

        # construct a human-readable summary
        summary_lines = [
            f"Reflected on {total} events matching '{cluster.get('key')}'.",
            f"Proposals seen: {proposals}. Executed: {executed}. Failed: {failed}. Errors: {errors}. User-accepted: {accepted}.",
        ]

        # find common error messages (best-effort)
        error_snippets = []
        for e in events:
            try:
                out = e.get("outcome") or (e.get("payload") or {}).get("outcome")
                if isinstance(out, dict) and out.get("error"):
                    snippet = str(out.get("error"))[:200]
                    error_snippets.append(snippet)
            except Exception:
                continue
        if error_snippets:
            summary_lines.append("Common errors: " + "; ".join(error_snippets[:3]))

        summary = " ".join(summary_lines)

        # build insights (structured)
        insights: Dict[str, Any] = {
            "cluster_key": cluster.get("key"),
            "count": total,
            "proposals": proposals,
            "executed": executed,
            "failed": failed,
            "errors": errors,
            "user_accepted": accepted,
            "common_window": cluster.get("common_window"),
        }

        # optionally compute an embedding for the summary (best-effort)
        emb = None
        if self.embeddings and getattr(self.embeddings, "embed", None):
            try:
                maybe = self.embeddings.embed(summary)
                if asyncio.iscoroutine(maybe):
                    emb = await asyncio.wait_for(maybe, timeout=self.config.embedding_timeout)
                else:
                    emb = maybe
            except Exception:
                logger.debug("Embedding summary failed during reflection")
                emb = None

        # attach embedding to insights for downstream use
        if emb is not None:
            insights["summary_embedding"] = emb

        return summary, insights

    def _propose_actions_from_insights(self, insights: Dict[str, Any], cluster: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Convert insights into actionable suggestions. These are recommendations for humans
        or downstream automated tuning pipelines (not direct code changes).
        Example suggestions:
          - "increase planner timeout for orchestrator"
          - "mark skill X as disabled by default (if many failures)"
          - "investigate orchestrator connectivity"
        """
        actions: List[Dict[str, Any]] = []
        try:
            # If many failures relative to executions
            executed = insights.get("executed", 0)
            failed = insights.get("failed", 0)
            proposals = insights.get("proposals", 0)
            if proposals > 0:
                failure_rate = float(failed) / max(1.0, proposals)
            else:
                failure_rate = 0.0

            if failure_rate > 0.25 and proposals >= 3:
                actions.append({"action": "investigate_failures", "reason": f"High failure rate {failure_rate:.2f} for '{insights.get('cluster_key')}'", "severity": "high"})

            # If a skill produces many proposals but low execution, consider tuning policy thresholds
            if proposals >= 5 and executed / max(1.0, proposals) < 0.2:
                actions.append({"action": "review_skill_policy_thresholds", "reason": "Many proposals are ignored or asked", "severity": "medium", "skill": insights.get("cluster_key")})

            # If errors include orchestrator issues
            if insights.get("errors", 0) > 0:
                actions.append({"action": "check_orchestrator_connectivity", "reason": "Errors reported in outcomes", "severity": "medium"})

            # If repeated errors contain a message, surface it
            if insights.get("common_window"):
                actions.append({"action": "inspect_context_window", "reason": "Recurring window may be contributing", "severity": "low", "window_title": insights.get("common_window")})
        except Exception:
            logger.exception("propose_actions_from_insights failed")
        return actions

    def _make_reflection_record(self, summary: str, insights: Dict[str, Any], suggested_actions: List[Dict[str, Any]], source_window: Optional[str] = None) -> Dict[str, Any]:
        if PydanticAvailable:
            rec = ReflectionRecord(summary=summary, insights=insights, suggested_actions=suggested_actions, source_window=source_window).model_dump()
            return rec
        else:
            rec = ReflectionRecord(summary=summary, insights=insights, suggested_actions=suggested_actions, source_window=source_window)
            # ensure dict shape
            if isinstance(rec, dict):
                return rec
            else:
                return dict(rec)

    # --------------------
    # persistence & publication
    # --------------------
    async def _persist_reflection(self, rec: Dict[str, Any]):
        """
        Persist reflection as:
         - episodic_store.append(...) with type 'reflection'
         - optionally insert the summary into vector_store for semantic memory
        """
        try:
            # write to episodic_store
            if self.episodic_store and getattr(self.episodic_store, "append", None):
                try:
                    self.episodic_store.append({"ts": rec.get("ts", time.time()), "type": "reflection", "payload": rec})
                except Exception:
                    logger.exception("episodic_store.append failed for reflection")

            # insert semantic memory: upsert summary embedding + metadata
            if self.vector_store and self.embeddings and getattr(self.embeddings, "embed", None):
                try:
                    emb = self.embeddings.embed(rec.get("summary", ""))
                    if asyncio.iscoroutine(emb):
                        emb = await asyncio.wait_for(emb, timeout=self.config.embedding_timeout)
                    if emb is not None:
                        mem_id = f"reflection_{int(rec.get('ts', time.time()) * 1000)}"
                        if getattr(self.vector_store, "upsert", None):
                            self.vector_store.upsert(mem_id, emb, {"type": "reflection", "summary": rec.get("summary")})
                        elif getattr(self.vector_store, "add", None):
                            self.vector_store.add(mem_id, emb, {"type": "reflection", "summary": rec.get("summary")})
                except Exception:
                    logger.debug("Embedding/upsert failed for reflection (non-fatal)")

            # publish a 'reflection.lesson' event for each suggested action
            if self.event_bus and getattr(self.event_bus, "publish", None):
                try:
                    for action in rec.get("suggested_actions", []):
                        payload = {"type": "reflection.lesson", "ts": time.time(), "action": action, "summary": rec.get("summary")}
                        try:
                            self.event_bus.publish("reflection.lesson", payload)
                        except Exception:
                            logger.debug("event_bus.publish reflection.lesson failed (ignored)")
                except Exception:
                    logger.exception("publishing reflection lessons failed")
        except Exception:
            logger.exception("Failed persisting reflection record")

    # --------------------
    # watermark helpers
    # --------------------
    def _load_watermark(self):
        try:
            if self.session_manager and getattr(self.session_manager, "get", None):
                sid = self.default_session_id
                if sid:
                    sess = self.session_manager.get(sid)
                    if sess:
                        ctx = sess.get("context", {}) or {}
                        self._last_reflection_ts = float(ctx.get(self._watermark_key, 0.0))
        except Exception:
            logger.debug("Failed loading reflection watermark")

    def _update_watermark(self, ts: float):
        try:
            self._last_reflection_ts = max(self._last_reflection_ts, float(ts or time.time()))
            # persist to session_manager
            if self.session_manager:
                try:
                    sid = self.default_session_id
                    if sid:
                        self.session_manager.update(sid, context={self._watermark_key: self._last_reflection_ts})
                except Exception:
                    # fallback direct save
                    try:
                        s = self.session_manager.get(self.default_session_id) or {}
                        ctx = s.get("context", {}) or {}
                        ctx[self._watermark_key] = self._last_reflection_ts
                        self.session_manager._enqueue_save(self.default_session_id, s)
                    except Exception:
                        logger.debug("Reflection watermark persist fallback failed")
        except Exception:
            logger.exception("update watermark failed")

    # --------------------
    # small utilities & event publishing
    # --------------------
    def _publish_event(self, topic: str, payload: Dict[str, Any]):
        try:
            if self.event_bus and getattr(self.event_bus, "publish", None):
                try:
                    self.event_bus.publish(topic, payload)
                except Exception:
                    logger.debug("event_bus.publish failed for topic %s", topic)
        except Exception:
            logger.exception("publish failed")

    # --------------------
    # convenience: get recent persisted reflections
    # --------------------
    async def get_recent_reflections(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Best-effort read reflections from episodic_store.
        """
        out = []
        try:
            if not self.episodic_store:
                return out
            if getattr(self.episodic_store, "query", None):
                res = self.episodic_store.query({"type": "reflection", "limit": limit})
                return list(res or [])
            # fallback: iterate and filter
            all_items = []
            if getattr(self.episodic_store, "iter", None):
                for r in self.episodic_store.iter(0):
                    all_items.append(r)
            else:
                if getattr(self.episodic_store, "get_recent", None):
                    all_items = self.episodic_store.get_recent(1000)
            for item in reversed(all_items):
                if item.get("type") == "reflection":
                    out.append(item)
                    if len(out) >= limit:
                        break
        except Exception:
            logger.exception("get_recent_reflections failed")
        return out
