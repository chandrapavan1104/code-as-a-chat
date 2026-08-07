"""Night Shift work queue — jobs the overnight runner builds across the three
coding subscriptions.

You queue tasks from the phone during the day; `server/night_shift.py` claims and
builds them overnight on isolated branches. This store is just the durable job
list + an atomic `claim_next` so three parallel engine workers never grab the same
job. It mirrors the shape/helpers of `cli_runs_store` (SQLite at ~/.codeasachat/).

Statuses:
  queued     waiting to be built
  running    a night worker is on it
  deployed   app-only change built + APK deployed (branch holds the code)
  staged     work committed on its branch, waiting for `/queue ship`
  needs_you  agent stopped on a design/product decision (no change made)
  failed     the run errored / timed out
  shipped    merged to base (terminal)
  held       parked by you (`mine` tag) — never auto-run
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "night_queue.db"

# Terminal-ish statuses that a night worker will not re-run.
RUNNABLE = ("queued",)


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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                project         TEXT NOT NULL,
                task            TEXT NOT NULL,
                tag             TEXT NOT NULL DEFAULT 'auto',
                engine          TEXT NOT NULL DEFAULT 'auto',
                priority        INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'queued',
                origin          TEXT NOT NULL DEFAULT 'queue',
                branch          TEXT,
                base            TEXT,
                summary         TEXT,
                files_changed   TEXT,
                engine_used     TEXT,
                tokens_total    INTEGER NOT NULL DEFAULT 0,
                tokens_billable INTEGER NOT NULL DEFAULT 0,
                created_at      REAL NOT NULL,
                started_at      REAL,
                ended_at        REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, priority DESC, id)"
        )
        conn.commit()


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    try:
        d["files_changed"] = json.loads(d["files_changed"]) if d.get("files_changed") else []
    except (TypeError, json.JSONDecodeError):
        d["files_changed"] = []
    return d


def add(*, project: str, task: str, tag: str = "auto", engine: str = "auto",
        priority: int = 0, origin: str = "queue") -> int:
    init()
    status = "held" if tag == "mine" else "queued"
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (project, task, tag, engine, priority, status, "
            "origin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project, task, tag, engine, priority, status, origin, time.time()),
        )
        conn.commit()
        return int(cur.lastrowid)


def get(job_id: int) -> dict | None:
    init()
    with _conn() as conn:
        return _row(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def list_jobs(status: str | None = None, limit: int = 100) -> list[dict]:
    """Jobs, newest first. `status` may be a single value or a comma list."""
    init()
    with _conn() as conn:
        if status:
            wanted = [s.strip() for s in status.split(",") if s.strip()]
            qs = ",".join("?" * len(wanted))
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({qs}) "
                "ORDER BY id DESC LIMIT ?", (*wanted, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]


def list_since(started_at: float) -> list[dict]:
    """Jobs a night worker has touched since `started_at` (this night's batch)."""
    init()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE started_at IS NOT NULL AND started_at >= ? "
            "ORDER BY id ASC", (started_at,),
        ).fetchall()
        return [_row(r) for r in rows]


def claim_next(engine: str) -> dict | None:
    """Atomically take the next runnable `auto` job for this engine and mark it
    running. `BEGIN IMMEDIATE` serializes concurrent workers at the DB level, so
    two engines never claim the same row. A job pinned to a specific engine is
    only claimed by that engine; `engine='auto'` jobs go to whoever asks first.
    """
    init()
    with _conn() as conn:
        conn.isolation_level = None  # manual transaction control
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' AND tag = 'auto' "
                "AND (engine = ? OR engine = 'auto') "
                "ORDER BY priority DESC, id ASC LIMIT 1", (engine,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            now = time.time()
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, engine_used = ? "
                "WHERE id = ?", (now, engine, row["id"]),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        claimed = _row(row)
        claimed["status"] = "running"
        claimed["started_at"] = now
        claimed["engine_used"] = engine
        return claimed


_UPDATABLE = {
    "task", "status", "branch", "base", "summary", "files_changed", "engine_used",
    "tokens_total", "tokens_billable", "started_at", "ended_at", "priority",
    "tag", "engine",
}


def update(job_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return
    if "files_changed" in fields and not isinstance(fields["files_changed"], str):
        fields["files_changed"] = json.dumps(fields["files_changed"])
    init()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?",
                     (*fields.values(), job_id))
        conn.commit()


def drop(job_id: int) -> bool:
    init()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
