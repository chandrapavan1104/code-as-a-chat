"""Durable ledger for the trading desk: tickets, fills, watchlist, audit trail.

A ticket is one proposed order. It moves through a small state machine and every
move is written to `audit`, so "why did the bot buy this?" always has an answer
that does not depend on anyone's memory or on a model's account of itself.

    proposed ──► approved ──► submitted ──► filled
        │            │             └──────► failed
        ├──► rejected └──► failed
        └──► expired

Paper and live activity share the tables but never mix: every ticket and fill
carries its `mode`, and every query here takes one.

Stored at ~/.codeasachat/trading.db.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "trading.db"

TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"approved", "rejected", "expired"},
    "approved": {"submitted", "failed"},
    "submitted": {"filled", "failed"},
    "rejected": set(),
    "expired": set(),
    "filled": set(),
    "failed": set(),
}
OPEN_STATUSES = ("proposed", "approved", "submitted")


class IllegalTransition(ValueError):
    """A ticket was asked to move somewhere its current status does not allow."""


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def init() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                mode            TEXT NOT NULL,
                symbol          TEXT NOT NULL,
                side            TEXT NOT NULL,
                notional_usd    REAL NOT NULL,
                limit_price     REAL NOT NULL,
                thesis_ref      TEXT NOT NULL DEFAULT '',
                reason          TEXT NOT NULL DEFAULT '',
                max_loss_pct    REAL,
                source          TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL,
                gate_notes      TEXT NOT NULL DEFAULT '',
                broker_order_id TEXT,
                decided_by      TEXT,
                expires_at      REAL,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (mode, status, id DESC);
            CREATE TABLE IF NOT EXISTS fills (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id    INTEGER NOT NULL,
                mode         TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                side         TEXT NOT NULL,
                quantity     REAL NOT NULL,
                price        REAL NOT NULL,
                notional_usd REAL NOT NULL,
                filled_at    REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fills_mode ON fills (mode, filled_at);
            CREATE TABLE IF NOT EXISTS audit (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                at        REAL NOT NULL,
                event     TEXT NOT NULL,
                actor     TEXT NOT NULL DEFAULT '',
                detail    TEXT
            );
            CREATE TABLE IF NOT EXISTS state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol   TEXT PRIMARY KEY,
                memo     TEXT NOT NULL DEFAULT '',
                status   TEXT NOT NULL DEFAULT 'WATCH',
                added_at REAL NOT NULL
            );
            -- Names the owner has confirmed as buyable. Kept apart from the
            -- research-written watchlist, which a replace never touches here.
            CREATE TABLE IF NOT EXISTS watchlist_confirmed (
                symbol       TEXT PRIMARY KEY,
                confirmed_at REAL NOT NULL,
                actor        TEXT NOT NULL DEFAULT ''
            );
        """)
        c.commit()


def _audit(c: sqlite3.Connection, ticket_id: int | None, event: str,
           actor: str = "", detail: dict | None = None) -> None:
    c.execute("INSERT INTO audit (ticket_id, at, event, actor, detail) VALUES (?,?,?,?,?)",
              (ticket_id, time.time(), event, actor, json.dumps(detail or {})))


# ── tickets ───────────────────────────────────────────────────────────────────

def create_ticket(*, mode: str, symbol: str, side: str, notional_usd: float,
                  limit_price: float, thesis_ref: str = "", reason: str = "",
                  max_loss_pct: float | None = None, source: str = "",
                  status: str = "proposed", gate_notes: str = "",
                  expires_at: float | None = None) -> dict:
    """Insert a ticket. `status` is `proposed`, or `rejected` when the gate
    refused it: refused proposals are kept so the report can show them."""
    if status not in ("proposed", "rejected"):
        raise ValueError("a new ticket starts as proposed or rejected")
    init()
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tickets (mode, symbol, side, notional_usd, limit_price, thesis_ref, "
            "reason, max_loss_pct, source, status, gate_notes, expires_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mode, symbol, side, float(notional_usd), float(limit_price), thesis_ref,
             reason[:200], max_loss_pct, source, status, gate_notes, expires_at, now, now))
        ticket_id = int(cur.lastrowid)
        _audit(c, ticket_id, f"created:{status}", source,
               {"gate_notes": gate_notes} if gate_notes else None)
        c.commit()
    return get_ticket(ticket_id)  # type: ignore[return-value]


def get_ticket(ticket_id: int) -> dict | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def list_tickets(mode: str, statuses: tuple[str, ...] | None = None,
                 limit: int = 50) -> list[dict]:
    init()
    sql, args = "SELECT * FROM tickets WHERE mode = ?", [mode]
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args += list(statuses)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def transition(ticket_id: int, to: str, *, actor: str = "", detail: dict | None = None,
               broker_order_id: str | None = None, gate_notes: str | None = None) -> dict:
    """Move a ticket, or raise IllegalTransition. The check and the write happen
    in one transaction, so two approvals racing for one ticket cannot both win."""
    init()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            c.rollback()
            raise KeyError(f"no ticket #{ticket_id}")
        current = row["status"]
        if to not in TRANSITIONS.get(current, set()):
            c.rollback()
            raise IllegalTransition(f"ticket #{ticket_id} is {current}; cannot become {to}")
        sets, args = ["status = ?", "updated_at = ?"], [to, time.time()]
        if to in ("approved", "rejected"):
            sets.append("decided_by = ?")
            args.append(actor)
        if broker_order_id is not None:
            sets.append("broker_order_id = ?")
            args.append(broker_order_id)
        if gate_notes is not None:
            sets.append("gate_notes = ?")
            args.append(gate_notes)
        c.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", (*args, ticket_id))
        _audit(c, ticket_id, f"{current}->{to}", actor, detail)
        c.commit()
    return get_ticket(ticket_id)  # type: ignore[return-value]


