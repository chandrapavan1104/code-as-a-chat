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
  staged     committed branch awaiting an automatic retry or manual `/queue ship`
  deploying  merge/restart/health verification is in progress
  completed  non-code/research result is ready in the job summary
  needs_you  agent stopped on a design/product decision (no change made)
  failed     the run errored / timed out
  shipped    merged to base (terminal)
  held       parked by you (`mine` tag) — never auto-run
  closed     recoverably archived with a reason; never auto-run
"""

from __future__ import annotations

import json
import hashlib
import re
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
            "source_note_id": "INTEGER",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "attempted_engines": "TEXT NOT NULL DEFAULT '[]'",
            "failure_kind": "TEXT",
            "blocker_reason": "TEXT",
            "next_action": "TEXT",
            "next_retry_at": "REAL",
            "last_supervised_at": "REAL",
            "awareness_json": "TEXT",
            "awareness_checked_at": "REAL",
            "session_id": "TEXT",
            "origin_message_id": "INTEGER",
            "request_id": "TEXT",
            "result_text": "TEXT",
            "result_kind": "TEXT",
            "result_completeness": "TEXT",
            "result_artifacts": "TEXT NOT NULL DEFAULT '[]'",
            "result_saved_at": "REAL",
            "result_delivered_at": "REAL",
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
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_session_request "
            "ON jobs(session_id, request_id) WHERE session_id IS NOT NULL "
            "AND request_id IS NOT NULL"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                attempt_no INTEGER NOT NULL,
                engine TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at REAL NOT NULL,
                ended_at REAL,
                error TEXT,
                exit_code INTEGER,
                stdout TEXT,
                stderr TEXT,
                output TEXT,
                stdout_truncated INTEGER NOT NULL DEFAULT 0,
                stderr_truncated INTEGER NOT NULL DEFAULT 0,
                output_truncated INTEGER NOT NULL DEFAULT 0,
                logs_expired INTEGER NOT NULL DEFAULT 0,
                UNIQUE(job_id, attempt_no)
            )
        """)
        attempt_columns = {row[1] for row in conn.execute("PRAGMA table_info(job_attempts)")}
        if "logs_expired" not in attempt_columns:
            conn.execute("ALTER TABLE job_attempts ADD COLUMN logs_expired INTEGER NOT NULL DEFAULT 0")
        # Retain attempt metadata indefinitely, but keep raw diagnostics only for
        # the newest twenty attempts per job and for at most thirty days.
        conn.execute("""
            UPDATE job_attempts SET stdout=NULL, stderr=NULL, output=NULL, logs_expired=1
            WHERE logs_expired=0 AND (
                started_at < ? OR id NOT IN (
                    SELECT id FROM job_attempts AS recent
                    WHERE recent.job_id=job_attempts.job_id
                    ORDER BY recent.attempt_no DESC LIMIT 20
                )
            )
        """, (time.time() - 30 * 24 * 60 * 60,))
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
                           ("closure_history", []), ("attempted_engines", []),
                           ("awareness_json", {}), ("result_artifacts", [])):
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
        depends_on: list[int] | None = None, source_note_id: int | None = None,
        session_id: str | None = None, origin_message_id: int | None = None,
        request_id: str | None = None) -> int:
    init()
    from server.work_orders import migrate_spec
    if spec and not spec.get("title"):
        # Callers may attach metadata (for example validated chat files) at
        # capture time before a complete work order exists.
        parsed = migrate_spec(None, task)
        parsed.attachment_refs = list(spec.get("attachment_refs") or [])
    else:
        parsed = migrate_spec(spec, task)
    # Rough captures are inbox items, never executable instructions. Refinement
    # returns them held as well so the owner reviews before enabling automation.
    if parsed.readiness == "draft":
        tag = "mine"
    status = "held" if tag == "mine" else "queued"
    with _conn() as conn:
        if session_id and request_id:
            conn.execute("BEGIN IMMEDIATE")
        if session_id and request_id:
            existing = conn.execute(
                "SELECT * FROM jobs WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            ).fetchone()
            if existing:
                old = _row(existing)
                expected = (project, task, tag, engine, priority, origin,
                            json.dumps(parsed.model_dump()), json.dumps(depends_on or []),
                            source_note_id, origin_message_id)
                actual = (old["project"], old["task"], old["tag"], old["engine"],
                          old["priority"], old["origin"], json.dumps(old["spec_json"]),
                          json.dumps(old["depends_on"]), old["source_note_id"],
                          old["origin_message_id"])
                if expected != actual:
                    conn.rollback()
                    raise ValueError("request_id was already used for a different queue task")
                conn.commit()
                return int(old["id"])
        cur = conn.execute(
            "INSERT INTO jobs (project, task, tag, engine, priority, status, "
            "origin, spec_json, depends_on, source_note_id, session_id, "
            "origin_message_id, request_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project, task, tag, engine, priority, status, origin,
             json.dumps(parsed.model_dump()), json.dumps(depends_on or []),
             source_note_id, session_id, origin_message_id, request_id, time.time()),
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
                "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
                "AND (engine = ? OR engine = 'auto') "
                "ORDER BY priority DESC, id ASC", (time.time(), engine),
            ).fetchall()
            row = next((candidate for candidate in rows
                        if _is_refined(candidate)
                        and not _blocked_dependencies(conn, candidate)), None)
            if row is None:
                conn.execute("COMMIT")
                return None
            now = time.time()
            try:
                attempted = json.loads(row["attempted_engines"] or "[]")
            except (TypeError, json.JSONDecodeError):
                attempted = []
            attempted.append(engine)
            conn.execute(
                "UPDATE jobs SET status='running', started_at=?, engine_used=?, "
                "attempt_count=COALESCE(attempt_count,0)+1, attempted_engines=?, "
                "failure_kind=NULL, blocker_reason=NULL, ended_at=NULL, "
                "next_action='Worker is implementing and testing this task.', "
                "next_retry_at=NULL WHERE id=?",
                (now, engine, json.dumps(attempted), row["id"]),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        claimed = _row(row)
        claimed["status"] = "running"
        claimed["started_at"] = now
        claimed["engine_used"] = engine
        claimed["attempt_count"] = (claimed.get("attempt_count") or 0) + 1
        claimed["attempted_engines"] = attempted
        return claimed


def fail_orphaned_running(active_job_ids: set[int] | None = None,
                          grace_seconds: int = 60) -> list[dict]:
    """Fail durable `running` claims that have no worker in this server process.

    CLI timeouts cover a healthy worker, but an app/server restart used to leave
    the SQLite claim behind forever. A short grace avoids racing a freshly
    scheduled run-now task before it enters the in-memory registry.
    """
    active = active_job_ids or set()
    now = time.time()
    cutoff = now - max(0, grace_seconds)
    init()
    recovered: list[dict] = []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='running' AND "
            "(started_at IS NULL OR started_at < ?)", (cutoff,),
        ).fetchall()
        for row in rows:
            if row["id"] in active:
                continue
            reason = ("Worker disappeared before reporting a result (the server "
                      "restarted or the worker crashed). The job was released "
                      "instead of remaining stuck in Building.")
            conn.execute(
                "UPDATE jobs SET status='failed', ended_at=?, summary=?, "
                "failure_kind='worker_lost', blocker_reason=?, next_retry_at=NULL, "
                "next_action='The worker disappeared. The supervisor will assess a safe retry.' "
                "WHERE id=? AND status='running'", (now, reason, reason, row["id"]),
            )
            value = _row(row)
            latest = conn.execute(
                "SELECT id FROM job_attempts WHERE job_id=? AND status='running' "
                "ORDER BY attempt_no DESC LIMIT 1", (row["id"],)
            ).fetchone()
            if latest:
                conn.execute(
                    "UPDATE job_attempts SET status='failed', stage='worker_lost', ended_at=?, error=? WHERE id=?",
                    (now, reason, latest["id"]),
                )
            value.update(status="failed", ended_at=now, summary=reason,
                         failure_kind="worker_lost", blocker_reason=reason)
            recovered.append(value)
        conn.commit()
    return recovered


