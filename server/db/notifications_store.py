"""Durable in-app notification inbox for Gajala.

Pushes used to be transient — an FCM ping and nothing to look back at. This is the
log behind the app's Alerts tab: every inbox-worthy event (a night job needing
your decision, a job deployed/failed, the morning report, "new build ready", a
fired reminder) is stored here as well as pushed, so the tab and the push always
agree. Written through `server/notifier.py`, never directly by feature code.

Stored at ~/.codeasachat/notifications.db (mirrors reminders_store's shape).
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "notifications.db"

# Column 'type':   queue_input | queue_status | night_report | gajala_update | reminder
# Column 'status': unread | read | answered | dismissed


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def init() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                type           TEXT NOT NULL,
                title          TEXT NOT NULL,
                body           TEXT NOT NULL DEFAULT '',
                data           TEXT,
                status         TEXT NOT NULL DEFAULT 'unread',
                needs_response INTEGER NOT NULL DEFAULT 0,
                response       TEXT,
                ref_kind       TEXT,
                ref_id         INTEGER,
                created_at     REAL NOT NULL,
                updated_at     REAL NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_notif_status "
                  "ON notifications (status, id DESC)")
        c.commit()


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    try:
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
    except (TypeError, json.JSONDecodeError):
        d["data"] = {}
    return d


def add(*, type: str, title: str, body: str = "", data: dict | None = None,
        needs_response: bool = False, ref_kind: str | None = None,
        ref_id: int | None = None) -> int:
    init()
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO notifications (type, title, body, data, status, "
            "needs_response, ref_kind, ref_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'unread', ?, ?, ?, ?, ?)",
            (type, title, body, json.dumps(data or {}), 1 if needs_response else 0,
             ref_kind, ref_id, now, now),
        )
        c.commit()
        return int(cur.lastrowid)


def list(status: str | None = None, limit: int = 50) -> builtins.list[dict]:
    init()
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT * FROM notifications WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]


def get(notif_id: int) -> dict | None:
    init()
    with _conn() as c:
        return _row(c.execute("SELECT * FROM notifications WHERE id = ?",
                              (notif_id,)).fetchone())


def unread_count() -> int:
    init()
    with _conn() as c:
        return int(c.execute(
            "SELECT COUNT(*) FROM notifications WHERE status = 'unread'"
        ).fetchone()[0])


def mark_read(notif_id: int) -> None:
    _set(notif_id, status="read")


def mark_all_read() -> None:
    init()
    with _conn() as c:
        c.execute("UPDATE notifications SET status = 'read', updated_at = ? "
                  "WHERE status = 'unread'", (time.time(),))
        c.commit()


def set_response(notif_id: int, response: str) -> None:
    _set(notif_id, status="answered", response=response)


def dismiss(notif_id: int) -> bool:
    init()
    with _conn() as c:
        cur = c.execute(
            "UPDATE notifications SET status = 'dismissed', updated_at = ? WHERE id = ?",
            (time.time(), notif_id))
        c.commit()
        return cur.rowcount > 0


def _set(notif_id: int, **fields) -> None:
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k} = ?" for k in fields)
    init()
    with _conn() as c:
        c.execute(f"UPDATE notifications SET {sets} WHERE id = ?",
                  (*fields.values(), notif_id))
        c.commit()
