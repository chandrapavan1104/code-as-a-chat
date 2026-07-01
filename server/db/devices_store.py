"""
Registered app devices (their FCM tokens) — the scheduler pushes reminders /
alerts to these alongside Telegram. Stored at ~/.codeasachat/devices.db
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "devices.db"


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
            CREATE TABLE IF NOT EXISTS devices (
                fcm_token   TEXT PRIMARY KEY,
                platform    TEXT NOT NULL DEFAULT 'android',
                label       TEXT,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
        """)
        c.commit()


_init()


def upsert(fcm_token: str, platform: str = "android", label: str | None = None) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO devices (fcm_token, platform, label, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(fcm_token) DO UPDATE SET platform=excluded.platform, "
            "label=excluded.label, updated_at=excluded.updated_at",
            (fcm_token, platform, label, now, now),
        )
        c.commit()


def all_tokens() -> list[str]:
    with _conn() as c:
        rows = c.execute("SELECT fcm_token FROM devices").fetchall()
    return [r["fcm_token"] for r in rows]


def count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM devices").fetchone()[0]


def remove(fcm_token: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM devices WHERE fcm_token = ?", (fcm_token,))
        c.commit()
        return cur.rowcount > 0