def start_attempt(job_id: int, engine: str) -> None:
    """Record an on-demand attempt with the same accounting as a night claim."""
    job = get(job_id)
    attempted = list(job.get("attempted_engines") or []) if job else []
    attempted.append(engine)
    update(
        job_id, status="running", started_at=time.time(), engine_used=engine,
        attempt_count=(job.get("attempt_count") or 0) + 1,
        attempted_engines=attempted, failure_kind=None, blocker_reason=None,
        ended_at=None, next_action="Worker is implementing and testing this task.",
        next_retry_at=None,
    )


_UPDATABLE = {
    "project", "task", "status", "branch", "base", "summary", "files_changed", "engine_used",
    "tokens_total", "tokens_billable", "started_at", "ended_at", "priority",
    "tag", "engine", "spec_json", "depends_on", "closed_at", "close_reason",
    "previous_status", "closure_history", "source_note_id",
    "attempt_count", "max_attempts", "attempted_engines", "failure_kind",
    "blocker_reason", "next_action", "next_retry_at", "last_supervised_at",
    "awareness_json", "awareness_checked_at", "session_id", "origin_message_id",
    "request_id", "result_text", "result_kind", "result_completeness",
    "result_artifacts", "result_saved_at",
}


def update(job_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return
    if fields.get("status") in {"queued", "running"}:
        fields.setdefault("failure_kind", None)
        fields.setdefault("blocker_reason", None)
        fields.setdefault("next_retry_at", None)
        fields.setdefault("ended_at", None)
    if fields.get("status") in {"completed", "shipped", "deployed", "closed"}:
        fields.setdefault("failure_kind", None)
        fields.setdefault("blocker_reason", None)
        fields.setdefault("next_retry_at", None)
    if "files_changed" in fields and not isinstance(fields["files_changed"], str):
        fields["files_changed"] = json.dumps(fields["files_changed"])
    for name in ("spec_json", "depends_on", "closure_history", "attempted_engines",
                 "awareness_json", "result_artifacts"):
        if name in fields and not isinstance(fields[name], str):
            fields[name] = json.dumps(fields[name])
    init()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?",
                     (*fields.values(), job_id))
        conn.commit()



