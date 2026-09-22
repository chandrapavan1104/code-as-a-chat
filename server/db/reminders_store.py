"""
SQLite-backed reminders. A background scheduler fires the ones whose due_at
has passed, pushing a Telegram message, then marks them fired.

Stored at ~/.codeasachat/reminders.db
"""

import sqlite3
import time
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo


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
                recurrence  TEXT NOT NULL DEFAULT 'none',
                timezone    TEXT NOT NULL DEFAULT 'UTC',
                until_note_id INTEGER,
                app_notified INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL
            )
        """)
        columns = {row[1] for row in c.execute("PRAGMA table_info(reminders)")}
        migrations = {
            "recurrence": "ALTER TABLE reminders ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none'",
            "timezone": "ALTER TABLE reminders ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'",
            "until_note_id": "ALTER TABLE reminders ADD COLUMN until_note_id INTEGER",
            "app_notified": "ALTER TABLE reminders ADD COLUMN app_notified INTEGER NOT NULL DEFAULT 0",
        }
        for name, statement in migrations.items():
            if name not in columns:
                c.execute(statement)
        c.execute("CREATE INDEX IF NOT EXISTS idx_rem_due ON reminders(due_at, fired)")
        c.commit()


def _norm_text(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _next_daily_due(due_at: float, timezone: str) -> float:
    """Return the next occurrence at the same local wall-clock time.

    Constructing the next occurrence in the named zone (rather than adding
    86400 seconds) keeps a 9am reminder at 9am across DST transitions.
    Nonexistent spring-forward times are moved to the first valid minute.
    """
    try:
        zone = ZoneInfo(timezone)
    except (KeyError, ValueError):
        zone = ZoneInfo("UTC")
    local = datetime.fromtimestamp(due_at, zone)
    target = (local.replace(tzinfo=None) + timedelta(days=1)).replace(tzinfo=zone, fold=0)
    for _ in range(180):
        roundtrip = datetime.fromtimestamp(target.timestamp(), zone)
        if roundtrip.replace(tzinfo=None) == target.replace(tzinfo=None):
            return target.timestamp()
        target += timedelta(minutes=1)
    return target.timestamp()


def add(text: str, due_at: float, chat_id: int | None = None,
        project: str | None = None, *, recurrence: str = "none",
        timezone: str = "UTC", until_note_id: int | None = None) -> int:
    _init()
    recurrence = (recurrence or "none").strip().lower()
    timezone = (timezone or "UTC").strip()
    if recurrence not in {"none", "daily"}:
        raise ValueError("recurrence must be 'none' or 'daily'")
    try:
        ZoneInfo(timezone)
    except (KeyError, ValueError):
        raise ValueError(f"unknown timezone: {timezone}")
    normalized = _norm_text(text)
    with _conn() as c:
        candidates = c.execute(
            "SELECT id, text FROM reminders WHERE fired = 0 AND due_at = ? "
            "AND COALESCE(chat_id, 0) = COALESCE(?, 0) AND COALESCE(project, '') = COALESCE(?, '') "
            "AND recurrence = ? AND timezone = ? AND COALESCE(until_note_id, 0) = COALESCE(?, 0)",
            (due_at, chat_id, project, recurrence, timezone, until_note_id),
        ).fetchall()
        for candidate in candidates:
            if _norm_text(candidate["text"]) == normalized:
                return int(candidate["id"])
        cur = c.execute(
            "INSERT INTO reminders (text, due_at, chat_id, project, fired, recurrence, "
            "timezone, until_note_id, app_notified, created_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0, ?)",
            (text, due_at, chat_id, project, recurrence, timezone, until_note_id, time.time()),
        )
        c.commit()
        return cur.lastrowid


def due_now(now: float | None = None) -> list[dict]:
    _init()
    now = now if now is not None else time.time()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM reminders WHERE fired = 0 AND due_at <= ? ORDER BY due_at",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_pending(limit: int = 20) -> list[dict]:
    _init()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM reminders WHERE fired = 0 ORDER BY due_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_fired(reminder_id: int) -> bool:
    """Legacy one-shot completion API; recurring rows advance instead."""
    return complete_delivery(reminder_id)


def mark_app_notified(reminder_id: int) -> bool:
    _init()
    with _conn() as c:
        cur = c.execute(
            "UPDATE reminders SET app_notified = 1 WHERE id = ? AND fired = 0 AND app_notified = 0",
            (reminder_id,),
        )
        c.commit()
        return cur.rowcount > 0


def complete_delivery(reminder_id: int, now: float | None = None) -> bool:
    """Complete the current occurrence, advancing a daily row once."""
    _init()
    now = now if now is not None else time.time()
    with _conn() as c:
        row = c.execute("SELECT * FROM reminders WHERE id = ? AND fired = 0", (reminder_id,)).fetchone()
        if row is None:
            return False
        if row["recurrence"] == "daily":
            next_due = _next_daily_due(row["due_at"], row["timezone"])
            # A host may be asleep for several days. Consume missed calendar
            # occurrences in one transaction so the scheduler does not replay
            # one notification per day after it wakes.
            while next_due <= now:
                next_due = _next_daily_due(next_due, row["timezone"])
            c.execute("UPDATE reminders SET due_at = ?, app_notified = 0 WHERE id = ? AND fired = 0",
                      (next_due, reminder_id))
        else:
            c.execute("UPDATE reminders SET fired = 1, app_notified = 1 WHERE id = ? AND fired = 0",
                      (reminder_id,))
        c.commit()
        return True


def delete(reminder_id: int) -> bool:
    _init()
    with _conn() as c:
        cur = c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        c.commit()
        return cur.rowcount > 0


def cancel(reminder_id: int) -> bool:
    """Stop a recurring reminder while retaining its row for audit/list safety."""
    _init()
    with _conn() as c:
        cur = c.execute("UPDATE reminders SET fired = 1 WHERE id = ? AND fired = 0", (reminder_id,))
        c.commit()
        return cur.rowcount > 0
