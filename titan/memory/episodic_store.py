# Path: FLOW/titan/memory/episodic_store.py
from __future__ import annotations
import os
import json
import sqlite3
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime
import logging

from titan.schemas.events import Event

logger = logging.getLogger(__name__)


class EpisodicStore:
    """
    Stores executor/planner events:
      - sqlite table `events` for structured queries
      - append-only JSON Lines file (provenance.jl) for replay/audit

    Methods:
      - write_event(Event)
      - query_events(filter...) -> List[Event]
    """

    def __init__(self, db_path: str = "data/memory.db", provenance_path: str = "data/provenance.jl"):
        self._lock = threading.RLock()
        self._db_path = db_path
        self._provenance_path = provenance_path

        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    type TEXT,
                    timestamp TEXT,
                    session_id TEXT,
                    plan_id TEXT,
                    node_id TEXT,
                    payload TEXT,
                    metadata TEXT
                )
                """
            )
            self._conn.commit()

    def write_event(self, event: Event) -> None:
        """Persist event to sqlite and append to provenance.jl"""
        with self._lock:
            cur = self._conn.cursor()
            payload_json = json.dumps(event.payload)
            metadata_json = json.dumps(event.metadata)
            eid = event.id or f"evt_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO events (id, type, timestamp, session_id, plan_id, node_id, payload, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (eid, event.type.value, event.timestamp, event.session_id, event.plan_id, event.node_id, payload_json, metadata_json),
                )
                self._conn.commit()
            except Exception as e:
                logger.exception("Failed to write event to sqlite: %s", e)

            # Append to provenance.jl (append-only)
            entry = event.to_provenance_entry(previous_hash=None)  # kernel will link previous_hash if needed
            try:
                with open(self._provenance_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.exception("Failed to append provenance entry: %s", e)

    def query_events(self, plan_id: Optional[str] = None, session_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            sql = "SELECT id, type, timestamp, session_id, plan_id, node_id, payload, metadata FROM events"
            params = []
            clauses = []
            if plan_id:
                clauses.append("plan_id = ?")
                params.append(plan_id)
            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall()
            out = []
            for id_, typ, ts, sid, pid, nid, payload_json, metadata_json in rows:
                try:
                    payload = json.loads(payload_json) if payload_json else {}
                except Exception:
                    payload = {}
                try:
                    metadata = json.loads(metadata_json) if metadata_json else {}
                except Exception:
                    metadata = {}
                out.append({
                    "id": id_,
                    "type": typ,
                    "timestamp": ts,
                    "session_id": sid,
                    "plan_id": pid,
                    "node_id": nid,
                    "payload": payload,
                    "metadata": metadata,
                })
            return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
