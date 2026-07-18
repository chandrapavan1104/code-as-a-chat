"""
SQLite-backed error/crash capture — the fix agent's eyes.

Server exceptions and app (Gajala) crashes land here so the `errors` skill and
the fix agent can read what actually broke instead of relying on a description.

Light dedup (identical source+message within 30s is dropped) keeps error loops
from flooding the table, and it's pruned to the most recent MAX_ROWS.

Storage path: ~/.codeasachat/errors.db
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "errors.db"
MAX_ROWS = 500
_DEDUP_WINDOW = 30  # seconds


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
            CREATE TABLE IF NOT EXISTS errors (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL    NOT NULL,
                source  TEXT    NOT NULL,   -- server | app
                kind    TEXT,               -- exception | http_500 | flutter | dio ...
                message TEXT    NOT NULL,
                detail  TEXT,               -- traceback / stack
                context TEXT                -- json blob
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts)")
        c.commit()


_init()


def add(source: str, kind: str, message: str,
        detail: str = "", context: dict | None = None) -> None:
    """Record one error. Best-effort; never raises."""
    message = (message or "").strip()
    if not message:
        return
    try:
        now = time.time()
        with _conn() as c:
            row = c.execute(
                "SELECT ts FROM errors WHERE source=? AND message=? "
                "ORDER BY ts DESC LIMIT 1", (source, message),
            ).fetchone()
            if row and now - row[0] < _DEDUP_WINDOW:
                return  # same error just landed — don't flood
            c.execute(
                "INSERT INTO errors (ts, source, kind, message, detail, context) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, source, kind, message, (detail or "")[:8000],
                 json.dumps(context or {})),
            )
            c.execute(
                "DELETE FROM errors WHERE id NOT IN "
                "(SELECT id FROM errors ORDER BY ts DESC LIMIT ?)", (MAX_ROWS,),
            )
            c.commit()
    except Exception:
        pass


def recent(n: int = 20, source: str | None = None) -> list[dict]:
    try:
        with _conn() as c:
            if source:
                rows = c.execute(
                    "SELECT ts, source, kind, message, detail, context FROM errors "
                    "WHERE source=? ORDER BY ts DESC LIMIT ?", (source, n),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT ts, source, kind, message, detail, context FROM errors "
                    "ORDER BY ts DESC LIMIT ?", (n,),
                ).fetchall()
    except Exception:
        return []
    out = []
    for ts, src, kind, msg, detail, ctx in rows:
        try:
            context = json.loads(ctx) if ctx else {}
        except json.JSONDecodeError:
            context = {}
        out.append({"ts": ts, "source": src, "kind": kind, "message": msg,
                    "detail": detail or "", "context": context})
    return out


def clear() -> int:
    try:
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
            c.execute("DELETE FROM errors")
            c.commit()
            return n
    except Exception:
        return 0
