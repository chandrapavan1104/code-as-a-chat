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
  completed  non-code/research result is ready in the job summary
  needs_you  agent stopped on a design/product decision (no change made)
  failed     the run errored / timed out
  shipped    merged to base (terminal)
  held       parked by you (`mine` tag) — never auto-run
  closed     recoverably archived with a reason; never auto-run
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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        additions = {
            "spec_json": "TEXT",
            "depends_on": "TEXT",
            "closed_at": "REAL",
            "close_reason": "TEXT",
            "previous_status": "TEXT",
            "closure_history": "TEXT",
        }
        for name, kind in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")
        # Materialize structured work orders for every legacy row once. The
        # original task remains untouched as the compatibility/audit copy.
        from server.work_orders import migrate_spec
        rows = conn.execute(
            "SELECT id, task, spec_json, status, tag FROM jobs"
        ).fetchall()
        for row in rows:
            try:
                stored = json.loads(row["spec_json"]) if row["spec_json"] else None
            except (TypeError, json.JSONDecodeError):
                stored = None
            spec = migrate_spec(stored, row["task"])
            if (stored == spec.model_dump()
                    and not (spec.readiness == "draft" and row["status"] == "queued")):
                continue
            if spec.readiness == "draft":
                conn.execute(
                    "UPDATE jobs SET spec_json = ?, "
                    "depends_on = COALESCE(depends_on, '[]'), "
                    "closure_history = COALESCE(closure_history, '[]'), "
                    "tag = CASE WHEN status = 'queued' THEN 'mine' ELSE tag END, "
                    "status = CASE WHEN status = 'queued' THEN 'held' ELSE status END "
                    "WHERE id = ?",
                    (json.dumps(spec.model_dump()), row["id"]),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET spec_json = ?, "
                    "depends_on = COALESCE(depends_on, '[]'), "
                    "closure_history = COALESCE(closure_history, '[]') WHERE id = ?",
                    (json.dumps(spec.model_dump()), row["id"]),
                )
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
    for name, fallback in (("spec_json", None), ("depends_on", []),
                           ("closure_history", [])):
        try:
            d[name] = json.loads(d[name]) if d.get(name) else fallback
        except (TypeError, json.JSONDecodeError):
            d[name] = fallback
    if not d.get("spec_json"):
        from server.work_orders import from_task
        d["spec_json"] = from_task(d.get("task") or "").model_dump()
    return d


def add(*, project: str, task: str, tag: str = "auto", engine: str = "auto",
        priority: int = 0, origin: str = "queue", spec: dict | None = None,
        depends_on: list[int] | None = None) -> int:
    init()
    from server.work_orders import migrate_spec
    parsed = migrate_spec(spec, task)
    # Rough captures are inbox items, never executable instructions. Refinement
    # returns them held as well so the owner reviews before enabling automation.
    if parsed.readiness == "draft":
        tag = "mine"
    status = "held" if tag == "mine" else "queued"
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (project, task, tag, engine, priority, status, "
            "origin, spec_json, depends_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project, task, tag, engine, priority, status, origin,
             json.dumps(parsed.model_dump()), json.dumps(depends_on or []), time.time()),
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
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' AND tag = 'auto' "
                "AND (engine = ? OR engine = 'auto') "
                "ORDER BY priority DESC, id ASC", (engine,),
            ).fetchall()
            row = next((candidate for candidate in rows
                        if _is_refined(candidate)
                        and not _blocked_dependencies(conn, candidate)), None)
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
    "project", "task", "status", "branch", "base", "summary", "files_changed", "engine_used",
    "tokens_total", "tokens_billable", "started_at", "ended_at", "priority",
    "tag", "engine", "spec_json", "depends_on", "closed_at", "close_reason",
    "previous_status", "closure_history",
}


def update(job_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return
    if "files_changed" in fields and not isinstance(fields["files_changed"], str):
        fields["files_changed"] = json.dumps(fields["files_changed"])
    for name in ("spec_json", "depends_on", "closure_history"):
        if name in fields and not isinstance(fields[name], str):
            fields[name] = json.dumps(fields[name])
    init()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?",
                     (*fields.values(), job_id))
        conn.commit()


def dependency_status(job: dict) -> list[dict]:
    """Dependency rows with enough state for API/UI explanations."""
    result = []
    for dep_id in job.get("depends_on") or []:
        dep = get(int(dep_id))
        result.append({
            "id": int(dep_id),
            "status": dep.get("status", "missing") if dep else "missing",
            "title": ((dep or {}).get("spec_json") or {}).get("title"),
            "satisfied": _dependency_satisfied(dep),
        })
    return result


def _blocked_dependencies(conn: sqlite3.Connection, row: sqlite3.Row) -> list[int]:
    try:
        deps = json.loads(row["depends_on"] or "[]")
    except (TypeError, json.JSONDecodeError):
        deps = []
    blocked = []
    for dep_id in deps:
        dep = conn.execute("SELECT * FROM jobs WHERE id = ?", (dep_id,)).fetchone()
        if not _dependency_satisfied(_row(dep) if dep else None):
            blocked.append(int(dep_id))
    return blocked


def _dependency_satisfied(job: dict | None) -> bool:
    """Terminal work stays satisfied after recoverable close/reopen cycles.

    Closing is archival, not an undo of code already shipped or a report already
    completed. Closure history is durable even though reopen safely returns the
    row to Held, so consult the full history rather than only current status.
    """
    if not job:
        return False
    if job.get("status") in ("shipped", "completed"):
        return True
    return any(
        event.get("previous_status") in ("shipped", "completed")
        for event in (job.get("closure_history") or [])
    )


def _is_refined(row: sqlite3.Row) -> bool:
    try:
        spec = json.loads(row["spec_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    return spec.get("readiness") == "refined"


def blocked_by(job: dict) -> list[int]:
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job["id"],)).fetchone()
        return _blocked_dependencies(conn, row) if row else []


def is_refined(job: dict) -> bool:
    return (job.get("spec_json") or {}).get("readiness") == "refined"


def close(job_id: int, reason: str) -> bool:
    reason = reason.strip()
    if not reason:
        raise ValueError("close reason is required")
    init()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return False
        if row["status"] == "running":
            raise ValueError("stop a running job before closing it")
        if row["status"] == "closed":
            return True
        now = time.time()
        try:
            history = json.loads(row["closure_history"] or "[]")
        except (TypeError, json.JSONDecodeError):
            history = []
        history.append({"closed_at": now, "reason": reason,
                        "previous_status": row["status"]})
        conn.execute(
            "UPDATE jobs SET status='closed', tag='mine', closed_at=?, "
            "close_reason=?, previous_status=?, closure_history=? WHERE id=?",
            (now, reason, row["status"], json.dumps(history), job_id),
        )
        conn.commit()
        return True


def reopen(job_id: int) -> bool:
    """Recover a closed job safely into held state; never make it runnable."""
    init()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='held', tag='mine', closed_at=NULL, "
            "close_reason=NULL, previous_status=NULL WHERE id=? AND status='closed'",
            (job_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def purge(job_id: int) -> bool:
    """Maintenance-only hard deletion; never expose through app/agent APIs."""
    init()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
