"""Session-Store — persistente Gesprächs-Sessions via SQLite.

Jede Session speichert: Turns (Query/Answer/Zone/Meta), Summary (auto-generated),
erstellt/aktualisiert-Timestamps. Auto-Summarization: alle N Turns wird der
bisherige Kontext per kleinem LLM auf wenige Kernfakten verdichtet.

Transportfrei, direkt testbar — kein Redis nötig.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from collect.config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    zone TEXT NOT NULL DEFAULT '',
    best_distance REAL,
    duration_s REAL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT '',
    turns_covered INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


class SessionStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or settings.session_db)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.executescript(SCHEMA)
                conn.execute("PRAGMA foreign_keys = ON")
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, session_id: str, title: str = "") -> dict:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now))
            conn.commit()
            return {"id": session_id, "title": title, "created_at": now, "turn_count": 0}
        finally:
            conn.close()

    def list_sessions(self, limit: int = 20) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM turns WHERE session_id = s.id) AS turn_count "
                "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get(self, session_id: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?",
                               (session_id,)).fetchone()
            if not row:
                return None
            turns = conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,)).fetchall()
            summary_row = conn.execute(
                "SELECT * FROM summaries WHERE session_id = ?", (session_id,)).fetchone()
            return {
                **dict(row),
                "turns": [dict(t) for t in turns],
                "summary": dict(summary_row) if summary_row else None,
            }
        finally:
            conn.close()

    def add_turn(self, session_id: str, query: str, answer: str,
                 zone: str = "", best_distance: Optional[float] = None,
                 duration_s: Optional[float] = None, meta: Optional[dict] = None) -> int:
        now = time.time()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO turns (session_id, query, answer, zone, best_distance, "
                "duration_s, meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, query, answer, zone, best_distance, duration_s,
                 json.dumps(meta or {}, ensure_ascii=False), now))
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?",
                         (now, session_id))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def get_turns(self, session_id: str, limit: int = 5) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT query, answer FROM turns WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit)).fetchall()
            return [{"q": r["query"], "a": r["answer"]} for r in reversed(rows)]
        finally:
            conn.close()

    def get_summary(self, session_id: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT content FROM summaries WHERE session_id = ?",
                (session_id,)).fetchone()
            return row["content"] if row and row["content"] else None
        finally:
            conn.close()

    def set_summary(self, session_id: str, content: str, turns_covered: int) -> None:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO summaries (session_id, content, turns_covered, "
                "created_at) VALUES (?, ?, ?, ?)",
                (session_id, content, turns_covered, now))
            conn.commit()
        finally:
            conn.close()

    def turn_count(self, session_id: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM turns WHERE session_id = ?",
                (session_id,)).fetchone()
            return row["n"] if row else 0
        finally:
            conn.close()

    def delete(self, session_id: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def needs_summary(self, session_id: str) -> bool:
        count = self.turn_count(session_id)
        if count < settings.session_max_turns:
            return False
        summary = self.get_summary(session_id)
        if summary is None:
            return True
        summary_row = self._get_summary_row(session_id)
        covered = summary_row["turns_covered"] if summary_row else 0
        return count > covered

    def _get_summary_row(self, session_id: str) -> Optional[dict]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM summaries WHERE session_id = ?", (session_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def build_context(self, session_id: str) -> list[dict]:
        turns = self.get_turns(session_id)
        summary = self.get_summary(session_id)
        if summary:
            history = [{"q": "_summary", "a": summary}]
            history.extend(turns)
            return history
        return turns
