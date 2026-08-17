"""Durable trace of what the shell agent actually did on each turn.

Until now a turn's steps existed only as a live progress bubble in the app,
thrown away the moment the reply landed — and lost entirely if the phone dropped
the stream. So when a turn went wrong there was nothing to look at: no record of
which tools ran, in which project, in what order, or why it stopped. The only
way to reconstruct the incident that prompted this file was to open
conversations.db by hand.

One row per turn in `runs`, one per tool call in `run_steps`. Small and
append-only; pruned to the most recent MAX_RUNS turns so it stays a debugging
aid rather than another database to manage.
"""

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "agent_runs.db"

# Roughly a month of heavy phone use. Old traces have no value once the
# behaviour they explain has been fixed.
MAX_RUNS = 500

# Why a turn ended. Anything other than `done` is worth surfacing on the phone.
STOP_REASONS = (
    "done",             # the agent finished and replied
    "passthrough",      # a different-persona skill replied directly
    "final_output",     # a presentation-ready skill result was returned as-is
    "step_limit",       # the productive-step budget ran out
    "llm_error",        # the routing model was unreachable
    "no_action",        # the model returned a decision with nothing to act on
    "duplicate_stop",   # an identical call had already timed out
    "error",            # the turn raised
)


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          TEXT PRIMARY KEY,
                session_id  TEXT,
                workspace   TEXT,
                prompt      TEXT,
                stop_reason TEXT,
                reply       TEXT,
                brains      TEXT,
                started_at  REAL NOT NULL,
                ended_at    REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS run_steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                idx         INTEGER NOT NULL,
                tool        TEXT NOT NULL,
                args        TEXT,
                result      TEXT,
                ok          INTEGER NOT NULL DEFAULT 1,
                charged     INTEGER NOT NULL DEFAULT 1,
                workspace   TEXT,
                duration_ms INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Which brain(s) routed the turn, e.g. "claude" or "qwen:rejected -> claude".
        # Added after the table existed, hence the guarded ALTER.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        if "brains" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN brains TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_session "
            "ON runs (session_id, started_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps (run_id, idx)"
        )
        conn.commit()
    _prune()


def _prune() -> None:
    """Keep the newest MAX_RUNS turns; drop their steps with them."""
    with _conn() as conn:
        stale = [r["id"] for r in conn.execute(
            "SELECT id FROM runs ORDER BY started_at DESC LIMIT -1 OFFSET ?",
            (MAX_RUNS,),
        )]
        if not stale:
            return
        marks = ",".join("?" * len(stale))
        conn.execute(f"DELETE FROM run_steps WHERE run_id IN ({marks})", stale)
        conn.execute(f"DELETE FROM runs WHERE id IN ({marks})", stale)
        conn.commit()


# ── writing ───────────────────────────────────────────────────────────────────

def start(*, session_id: str | None, workspace: str, prompt: str) -> str:
    """Open a run. Returns its id, which the assistant's chat turn also stores so
    the app can fetch the trace behind any reply."""
    run_id = uuid.uuid4().hex[:16]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, session_id, workspace, prompt, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, session_id, workspace, prompt[:4000], time.time()),
        )
        conn.commit()
    return run_id


def add_step(run_id: str, *, idx: int, tool: str, args: str, result: str,
             ok: bool = True, charged: bool = True, workspace: str = "",
             duration_ms: int = 0) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO run_steps "
            "(run_id, idx, tool, args, result, ok, charged, workspace, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, idx, tool, args[:2000], result[:4000],
             int(ok), int(charged), workspace, duration_ms),
        )
        conn.commit()


def finish(run_id: str, *, stop_reason: str, reply: str = "",
           workspace: str = "", brains: str = "") -> None:
    with _conn() as conn:
        if workspace:
            conn.execute(
                "UPDATE runs SET stop_reason = ?, reply = ?, ended_at = ?, "
                "workspace = ?, brains = ? WHERE id = ?",
                (stop_reason, reply[:4000], time.time(), workspace, brains, run_id),
            )
        else:
            conn.execute(
                "UPDATE runs SET stop_reason = ?, reply = ?, ended_at = ?, "
                "brains = ? WHERE id = ?",
                (stop_reason, reply[:4000], time.time(), brains, run_id),
            )
        conn.commit()


# ── reading ───────────────────────────────────────────────────────────────────

def get(run_id: str) -> dict | None:
    """A run with its steps — what the app renders under a reply."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        steps = conn.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY idx", (run_id,)
        ).fetchall()
    run = dict(row)
    run["steps"] = [dict(s) for s in steps]
    run["charged_steps"] = sum(1 for s in run["steps"] if s["charged"])
    run["duration_ms"] = int(
        ((run["ended_at"] or run["started_at"]) - run["started_at"]) * 1000)
    return run


def list_runs(session_id: str | None = None, limit: int = 25) -> list[dict]:
    """Recent runs, newest first, without their steps."""
    with _conn() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM runs WHERE session_id = ? "
                "ORDER BY started_at DESC LIMIT ?", (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,),
            ).fetchall()
        out = []
        for r in rows:
            run = dict(r)
            run["step_count"] = conn.execute(
                "SELECT COUNT(*) FROM run_steps WHERE run_id = ?", (run["id"],)
            ).fetchone()[0]
            run["duration_ms"] = int(
                ((run["ended_at"] or run["started_at"]) - run["started_at"]) * 1000)
            out.append(run)
    return out
