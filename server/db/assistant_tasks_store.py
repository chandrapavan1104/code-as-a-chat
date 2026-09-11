"""Durable work owned by the everyday Gajala conversation.

Chat rows remember words and agent_runs remember tool traces, but neither says
what outcome is still active after a disconnect, correction, or server restart.
This store gives each user request an idempotent request ID and an append-only
event trail. Night Shift remains the execution authority for queued jobs.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path.home() / ".codeasachat" / "assistant_tasks.db"
CONTINUABLE = ("accepted", "working", "recovering", "waiting_for_user", "failed")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS assistant_tasks (
                id              TEXT PRIMARY KEY,
                request_id      TEXT NOT NULL UNIQUE,
                session_id      TEXT NOT NULL,
                command         TEXT NOT NULL,
                original_prompt TEXT NOT NULL,
                latest_prompt   TEXT NOT NULL,
                revision        INTEGER NOT NULL DEFAULT 1,
                project         TEXT,
                status          TEXT NOT NULL,
                summary         TEXT NOT NULL DEFAULT '',
                result          TEXT NOT NULL DEFAULT '',
                blocker         TEXT NOT NULL DEFAULT '',
                next_action     TEXT NOT NULL DEFAULT '',
                run_id          TEXT,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assistant_tasks_session
                ON assistant_tasks(session_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS assistant_task_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    TEXT NOT NULL,
                kind       TEXT NOT NULL,
                payload    TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assistant_task_events
                ON assistant_task_events(task_id, id);
        """)
        conn.commit()


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def add_event(task_id: str, kind: str, payload: dict | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO assistant_task_events (task_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, kind, json.dumps(payload or {}), time.time()),
        )
        conn.commit()


def accept(*, request_id: str, session_id: str, command: str, prompt: str,
           project: str | None = None, continue_task_id: str | None = None) -> tuple[dict, bool]:
    """Create once per client request, or append a correction to active work."""
    init()
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT * FROM assistant_tasks WHERE request_id = ?", (request_id,)
        ).fetchone()
        if existing:
            return dict(existing), False

        target = None
        if continue_task_id:
            target = conn.execute(
                "SELECT * FROM assistant_tasks WHERE id = ? AND session_id = ?",
                (continue_task_id, session_id),
            ).fetchone()
        if target and target["status"] in CONTINUABLE:
            revision = int(target["revision"]) + 1
            conn.execute(
                "UPDATE assistant_tasks SET request_id=?, latest_prompt=?, revision=?, "
                "status='accepted', blocker='', next_action='', updated_at=? WHERE id=?",
                (request_id, prompt, revision, now, target["id"]),
            )
            task_id = target["id"]
            created = False
        else:
            task_id = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT INTO assistant_tasks "
                "(id, request_id, session_id, command, original_prompt, latest_prompt, "
                "project, status, summary, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?, ?)",
                (task_id, request_id, session_id, command, prompt, prompt, project,
                 _summary(prompt), now, now),
            )
            revision = 1
            created = True
        conn.execute(
            "INSERT INTO assistant_task_events (task_id, kind, payload, created_at) "
            "VALUES (?, 'accepted', ?, ?)",
            (task_id, json.dumps({"request_id": request_id, "prompt": prompt,
                                  "revision": revision}), now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM assistant_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return dict(row), created


def update(task_id: str, *, status: str | None = None, summary: str | None = None,
           result: str | None = None, blocker: str | None = None,
           next_action: str | None = None, run_id: str | None = None,
           project: str | None = None, event: str = "updated",
           payload: dict | None = None) -> dict | None:
    init()
    values = {
        "status": status, "summary": summary, "result": result,
        "blocker": blocker, "next_action": next_action, "run_id": run_id,
        "project": project,
    }
    fields = [f"{key}=?" for key, value in values.items() if value is not None]
    args = [value for value in values.values() if value is not None]
    fields.append("updated_at=?")
    args.extend([time.time(), task_id])
    with _conn() as conn:
        conn.execute(f"UPDATE assistant_tasks SET {', '.join(fields)} WHERE id=?", args)
        conn.execute(
            "INSERT INTO assistant_task_events (task_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?)",
            (task_id, event, json.dumps(payload or {}), time.time()),
        )
        conn.commit()
        return _row(conn.execute(
            "SELECT * FROM assistant_tasks WHERE id = ?", (task_id,)
        ).fetchone())


def get(task_id: str, *, include_events: bool = False) -> dict | None:
    init()
    with _conn() as conn:
        task = _row(conn.execute(
            "SELECT * FROM assistant_tasks WHERE id = ?", (task_id,)
        ).fetchone())
        if task is not None and include_events:
            rows = conn.execute(
                "SELECT id, kind, payload, created_at FROM assistant_task_events "
                "WHERE task_id = ? ORDER BY id", (task_id,),
            ).fetchall()
            task["events"] = [
                {"id": r[0], "kind": r[1], "payload": json.loads(r[2]),
                 "created_at": r[3]} for r in rows
            ]
        return task


def list_for_session(session_id: str, *, limit: int = 20) -> list[dict]:
    init()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM assistant_tasks WHERE session_id=? "
            "ORDER BY updated_at DESC LIMIT ?", (session_id, max(1, min(limit, 100))),
        ).fetchall()
    return [dict(row) for row in rows]


def recover_orphans() -> int:
    """A process restart cannot leave ordinary work pretending to be live."""
    init()
    now = time.time()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM assistant_tasks WHERE status='working'"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE assistant_tasks SET status='recovering', blocker=?, "
                "next_action=?, updated_at=? WHERE id=?",
                ("The server restarted while this work was running",
                 "Continue from the preserved request and verify prior effects",
                 now, row[0]),
            )
            conn.execute(
                "INSERT INTO assistant_task_events (task_id, kind, payload, created_at) "
                "VALUES (?, 'orphan_recovered', '{}', ?)", (row[0], now))
        conn.commit()
    return len(rows)


def _summary(prompt: str) -> str:
    flat = " ".join((prompt or "").split())
    return flat if len(flat) <= 120 else flat[:119].rstrip() + "…"