def stale_proposals(mode: str, now: float | None = None) -> list[dict]:
    now = now or time.time()
    return [t for t in list_tickets(mode, ("proposed",), limit=500)
            if t["expires_at"] and t["expires_at"] <= now]


# ── fills and derived positions ───────────────────────────────────────────────

def record_fill(*, ticket_id: int, mode: str, symbol: str, side: str,
                quantity: float, price: float, filled_at: float | None = None) -> int:
    init()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO fills (ticket_id, mode, symbol, side, quantity, price, notional_usd, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ticket_id, mode, symbol, side, float(quantity), float(price),
             float(quantity) * float(price), filled_at or time.time()))
        _audit(c, ticket_id, "fill", "broker",
               {"quantity": quantity, "price": price, "side": side})
        c.commit()
        return int(cur.lastrowid)


def fills(mode: str) -> list[dict]:
    init()
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM fills WHERE mode = ? ORDER BY filled_at, id", (mode,)).fetchall()]


def position_quantities(mode: str) -> dict[str, float]:
    """Net shares per symbol from the fills ledger (zeroed-out names dropped)."""
    held: dict[str, float] = {}
    for f in fills(mode):
        sign = 1.0 if f["side"] == "buy" else -1.0
        held[f["symbol"]] = held.get(f["symbol"], 0.0) + sign * f["quantity"]
    return {s: q for s, q in held.items() if q > 1e-9}


def net_cash_flow(mode: str) -> float:
    """Cash spent on buys minus cash returned by sells (positive = net spent)."""
    return sum((1.0 if f["side"] == "buy" else -1.0) * f["notional_usd"] for f in fills(mode))


def committed_today(mode: str, now: float | None = None) -> float:
    """Dollars of BUY orders committed since local midnight: filled, plus
    approved or submitted tickets that have not filled yet. Proposals awaiting a
    decision do not count; they are checked again at approval."""
    now = now or time.time()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    init()
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(notional_usd), 0) AS total FROM tickets "
            "WHERE mode = ? AND side = 'buy' AND status IN ('approved','submitted','filled') "
            "AND updated_at >= ?", (mode, midnight)).fetchone()
    return float(row["total"])


# ── key/value state (pause flag, peak equity, market mood) ────────────────────

def get_state(key: str, default: str | None = None) -> str | None:
    init()
    with _conn() as c:
        row = c.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str, *, actor: str = "") -> None:
    init()
    with _conn() as c:
        c.execute("INSERT INTO state (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        _audit(c, None, f"state:{key}", actor, {"value": value})
        c.commit()


# ── watchlist ─────────────────────────────────────────────────────────────────
# Two states. WATCH: being followed, not buyable. ELIGIBLE: every trust check in
# the research memo passed, so a buy may target it. Anything unrecognised is WATCH.

def replace_watchlist(entries: list[dict], *, actor: str = "") -> None:
    init()
    now = time.time()
    with _conn() as c:
        c.execute("DELETE FROM watchlist")
        for e in entries:
            status = "ELIGIBLE" if str(e.get("status") or "").upper() == "ELIGIBLE" else "WATCH"
            c.execute("INSERT OR REPLACE INTO watchlist (symbol, memo, status, added_at) "
                      "VALUES (?,?,?,?)",
                      (str(e["symbol"]).upper(), str(e.get("memo") or ""), status, now))
        _audit(c, None, "watchlist:replaced", actor,
               {"symbols": [str(e["symbol"]).upper() for e in entries]})
        c.commit()


def watchlist() -> dict[str, dict]:
    """{symbol: {"memo": path, "status": "WATCH" | "ELIGIBLE"}}."""
    init()
    with _conn() as c:
        return {r["symbol"]: {"memo": r["memo"], "status": r["status"]} for r in c.execute(
            "SELECT symbol, memo, status FROM watchlist ORDER BY symbol").fetchall()}


def confirm_watchlist(symbol: str, *, actor: str = "owner") -> None:
    """The owner's own yes for one name. Research can mark a name ELIGIBLE, but
    the research run reads the web, so its say-so alone never makes a name
    buyable."""
    init()
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO watchlist_confirmed (symbol, confirmed_at, actor) "
                  "VALUES (?,?,?)", (symbol.upper(), time.time(), actor))
        _audit(c, None, "watchlist:confirmed", actor, {"symbol": symbol.upper()})
        c.commit()


def unconfirm_watchlist(symbol: str, *, actor: str = "owner") -> bool:
    init()
    with _conn() as c:
        cur = c.execute("DELETE FROM watchlist_confirmed WHERE symbol = ?", (symbol.upper(),))
        _audit(c, None, "watchlist:unconfirmed", actor, {"symbol": symbol.upper()})
        c.commit()
        return cur.rowcount == 1


def confirmed_watchlist() -> set[str]:
    init()
    with _conn() as c:
        return {r["symbol"] for r in c.execute("SELECT symbol FROM watchlist_confirmed")}


def audit_log(ticket_id: int | None = None, limit: int = 100) -> list[dict]:
    init()
    with _conn() as c:
        if ticket_id is None:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM audit WHERE ticket_id = ? ORDER BY id",
                             (ticket_id,)).fetchall()
    return [dict(r) for r in rows]
