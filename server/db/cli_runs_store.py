"""Append-only audit of coding-CLI turns started by Code-as-a-chat.

The native Claude/Codex/Gemini stores tell us how much a project used, but not
whether a particular turn came from Gajala or a terminal.  Recording that fact
at dispatch time gives monitoring tools an exact origin marker without writing
to, or trying to reinterpret, the vendors' own databases.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "cli_runs.db"


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
            CREATE TABLE IF NOT EXISTS cli_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace       TEXT NOT NULL,
                engine          TEXT NOT NULL,
                cli_session_id  TEXT,
                source          TEXT NOT NULL,
                started_at      REAL NOT NULL,
                ended_at        REAL NOT NULL,
                status          TEXT NOT NULL,
                total_tokens    INTEGER NOT NULL DEFAULT 0,
                billable_tokens INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cli_runs_workspace_time "
            "ON cli_runs (workspace, started_at DESC)"
        )
        conn.commit()


def add(*, workspace: str, engine: str, cli_session_id: str | None,
        source: str, started_at: float, status: str,
        total_tokens: int = 0, billable_tokens: int = 0) -> None:
    init()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO cli_runs "
            "(workspace, engine, cli_session_id, source, started_at, ended_at, "
            "status, total_tokens, billable_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (workspace, engine, cli_session_id, source, started_at, time.time(),
             status, max(0, int(total_tokens or 0)),
             max(0, int(billable_tokens or 0))),
        )
        conn.commit()
