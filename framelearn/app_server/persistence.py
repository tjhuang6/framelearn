"""SQLite persistence for FrameLearn app-server sessions.

Write order (exactly once):
  1. user message written immediately on input
  2. assistant/tool messages written in batch after turn completes
  3. RuntimeAdapter sets persisted=True; upper layers must not write again
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    thread_id   TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    content          TEXT,
    tool_calls       TEXT,
    tool_call_id     TEXT,
    reasoning        TEXT,
    codex_thread_id  TEXT,
    codex_turn_id    TEXT,
    provider_item_id TEXT,
    created_at       REAL    NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    UNIQUE(session_id, provider_item_id, role)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
"""


class SessionDB:
    def __init__(self, db_path: Optional[str] = None, enabled: bool = True):
        self.enabled = enabled
        if not enabled:
            self.conn = None
            return
        
        if db_path is None:
            import os
            db_path = os.getenv(
                "FRAMELEARN_SESSION_DB",
                str(Path.home() / ".framelearn" / "sessions.db")
            )
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    def create_session(self, session_id: str, title: str = "", thread_id: str = ""):
        if not self.enabled or not self.conn:
            return
        now = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, thread_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, title, thread_id, now, now),
        )
        self.conn.commit()

    def update_session_thread(self, session_id: str, thread_id: str):
        if not self.enabled or not self.conn:
            return
        self.conn.execute(
            "UPDATE sessions SET thread_id=?, updated_at=? WHERE id=?",
            (thread_id, time.time(), session_id),
        )
        self.conn.commit()

    def update_session_title(self, session_id: str, title: str):
        if not self.enabled or not self.conn:
            return
        self.conn.execute(
            "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
            (title, time.time(), session_id),
        )
        self.conn.commit()

    def list_sessions(self) -> list[sqlite3.Row]:
        if not self.enabled or not self.conn:
            return []
        cur = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        )
        return cur.fetchall()

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def append_message(
        self,
        session_id: str,
        role: str,
        content: Optional[str] = None,
        tool_calls: Optional[list] = None,
        tool_call_id: Optional[str] = None,
        reasoning: Optional[dict] = None,
        codex_thread_id: Optional[str] = None,
        codex_turn_id: Optional[str] = None,
        provider_item_id: Optional[str] = None,
    ) -> Optional[int]:
        """Insert a message row. Returns the new row id, or None on duplicate."""
        if not self.enabled or not self.conn:
            return None
        
        import json as _json

        try:
            cur = self.conn.execute(
                """INSERT INTO messages
                   (session_id, role, content, tool_calls, tool_call_id,
                    reasoning, codex_thread_id, codex_turn_id, provider_item_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    content,
                    _json.dumps(tool_calls) if tool_calls else None,
                    tool_call_id,
                    _json.dumps(reasoning) if reasoning else None,
                    codex_thread_id,
                    codex_turn_id,
                    provider_item_id,
                    time.time(),
                ),
            )
            self.conn.commit()
            self.conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?",
                (time.time(), session_id),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Duplicate (session_id, provider_item_id, role) — already written
            return None

    def get_messages(self, session_id: str) -> list[sqlite3.Row]:
        if not self.enabled or not self.conn:
            return []
        cur = self.conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at",
            (session_id,),
        )
        return cur.fetchall()

    def close(self):
        if self.conn:
            self.conn.close()
