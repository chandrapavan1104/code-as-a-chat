"""
SQLite-backed conversation memory for the shell skill.

One row per turn (user or assistant). Sessions are namespaced by the client —
e.g. "tg:<chat_id>" for Telegram chats — so a future Android client can use
its own scheme without collisions.

Storage path: ~/.codeasachat/conversations.db
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path.home() / ".codeasachat" / "conversations.db"


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    try:
        yield c
    finally:
        c.close()


def _init() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT    NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                ts         REAL    NOT NULL
            )
        """)
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_ts "
            "ON conversations(session_id, ts)"
        )
        c.commit()


_init()


def append_turn(session_id: str, role: str, content: str) -> None:
    if not session_id or not role or not content:
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO conversations (session_id, role, content, ts) "
            "VALUES (?, ?, ?, ?)",
            (session_id, role, content, time.time()),
        )
        c.commit()


def get_recent(session_id: str, n: int = 5) -> list[dict]:
    """Return up to `n` *turn pairs* (so up to 2n rows), chronological order."""
    if not session_id or n <= 0:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content, ts FROM conversations "
            "WHERE session_id = ? "
            "ORDER BY ts DESC LIMIT ?",
            (session_id, n * 2),
        ).fetchall()
    return [
        {"role": r[0], "content": r[1], "ts": r[2]}
        for r in reversed(rows)
    ]


def clear(session_id: str) -> int:
    if not session_id:
        return 0
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM conversations WHERE session_id = ?", (session_id,)
        )
        c.commit()
        return cur.rowcount


def count(session_id: str) -> int:
    if not session_id:
        return 0
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else 0
