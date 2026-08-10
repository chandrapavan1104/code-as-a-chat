"""Shadow-mode routing recommendations.

For each job, record what the intelligent dispatcher recommends without
changing the live assignment. This lets us evaluate, iterate, and gain
confidence before activating live routing.

Structure:
  job_id → recommendation (engine choice + rationale)
  recommendation → alternatives, scores, quota snapshot, confidence
  evaluation → compare recommendation to actual outcome
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "routing_recommendations.db"


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
            CREATE TABLE IF NOT EXISTS recommendations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          INTEGER NOT NULL UNIQUE,
                recommended_engine TEXT NOT NULL,
                alternatives    TEXT NOT NULL,
                scores          TEXT NOT NULL,
                quota_snapshot  TEXT NOT NULL,
                confidence      REAL NOT NULL,
                rationale       TEXT NOT NULL,
                features_summary TEXT,
                created_at      REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recommendations_job_id "
            "ON recommendations(job_id)"
        )
        conn.commit()


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    try:
        d["alternatives"] = json.loads(d["alternatives"])
    except (TypeError, json.JSONDecodeError):
        d["alternatives"] = []
    try:
        d["scores"] = json.loads(d["scores"])
    except (TypeError, json.JSONDecodeError):
        d["scores"] = {}
    try:
        d["quota_snapshot"] = json.loads(d["quota_snapshot"])
    except (TypeError, json.JSONDecodeError):
        d["quota_snapshot"] = {}
    return d


def record(*, job_id: int, recommended_engine: str,
           alternatives: list[tuple[str, float]],  # [(engine, score), ...]
           scores: dict[str, float],              # engine → score
           quota_snapshot: dict[str, float],      # engine → usage %
           confidence: float,                     # 0.0–1.0
           rationale: str,
           features_summary: str = "") -> int:
    """Record a routing recommendation for a job.

    Args:
        job_id: The Night Shift queue job id
        recommended_engine: Best-match engine ("claude", "codex", "gemini")
        alternatives: [(engine, score), ...] for other candidates
        scores: Per-engine score breakdown
        quota_snapshot: {engine: usage_percent}
        confidence: 0.0–1.0 expressing decision certainty
        rationale: Human-readable explanation of the choice
        features_summary: Text description of extracted features
    """
    init()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT OR REPLACE INTO recommendations "
            "(job_id, recommended_engine, alternatives, scores, "
            "quota_snapshot, confidence, rationale, features_summary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, recommended_engine, json.dumps(alternatives),
             json.dumps(scores), json.dumps(quota_snapshot),
             confidence, rationale, features_summary, time.time()),
        )
        conn.commit()
        return int(cur.lastrowid)


def get(job_id: int) -> dict | None:
    """Fetch the recommendation for a job."""
    init()
    with _conn() as conn:
        return _row(conn.execute(
            "SELECT * FROM recommendations WHERE job_id = ?",
            (job_id,)
        ).fetchone())


def list_recent(limit: int = 50) -> list[dict]:
    """Recent recommendations, newest first."""
    init()
    with _conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM recommendations ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()]


def list_by_engine(engine: str, limit: int = 50) -> list[dict]:
    """Recommendations for a specific engine."""
    init()
    with _conn() as conn:
        return [_row(r) for r in conn.execute(
            "SELECT * FROM recommendations WHERE recommended_engine = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (engine, limit)
        ).fetchall()]


def count() -> int:
    """Total recommendations recorded."""
    init()
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