# Bound logs retained for debugging; final result text is stored separately and
# may be much larger because research reports are deliverables.
_ATTEMPT_LOG_LIMIT = 16_000
_ATTEMPT_OUTPUT_LIMIT = 32_000
_RESULT_LIMIT = 500_000
_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_AUTH_HEADER = re.compile(r"(?im)(authorization\s*:\s*(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_SECRET = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|credential|private[_-]?key)[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
_KNOWN_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,})\b")


def _safe_text(value: str | None, limit: int) -> tuple[str, bool]:
    text = _ANSI.sub("", str(value or ""))
    text = _PRIVATE_KEY.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _AUTH_HEADER.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET.sub(r"\1[REDACTED]", text)
    text = _KNOWN_KEY.sub("[REDACTED_KEY]", text)
    return text[:limit], len(text) > limit


def begin_attempt(job_id: int, engine: str, *, stage: str = "preflight") -> dict:
    """Create the durable attempt timeline row before any worker side effects."""
    init()
    now = time.time()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT attempt_count, attempted_engines FROM jobs WHERE id=?",
                           (job_id,)).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise KeyError(f"unknown job {job_id}")
        prior = conn.execute("SELECT COALESCE(MAX(attempt_no),0) FROM job_attempts WHERE job_id=?",
                             (job_id,)).fetchone()[0]
        attempt_no = max(int(prior or 0) + 1, int(row["attempt_count"] or 0))
        try:
            attempted = json.loads(row["attempted_engines"] or "[]")
        except (TypeError, json.JSONDecodeError):
            attempted = []
        # claim_next/start_attempt normally records this before the worker starts.
        if int(row["attempt_count"] or 0) < attempt_no:
            attempted.append(engine)
        cur = conn.execute(
            "INSERT INTO job_attempts(job_id,attempt_no,engine,stage,status,started_at) "
            "VALUES(?,?,?,?,?,?)", (job_id, attempt_no, engine, stage, "running", now),
        )
        conn.execute(
            "UPDATE jobs SET attempt_count=MAX(COALESCE(attempt_count,0),?), "
            "attempted_engines=?, status='running', started_at=?, ended_at=NULL, "
            "engine_used=?, failure_kind=NULL, blocker_reason=NULL, next_retry_at=NULL, "
            "next_action=? WHERE id=?",
            (attempt_no, json.dumps(attempted), now, engine,
             f"Attempt {attempt_no} started; preparing the project.", job_id),
        )
        conn.commit()
        return {"id": cur.lastrowid, "job_id": job_id, "attempt_no": attempt_no,
                "engine": engine, "stage": stage, "status": "running", "started_at": now}


def update_attempt(attempt_id: int, *, stage: str | None = None,
                   stdout: str | None = None, stderr: str | None = None,
                   output: str | None = None, error: str | None = None,
                   exit_code: int | None = None, status: str | None = None,
                   ended_at: float | None = None) -> None:
    values = {}
    if stage is not None: values["stage"] = stage
    if error is not None: values["error"] = _safe_text(error, 2000)[0]
    if exit_code is not None: values["exit_code"] = int(exit_code)
    if status is not None: values["status"] = status
    if ended_at is not None: values["ended_at"] = ended_at
    for name, value, limit in (("stdout", stdout, _ATTEMPT_LOG_LIMIT),
                               ("stderr", stderr, _ATTEMPT_LOG_LIMIT),
                               ("output", output, _ATTEMPT_OUTPUT_LIMIT)):
        if value is not None:
            text, truncated = _safe_text(value, limit)
            values[name] = text
            values[name + "_truncated"] = int(truncated)
    if not values: return
    init()
    with _conn() as conn:
        conn.execute("UPDATE job_attempts SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?",
                     (*values.values(), attempt_id))
        conn.commit()


