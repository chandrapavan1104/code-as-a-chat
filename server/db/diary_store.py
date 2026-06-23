"""
SQLite-backed personal diary — the data layer for the diary/Anna skill.

A conversation log: user entries + Anna's (mentor) replies, each tagged with a
life category. Fully local at ~/.codeasachat/diary.db — same backup family as
notes.db / conversations.db.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path.home() / ".codeasachat" / "diary.db"

CATEGORIES = {"health", "finance", "love", "career", "desires", "future", "general"}


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _init() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL,              -- 'user' | 'anna'
                category    TEXT NOT NULL DEFAULT 'general',
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_diary_cat ON entries(category, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_diary_ts  ON entries(created_at)")
        c.commit()


_init()


def add(role: str, category: str, content: str) -> int:
    if category not in CATEGORIES:
        category = "general"
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO entries (role, category, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (role, category, content, time.time()),
        )
        c.commit()
        return cur.lastrowid


def recent(limit: int = 12) -> list[dict]:
    """Most recent rows, returned in chronological order."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entries ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def by_category(category: str, limit: int = 10) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entries WHERE category = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (category, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def search(query: str, limit: int = 15) -> list[dict]:
    q = f"%{query}%"
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entries WHERE content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (q, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def since(ts: float) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entries WHERE created_at >= ? ORDER BY created_at",
            (ts,),
        ).fetchall()
    return [dict(r) for r in rows]


def counts() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute(
            "SELECT category, COUNT(*) AS n FROM entries "
            "WHERE role = 'user' GROUP BY category"
        ).fetchall()
    return {r["category"]: r["n"] for r in rows}
