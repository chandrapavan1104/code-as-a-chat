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
        # Links an assistant turn to its agent_runs trace, so the app can show
        # what the agent actually did behind any reply. Added after the table
        # existed, hence the guarded ALTER rather than a schema bump.
        cols = {r[1] for r in c.execute("PRAGMA table_info(conversations)")}
        if "run_id" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN run_id TEXT")
        c.commit()


_init()


def append_turn(session_id: str, role: str, content: str,
                run_id: str | None = None) -> None:
    if not session_id or not role or not content:
        return
    with _conn() as c:
        c.execute(
            "INSERT INTO conversations (session_id, role, content, ts, run_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, time.time(), run_id),
        )
        c.commit()


def get_recent(session_id: str, n: int = 5) -> list[dict]:
    """Return up to `n` *turn pairs* (so up to 2n rows), chronological order."""
    if not session_id or n <= 0:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content, ts, run_id FROM conversations "
            "WHERE session_id = ? "
            "ORDER BY ts DESC LIMIT ?",
            (session_id, n * 2),
        ).fetchall()
    return [
        {"role": r[0], "content": r[1], "ts": r[2], "run_id": r[3]}
        for r in reversed(rows)
    ]


def client_scope(session_id: str) -> str:
    """Return the client namespace shared by a user's conversations.

    Session IDs are deliberately client-prefixed (``app:<install>`` and
    ``tg:<chat>``).  The prefix is the safe boundary for an explicit
    cross-project search; callers never receive another client's memory.
    """
    return (session_id or "").split(":", 1)[0]


def search(session_id: str, query: str, *, limit: int = 20, offset: int = 0,
           all_client: bool = False) -> list[dict]:
    """Find exact matching messages in this session or its client namespace."""
    if not session_id or not query or limit <= 0 or offset < 0:
        return []
    limit = min(limit, 100)
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    with _conn() as c:
        if all_client and client_scope(session_id):
            rows = c.execute(
                "SELECT id, session_id, role, content, ts, run_id "
                "FROM conversations WHERE session_id LIKE ? AND content LIKE ? ESCAPE '\\' "
                "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (client_scope(session_id) + ":%", pattern, limit, offset),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, session_id, role, content, ts, run_id "
                "FROM conversations WHERE session_id = ? AND content LIKE ? ESCAPE '\\' "
                "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                (session_id, pattern, limit, offset),
            ).fetchall()
    return [
        {"id": r[0], "session_id": r[1], "role": r[2], "content": r[3],
         "ts": r[4], "run_id": r[5]}
        for r in rows
    ]


def get_message(message_id: int, session_id: str, *, all_client: bool = False) -> dict | None:
    """Return one full message, enforcing session/client ownership."""
    if not session_id or message_id <= 0:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT id, session_id, role, content, ts, run_id "
            "FROM conversations WHERE id = ?", (message_id,)
        ).fetchone()
    if not row:
        return None
    allowed = row[1] == session_id or (
        all_client and client_scope(session_id) and
        client_scope(row[1]) == client_scope(session_id)
    )
    if not allowed:
        return None
    return {"id": row[0], "session_id": row[1], "role": row[2],
            "content": row[3], "ts": row[4], "run_id": row[5]}


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
