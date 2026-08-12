"""Durable, evidence-backed inventory of what Gajala can already do."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "capabilities.db"


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
            CREATE TABLE IF NOT EXISTS capabilities (
                key TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                source TEXT NOT NULL, evidence TEXT NOT NULL, status TEXT NOT NULL,
                ref_id INTEGER, verified_commit TEXT, project TEXT,
                updated_at REAL NOT NULL
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(capabilities)")}
        if "project" not in columns:
            conn.execute("ALTER TABLE capabilities ADD COLUMN project TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cap_source ON capabilities(source,status)")
        conn.commit()


def replace_source(source: str, entries: list[dict]) -> None:
    """Atomically replace one discoverer's view without disturbing other sources."""
    init()
    now = time.time()
    with _conn() as conn:
        conn.execute("DELETE FROM capabilities WHERE source=?", (source,))
        conn.executemany(
            "INSERT OR REPLACE INTO capabilities "
            "(key,title,description,source,evidence,status,ref_id,verified_commit,project,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(entry["key"], entry["title"], entry.get("description") or "", source,
              json.dumps(entry.get("evidence") or []), entry.get("status") or "live",
              entry.get("ref_id"), entry.get("verified_commit"), entry.get("project"), now)
             for entry in entries],
        )
        conn.commit()


def list_all(status: str | None = "live", project: str | None = None) -> list[dict]:
    init()
    query, args = "SELECT * FROM capabilities", []
    if status:
        query += " WHERE status=?"
        args.append(status)
    if project:
        query += " AND" if status else " WHERE"
        query += " (project IS NULL OR project=?)"
        args.append(project)
    query += " ORDER BY source,key"
    with _conn() as conn:
        rows = conn.execute(query, args).fetchall()
    values = []
    for row in rows:
        value = dict(row)
        try:
            value["evidence"] = json.loads(value["evidence"] or "[]")
        except json.JSONDecodeError:
            value["evidence"] = []
        values.append(value)
    return values


def count() -> int:
    init()
    with _conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM capabilities WHERE status='live'").fetchone()[0])
