"""
SQLite-backed reminders. A background scheduler fires the ones whose due_at
has passed, pushing a Telegram message, then marks them fired.

Stored at ~/.codeasachat/reminders.db
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path.home() / ".codeasachat" / "reminders.db"


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
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT NOT NULL,
                due_at      REAL NOT NULL,
                chat_id     INTEGER,
                project     TEXT,
                fired       INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(due_at, fired)")
        c.commit()


_init()


def add(text: str, due_at: float, chat_id: int | None = None,
        project: str | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO reminders (text, due_at, chat_id, project, fired, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (text, due_at, chat_id, project, time.time()),
        )
        c.commit()
        return cur.lastrowid


def due_now(now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_pending(limit: int = 20) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM reminders WHERE fired = 0 ORDER BY due_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_fired(reminder_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("UPDATE reminders SET fired = 1 WHERE id = ?", (reminder_id,))
        c.commit()
        return cur.rowcount > 0


def delete(reminder_id: int) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        c.commit()
        return cur.rowcount > 0