def finish_attempt(attempt_id: int, *, status: str, stage: str,
                   error: str | None = None, exit_code: int | None = None,
                   stdout: str | None = None, stderr: str | None = None,
                   output: str | None = None) -> None:
    update_attempt(attempt_id, status=status, stage=stage, error=error,
                   exit_code=exit_code, stdout=stdout, stderr=stderr,
                   output=output, ended_at=time.time())


def attempts(job_id: int, *, include_logs: bool = False) -> list[dict]:
    init()
    columns = ("*" if include_logs else
               "id,job_id,attempt_no,engine,stage,status,started_at,ended_at,error,exit_code,")
    if not include_logs:
        columns += "stdout_truncated,stderr_truncated,output_truncated"
    with _conn() as conn:
        rows = conn.execute(f"SELECT {columns} FROM job_attempts WHERE job_id=? ORDER BY attempt_no",
                            (job_id,)).fetchall()
    return [dict(row) for row in rows]


def save_result(job_id: int, *, kind: str, text: str,
                completeness: str = "complete",
                artifacts: list[dict] | None = None) -> dict:
    safe, truncated = _safe_text(text, _RESULT_LIMIT)
    if truncated and completeness == "complete":
        completeness = "partial"
    init()
    staged_artifacts = list(artifacts or [])
    if safe and kind in {"research", "coding"}:
        try:
            from server.media import ensure_uploads_dir
            import os
            target_dir = ensure_uploads_dir() / "queue" / str(job_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()[:16]
            target = target_dir / f"result-{digest}.md"
            temporary = target.with_suffix(".md.tmp")
            temporary.write_text(safe, encoding="utf-8")
            os.replace(temporary, target)
            staged_artifacts = [a for a in staged_artifacts
                                if not (a.get("kind") == "file" and a.get("name") == "result.md")]
            staged_artifacts.append({"kind": "file", "path": str(target),
                                     "name": "result.md", "size": target.stat().st_size})
        except OSError:
            pass
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET result_text=?, result_kind=?, result_completeness=?, "
            "result_artifacts=?, result_saved_at=? WHERE id=?",
            (safe, kind, completeness, json.dumps(staged_artifacts), now, job_id),
        )
        conn.commit()
    return {"available": bool(safe), "kind": kind if safe else "unknown",
            "completeness": completeness if safe else "none",
            "artifact_count": len(staged_artifacts), "saved_at": now}


def result(job_id: int) -> dict | None:
    """Return the full durable result, with legacy summary fallback."""
    job = get(job_id)
    if not job:
        return None
    text = job.get("result_text") or job.get("summary") or ""
    kind = job.get("result_kind")
    if not kind:
        work_type = (job.get("spec_json") or {}).get("work_type")
        kind = ("failure" if job.get("status") == "failed" else
                "question" if job.get("status") in {"awaiting_input", "needs_you", "blocked"} else
                "research" if work_type == "research" else "coding" if text else "unknown")
    if job.get("result_completeness"):
        completeness = job["result_completeness"]
    elif not text:
        completeness = "none"
    elif job.get("status") in {"completed", "shipped", "deployed"}:
        completeness = "legacy"
    else:
        completeness = "partial"
    return {"job_id": job_id, "available": bool(text), "kind": kind,
            "completeness": completeness, "text": text,
            "artifacts": job.get("result_artifacts") or []}


def result_metadata(job: dict) -> dict:
    text = job.get("result_text") or job.get("summary") or ""
    completeness = job.get("result_completeness")
    if not completeness:
        completeness = ("legacy" if text and job.get("status") in
                        {"completed", "shipped", "deployed"} else
                        "partial" if text else "none")
    return {"available": bool(text),
            "kind": job.get("result_kind") or ("failure" if job.get("status") == "failed" else "unknown"),
            "completeness": completeness,
            "artifact_count": len(job.get("result_artifacts") or [])}


def result_receipt_candidates(limit: int = 100) -> list[dict]:
    """Completed, linked jobs available for the separate idempotent chat outbox."""
    init()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE session_id IS NOT NULL AND origin_message_id IS NOT NULL "
            "AND result_text IS NOT NULL AND result_saved_at IS NOT NULL "
            "AND (result_delivered_at IS NULL OR result_delivered_at < result_saved_at) "
            "AND status IN ('completed','failed','blocked','unverified','needs_you',"
            "'shipped','deployed','staged','awaiting_input','closed') "
            "ORDER BY result_saved_at ASC, id ASC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
    return [_row(row) for row in rows]


def mark_result_delivered(job_id: int, saved_at: float) -> None:
    """Acknowledge one exact result version after its outbox receipt commits."""
    init()
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET result_delivered_at=MAX(COALESCE(result_delivered_at,0),?) "
            "WHERE id=? AND result_saved_at=?",
            (saved_at, job_id, saved_at),
        )
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
