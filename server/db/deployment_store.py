"""Durable ledger and concurrency gate for autonomous deployments."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "deployments.db"
ACTIVE = ("merging", "restarting", "verifying", "rolling_back")


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
            CREATE TABLE IF NOT EXISTS deployments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL, branch TEXT NOT NULL, base TEXT NOT NULL,
                before_sha TEXT, deployed_sha TEXT, state TEXT NOT NULL,
                source TEXT NOT NULL, ref_id INTEGER, server_touched INTEGER NOT NULL,
                changed_files TEXT, detail TEXT, created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deploy_state ON deployments(state, id)")
        conn.commit()


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    try:
        value["changed_files"] = json.loads(value.get("changed_files") or "[]")
    except json.JSONDecodeError:
        value["changed_files"] = []
    value["server_touched"] = bool(value["server_touched"])
    return value


def begin(*, repo: str, branch: str, base: str, source: str,
          ref_id: int | None, server_touched: bool,
          changed_files: list[str]) -> tuple[dict | None, str | None]:
    """Atomically reserve the one deployment slot."""
    init()
    now = time.time()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            f"SELECT * FROM deployments WHERE state IN ({','.join('?' * len(ACTIVE))}) "
            "ORDER BY id DESC LIMIT 1", ACTIVE).fetchone()
        if active and now - active["updated_at"] < 1800:
            conn.rollback()
            return None, f"deployment #{active['id']} is already {active['state']}"
        if active:  # abandoned guard/process; unblock while preserving the audit row
            conn.execute(
                "UPDATE deployments SET state='failed', detail=?, updated_at=? WHERE id=?",
                ("deployment process disappeared or exceeded 30 minutes", now, active["id"]),
            )
        cur = conn.execute(
            "INSERT INTO deployments (repo,branch,base,state,source,ref_id,server_touched,"
            "changed_files,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (repo, branch, base, "merging", source, ref_id,
             int(server_touched), json.dumps(changed_files), now, now),
        )
        conn.commit()
        return get(int(cur.lastrowid)), None


def get(deployment_id: int) -> dict | None:
    init()
    with _conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone())


def latest(*, source: str | None = None, ref_id: int | None = None) -> dict | None:
    init()
    where, args = [], []
    if source is not None:
        where.append("source=?")
        args.append(source)
    if ref_id is not None:
        where.append("ref_id=?")
        args.append(ref_id)
    query = "SELECT * FROM deployments"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY id DESC LIMIT 1"
    with _conn() as conn:
        return _row(conn.execute(query, args).fetchone())


def list_recent(limit: int = 30) -> list[dict]:
    init()
    with _conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM deployments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def update(deployment_id: int, **fields) -> None:
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{key}=?" for key in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE deployments SET {sets} WHERE id=?",
                     (*fields.values(), deployment_id))
        conn.commit()
