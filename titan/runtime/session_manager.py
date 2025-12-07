# Path: FLOW/titan/runtime/session_manager.py
from __future__ import annotations
import os
import json
import sqlite3
import threading
import time
import uuid
import queue
import logging
from typing import Dict, Any, Optional, Callable, Iterable, List, Tuple

# Observability imports
from titan.observability.tracing import tracer
from titan.observability.metrics import metrics

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

DEFAULT_DB = "data/sessions.db"
DEFAULT_DIR = "data/sessions"  # used for snapshots/exports


# -------------------------
# Storage Adapter Interface
# -------------------------
class StorageAdapter:
    """
    Minimal storage adapter interface so SessionManager can be swapped easily.
    Current default implementation uses SQLite (built-in).
    Custom adapters should implement the same methods.
    """

    def init(self):
        raise NotImplementedError()

    def save_session(self, session_id: str, data: Dict[str, Any]):
        raise NotImplementedError()

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError()

    def delete_session(self, session_id: str):
        raise NotImplementedError()

    def list_session_ids(self) -> Iterable[str]:
        raise NotImplementedError()

    def export_all(self) -> List[Tuple[str, Dict[str, Any]]]:
        raise NotImplementedError()

    def close(self):
        raise NotImplementedError()


# -------------------------
# SQLite StorageAdapter
# -------------------------
class SQLiteStorageAdapter(StorageAdapter):
    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()

    def init(self):
        with self._lock:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self._ensure_tables()

    def _ensure_tables(self):
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    version INTEGER,
                    metadata TEXT,
                    context_json TEXT,
                    provenance_json TEXT,
                    created_at REAL,
                    updated_at REAL
                )
                """
            )

    def save_session(self, session_id: str, data: Dict[str, Any]):
        with self._lock:
            now = time.time()
            version = data.get("_version", 1)
            metadata = json.dumps(data.get("metadata", {}), ensure_ascii=False)
            context_json = json.dumps(data.get("context", {}), ensure_ascii=False)
            provenance_json = json.dumps(data.get("provenance", []), ensure_ascii=False)
            self.conn.execute(
                """
                INSERT INTO sessions (id, version, metadata, context_json, provenance_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    version=excluded.version,
                    metadata=excluded.metadata,
                    context_json=excluded.context_json,
                    provenance_json=excluded.provenance_json,
                    updated_at=excluded.updated_at
                """,
                (session_id, version, metadata, context_json, provenance_json, now, now),
            )
            self.conn.commit()

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id, version, metadata, context_json, provenance_json, created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "_version": row[1],
                "metadata": json.loads(row[2] or "{}"),
                "context": json.loads(row[3] or "{}"),
                "provenance": json.loads(row[4] or "[]"),
                "created_at": row[5],
                "updated_at": row[6],
            }

    def delete_session(self, session_id: str):
        with self._lock:
            self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.conn.commit()

    def list_session_ids(self) -> Iterable[str]:
        with self._lock:
            cur = self.conn.execute("SELECT id FROM sessions")
            for r in cur.fetchall():
                yield r[0]

    def export_all(self) -> List[Tuple[str, Dict[str, Any]]]:
        out = []
        with self._lock:
            cur = self.conn.execute(
                "SELECT id, version, metadata, context_json, provenance_json, created_at, updated_at FROM sessions"
            )
            for row in cur:
                out.append(
                    (
                        row[0],
                        {
                            "id": row[0],
                            "_version": row[1],
                            "metadata": json.loads(row[2] or "{}"),
                            "context": json.loads(row[3] or "{}"),
                            "provenance": json.loads(row[4] or "[]"),
                            "created_at": row[5],
                            "updated_at": row[6],
                        },
                    )
                )
        return out

    def close(self):
        with self._lock:
            if self.conn:
                try:
                    self.conn.commit()
                except Exception:
                    pass
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None


# -------------------------
# Enterprise SessionManager
# -------------------------
class SessionManager:
    """
    Enterprise-grade SessionManager.
    Integrated with:
      - distributed tracing
      - metrics
      - structured logging
    """

    def __init__(
        self,
        storage_adapter: Optional[StorageAdapter] = None,
        default_ttl_seconds: int = 60 * 60 * 24,
        autosave_interval_seconds: float = 2.0,
        sweeper_interval_seconds: float = 30.0,
        snapshot_dir: str = DEFAULT_DIR,
    ):
        self.storage = storage_adapter or SQLiteStorageAdapter()
        self.default_ttl = default_ttl_seconds
        self.autosave_interval = autosave_interval_seconds
        self.sweeper_interval = sweeper_interval_seconds
        self.snapshot_dir = snapshot_dir
        os.makedirs(self.snapshot_dir, exist_ok=True)

        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

        self._write_queue: "queue.Queue[Tuple[str, Dict[str, Any]]]" = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._writer_stop = threading.Event()

        self._sweeper_thread: Optional[threading.Thread] = None
        self._sweeper_stop = threading.Event()

        self._context_store = None
        self._trust_manager = None
        self._on_session_create_hooks: List[Callable[[Dict[str, Any]], None]] = []
        self._on_session_delete_hooks: List[Callable[[str], None]] = []

        self.storage.init()
        self._load_all_from_storage()

        self.start()

    # -------------------------
    # Lifecycle
    # -------------------------
    def start(self):
        with self._lock:
            if not (self._writer_thread and self._writer_thread.is_alive()):
                self._writer_stop.clear()
                self._writer_thread = threading.Thread(
                    target=self._writer_loop, daemon=True, name="SessionWriter"
                )
                self._writer_thread.start()

            if not (self._sweeper_thread and self._sweeper_thread.is_alive()):
                self._sweeper_stop.clear()
                self._sweeper_thread = threading.Thread(
                    target=self._sweeper_loop, daemon=True, name="SessionSweeper"
                )
                self._sweeper_thread.start()

    def stop(self):
        logger.info("[SessionManager] stopping", extra={
            "trace_id": tracer.current_trace_id(),
            "span_id": tracer.current_span_id(),
        })

        self._sweeper_stop.set()
        if self._sweeper_thread and self._sweeper_thread.is_alive():
            self._sweeper_thread.join(timeout=2.0)

        self._writer_stop.set()
        self._write_queue.put(None)
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5.0)

        # final sync flush
        with self._lock:
            for sid, data in list(self._sessions.items()):
                try:
                    self.storage.save_session(sid, data)
                except Exception:
                    logger.exception("Failed final save", extra={"session_id": sid})

        try:
            self.storage.close()
        except Exception:
            logger.exception("Failed closing session storage")

    # -------------------------
    # Internal helpers
    # -------------------------
    def _load_all_from_storage(self):
        try:
            items = self.storage.export_all()
            with self._lock:
                for sid, data in items:
                    meta = data.get("metadata", {})
                    meta.setdefault("_created_at", data.get("created_at", time.time()))
                    meta.setdefault("_ttl", meta.get("_ttl", self.default_ttl))
                    data["metadata"] = meta
                    self._sessions[sid] = data
        except Exception:
            logger.exception("Failed to load sessions from storage")

    def _writer_loop(self):
        logger.info("[SessionManager] writer thread started")
        c = metrics.counter("sessionmanager.writes")
        while not self._writer_stop.is_set():
            try:
                item = self._write_queue.get(timeout=self.autosave_interval)
            except queue.Empty:
                continue
            if item is None:
                if self._writer_stop.is_set():
                    break
                continue
            try:
                sid, data = item
                self.storage.save_session(sid, data)
                c.inc()
            except Exception:
                logger.exception("Writer failed saving", extra={"session_id": sid})
        logger.info("[SessionManager] writer thread exiting")

    def _sweeper_loop(self):
        logger.info("[SessionManager] sweeper thread started")
        expiry_counter = metrics.counter("sessionmanager.expired")
        while not self._sweeper_stop.is_set():
            now = time.time()
            expired = []
            with self._lock:
                for sid, sess in list(self._sessions.items()):
                    meta = sess.get("metadata", {})
                    created = meta.get("_created_at", now)
                    ttl = meta.get("_ttl", self.default_ttl)
                    sliding = meta.get("_sliding", False)
                    last_touch = meta.get("_last_touch", created)
                    expiry_time = last_touch + ttl if sliding else created + ttl
                    if now > expiry_time:
                        expired.append(sid)

            for sid in expired:
                logger.info("Session expired", extra={"session_id": sid})
                expiry_counter.inc()
                self.delete(sid)

            time.sleep(self.sweeper_interval)
        logger.info("[SessionManager] sweeper thread exiting")

    # -------------------------
    # Hooks & Integration
    # -------------------------
    def register_context_store(self, context_store):
        self._context_store = context_store

    def register_trust_manager(self, trust_manager):
        self._trust_manager = trust_manager

    def add_on_create_hook(self, fn: Callable[[Dict[str, Any]], None]):
        self._on_session_create_hooks.append(fn)

    def add_on_delete_hook(self, fn: Callable[[str], None]):
        self._on_session_delete_hooks.append(fn)

    # -------------------------
    # Session API (CRUD)
    # -------------------------
    def create(self, session_id: Optional[str] = None, initial_metadata: Optional[Dict[str, Any]] = None):
        with tracer.span("session.create"):
            sid = session_id or f"session_{uuid.uuid4().hex[:8]}"
            now = time.time()
            data = {
                "id": sid,
                "_version": 1,
                "metadata": {
                    "_created_at": now,
                    "_last_touch": now,
                    "_ttl": self.default_ttl,
                    "_sliding": False,
                    **(initial_metadata or {}),
                },
                "context": {},
                "provenance": [],
            }
            with self._lock:
                self._sessions[sid] = data

            metrics.counter("sessionmanager.created").inc()

            logger.info("Session created", extra={
                "session_id": sid,
                "trace_id": tracer.current_trace_id(),
            })

            for fn in self._on_session_create_hooks:
                try:
                    fn(data)
                except Exception:
                    logger.exception("Create hook failed", extra={"session_id": sid})

            self._enqueue_save(sid, data)
            return data

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with tracer.span("session.get"):
            with self._lock:
                sess = self._sessions.get(session_id)
                if not sess:
                    try:
                        loaded = self.storage.load_session(session_id)
                        if loaded:
                            self._sessions[session_id] = loaded
                            sess = loaded
                    except Exception:
                        logger.exception("Failed loading session", extra={"session_id": session_id})

                if sess:
                    meta = sess.setdefault("metadata", {})
                    meta["_last_touch"] = time.time()
                    metrics.counter("sessionmanager.touched").inc()
                    self._enqueue_save(session_id, sess)
                return sess

    def update(self, session_id: str, *, metadata: Optional[Dict[str, Any]] = None, context: Optional[Dict[str, Any]] = None):
        with tracer.span("session.update"):
            with self._lock:
                sess = self._sessions.get(session_id)
                if not sess:
                    raise KeyError(f"Unknown session {session_id}")

                if metadata:
                    sess["metadata"].update(metadata)
                if context:
                    sess_context = sess.setdefault("context", {})
                    sess_context.update(context)
                sess["_version"] = sess.get("_version", 1) + 1
                sess["metadata"]["_last_touch"] = time.time()

                metrics.counter("sessionmanager.updated").inc()
                self._enqueue_save(session_id, sess)

    def delete(self, session_id: str):
        with tracer.span("session.delete"):
            with self._lock:
                if session_id in self._sessions:
                    self._sessions.pop(session_id, None)

            try:
                self.storage.delete_session(session_id)
            except Exception:
                logger.exception("Failed deleting session", extra={"session_id": session_id})

            metrics.counter("sessionmanager.deleted").inc()

            for fn in self._on_session_delete_hooks:
                try:
                    fn(session_id)
                except Exception:
                    logger.exception("Delete hook failed", extra={"session_id": session_id})

            logger.info("Session deleted", extra={"session_id": session_id})

    # -------------------------
    # Provenance & Identity
    # -------------------------
    def append_provenance(self, session_id: str, entry: Dict[str, Any]):
        with tracer.span("session.provenance"):
            with self._lock:
                sess = self._sessions.get(session_id)
                if not sess:
                    raise KeyError(f"Unknown session {session_id}")

                # attach distributed tracing IDs
                entry["trace_id"] = tracer.current_trace_id()
                entry["span_id"] = tracer.current_span_id()
                entry["timestamp"] = time.time()

                p = sess.setdefault("provenance", [])
                p.append(entry)

                sess["_version"] = sess.get("_version", 1) + 1
                metrics.counter("sessionmanager.provenance_appended").inc()
                self._enqueue_save(session_id, sess)

    def bind_identity(self, session_id: str, identity: Dict[str, Any]):
        with tracer.span("session.identity"):
            with self._lock:
                sess = self._sessions.get(session_id)
                if not sess:
                    raise KeyError(f"Unknown session {session_id}")

                sess["metadata"]["identity"] = identity
                sess["_version"] = sess.get("_version", 1) + 1

                metrics.counter("sessionmanager.identity_bound").inc()
                self._enqueue_save(session_id, sess)

    def set_trust_level(self, session_id: str, trust_level: str):
        with tracer.span("session.trust"):
            with self._lock:
                sess = self._sessions.get(session_id)
                if not sess:
                    raise KeyError(f"Unknown session {session_id}")

                sess["metadata"]["trust_level"] = trust_level
                sess["_version"] = sess.get("_version", 1) + 1
                self._enqueue_save(session_id, sess)

                if self._trust_manager:
                    try:
                        self._trust_manager.enforce(session_id, sess)
                    except Exception:
                        logger.exception("Trust enforcement failed", extra={"session_id": session_id})

    # -------------------------
    # Snapshot
    # -------------------------
    def export_snapshot(self, path: Optional[str] = None) -> str:
        with tracer.span("session.snapshot.export"):
            path = path or os.path.join(self.snapshot_dir, f"sessions_snapshot_{int(time.time())}.json")
            items = self.storage.export_all()
            payload = {sid: data for sid, data in items}
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

            logger.info("Snapshot exported", extra={"path": path})
            return path

    def import_snapshot(self, path: str, overwrite: bool = False) -> int:
        with tracer.span("session.snapshot.import"):
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            count = 0

            for sid, data in payload.items():
                if not overwrite and sid in self._sessions:
                    continue
                try:
                    self.storage.save_session(sid, data)
                    with self._lock:
                        self._sessions[sid] = data
                    count += 1
                except Exception:
                    logger.exception("Failed importing session", extra={"session_id": sid})

            logger.info("Snapshot imported", extra={"count": count})
            return count

    # -------------------------
    # Utilities
    # -------------------------
    def _enqueue_save(self, session_id: str, data: Dict[str, Any]):
        try:
            copy_data = {
                "id": data.get("id"),
                "_version": data.get("_version"),
                "metadata": data.get("metadata"),
                "context": data.get("context"),
                "provenance": data.get("provenance"),
            }
            self._write_queue.put((session_id, copy_data), block=False)
        except queue.Full:
            logger.warning("Write queue full", extra={"session_id": session_id})

    def snapshot_and_compact(self, path: Optional[str] = None) -> str:
        snap = self.export_snapshot(path)
        try:
            if isinstance(self.storage, SQLiteStorageAdapter) and self.storage.conn:
                with self.storage.conn:
                    self.storage.conn.execute("VACUUM;")
        except Exception:
            logger.exception("Failed compaction")
        return snap

    def as_dict(self, session_id: str) -> Dict[str, Any]:
        return self.get(session_id) or {}

    def close(self):
        self.stop()
