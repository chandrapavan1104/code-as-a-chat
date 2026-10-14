"""Remote-shell device binding and audit log (~/.codeasachat/shell.db).

The shared API token is held by every client (the app, Telegram, widgets), so
it cannot by itself say "this is the owner's phone". The remote shell adds a
second, per-device secret: the phone generates it, the server keeps only its
SHA-256, and exactly one device may be bound at a time. The first device to
ask is bound (trust on first use, announced by a push); any other device is
refused until the binding is removed from that device or on the Mac
(`scripts/remote-shell unbind`).

The audit log records what was typed, never the output, and never input typed
while the terminal had echo off (password prompts).
"""

import hashlib
import hmac
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "shell.db"


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
        c.executescript("""
            CREATE TABLE IF NOT EXISTS shell_binding (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                key_hash   TEXT NOT NULL,
                label      TEXT NOT NULL,
                bound_at   REAL NOT NULL,
                last_used  REAL
            );
            CREATE TABLE IF NOT EXISTS shell_audit (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         REAL NOT NULL,
                session_id TEXT,
                device     TEXT,
                kind       TEXT NOT NULL,
                cwd        TEXT,
                text       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_shell_audit_ts ON shell_audit (ts);
        """)
        c.commit()


_init()


def key_hash(device_key: str) -> str:
    return hashlib.sha256(device_key.encode()).hexdigest()


def binding() -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM shell_binding WHERE id = 1").fetchone()
    return dict(row) if row else None


def matches(device_key: str | None) -> bool:
    """Constant-time check that [device_key] is the bound device's."""
    bound = binding()
    if not bound or not device_key:
        return False
    return hmac.compare_digest(bound["key_hash"], key_hash(device_key))


def bind(device_key: str, label: str) -> bool:
    """Bind when nothing is bound. False if another device already holds it."""
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO shell_binding (id, key_hash, label, bound_at) "
            "VALUES (1, ?, ?, ?)", (key_hash(device_key), label[:80], time.time()))
        c.commit()
        return cur.rowcount == 1


def unbind() -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM shell_binding WHERE id = 1")
        c.commit()
        return cur.rowcount == 1


def touch() -> None:
    with _conn() as c:
        c.execute("UPDATE shell_binding SET last_used = ? WHERE id = 1", (time.time(),))
        c.commit()


def audit(kind: str, *, session_id: str | None = None, device: str | None = None,
          cwd: str | None = None, text: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO shell_audit (ts, session_id, device, kind, cwd, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), session_id, device, kind, cwd, (text or "")[:2000] or None))
        c.commit()


def recent_audit(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM shell_audit ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [dict(r) for r in rows]
