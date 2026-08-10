"""
SQLite-backed personal notes / whiteboard store.

One row per note. Indexed on project, kind, status for fast filtering.
Stored at ~/.codeasachat/notes.db — same dir as conversations.db and state.json
so a single backup catches everything.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path.home() / ".codeasachat" / "notes.db"


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
            CREATE TABLE IF NOT EXISTS notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project         TEXT,
                kind            TEXT NOT NULL DEFAULT 'note',
                title           TEXT NOT NULL,
                body            TEXT NOT NULL,
                tags            TEXT,                       -- comma-separated
                status          TEXT NOT NULL DEFAULT 'open',  -- open | done | closed
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL,
                source_session  TEXT,
                closed_at       REAL,
                close_reason    TEXT,
                closure_history TEXT
            )
        """)
        columns = {row[1] for row in c.execute("PRAGMA table_info(notes)")}
        # Migrate legacy dropped status to closed, adding closure tracking.
        if "closed_at" not in columns:
            c.execute("ALTER TABLE notes ADD COLUMN closed_at REAL")
            c.execute("ALTER TABLE notes ADD COLUMN close_reason TEXT")
            c.execute("ALTER TABLE notes ADD COLUMN closure_history TEXT")
            # Convert old 'dropped' status to 'closed' with a generic reason
            dropped = c.execute("SELECT id, updated_at FROM notes WHERE status='dropped'").fetchall()
            for row in dropped:
                history = json.dumps([{
                    "closed_at": row["updated_at"],
                    "reason": "Recovered from legacy drop",
                    "previous_status": "open"
                }])
                c.execute(
                    "UPDATE notes SET status='closed', closed_at=?, close_reason=?, closure_history=? WHERE id=?",
                    (row["updated_at"], "Recovered from legacy drop", history, row["id"])
                )
            c.commit()
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_kind    ON notes(kind)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_status  ON notes(status)")
        c.commit()


_init()


# ── writes ────────────────────────────────────────────────────────────────────

def add(project: str | None, kind: str, title: str, body: str,
        tags: list[str] | None = None,
        source_session: str | None = None) -> int:
    now = time.time()
    tags_str = ",".join(t.strip() for t in (tags or []) if t.strip())
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO notes (project, kind, title, body, tags, status, "
            "created_at, updated_at, source_session) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (project, kind, title, body, tags_str, now, now, source_session),
        )
        c.commit()
        return cur.lastrowid


def set_status(note_id: int, status: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE notes SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), note_id),
        )
        c.commit()
        return cur.rowcount > 0


def update_body(note_id: int, body: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE notes SET body = ?, updated_at = ? WHERE id = ?",
            (body, time.time(), note_id),
        )
        c.commit()
        return cur.rowcount > 0


def close(note_id: int, reason: str) -> bool:
    """Close a note with an optional reason; preserve closure history."""
    reason = reason.strip()
    if not reason:
        raise ValueError("close reason is required")
    _init()
    with _conn() as c:
        row = c.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return False
        if row["status"] == "closed":
            return True
        now = time.time()
        try:
            history = json.loads(row["closure_history"] or "[]")
        except (TypeError, json.JSONDecodeError):
            history = []
        history.append({"closed_at": now, "reason": reason, "previous_status": row["status"]})
        c.execute(
            "UPDATE notes SET status='closed', closed_at=?, close_reason=?, "
            "closure_history=?, updated_at=? WHERE id=?",
            (now, reason, json.dumps(history), now, note_id),
        )
        c.commit()
        return True


def reopen(note_id: int) -> bool:
    """Recover a closed note back to open state."""
    _init()
    with _conn() as c:
        cur = c.execute(
            "UPDATE notes SET status='open', closed_at=NULL, close_reason=NULL, "
            "updated_at=? WHERE id=? AND status='closed'",
            (time.time(), note_id),
        )
        c.commit()
        return cur.rowcount > 0


def delete(note_id: int) -> bool:
    """Hard delete (maintenance only); never expose through app/agent APIs."""
    with _conn() as c:
        cur = c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        c.commit()
        return cur.rowcount > 0


# ── reads ─────────────────────────────────────────────────────────────────────

def get(note_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
    return dict(row) if row else None


def list_notes(project: str | None = None, kind: str | None = None,
               status: str | None = "open", limit: int = 20) -> list[dict]:
    where, args = [], []
    if project is not None:
        where.append("project = ?")
        args.append(project)
    if kind is not None:
        where.append("kind = ?")
        args.append(kind)
    if status is not None:
        where.append("status = ?")
        args.append(status)
    sql = "SELECT * FROM notes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def search(query: str, limit: int = 20) -> list[dict]:
    q = f"%{query}%"
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (q, q, q, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT project, kind, status, COUNT(*) AS c "
            "FROM notes GROUP BY project, kind, status "
            "ORDER BY project, kind"
        ).fetchall()
    return [dict(r) for r in rows]
