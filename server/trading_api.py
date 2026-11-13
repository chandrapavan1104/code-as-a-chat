"""Structured trading-desk endpoints for the app (mounted under /api).

`/trade/overview` is everything the Gajala Trading screen shows in one call;
the rest are the owner's controls: decide a ticket, confirm a watchlist name,
pause/resume, and read the research project's memos and reports.

There is no endpoint that creates a ticket: proposals enter only through the
runner's ingest of the research project's outbox. Approving a LIVE ticket also
needs the paired phone's key (`X-Shell-Device`), because the shared API token
can start a coding run that writes a proposal file.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException

from server import config
from server.db import shell_store, trading_store as store
from server.trading import desk, risk

router = APIRouter()

ACCOUNT_TTL = 60.0          # seconds; a live account read is a short Claude run
ACCOUNT_TIMEOUT = 25.0
_DOC = re.compile(r"^(memos|reports)/[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.md$")
MAX_DOC_BYTES = 200_000
_account_cache: dict[str, tuple[float, dict]] = {}


def _view(t: dict) -> dict:
    return {k: t[k] for k in (
        "id", "mode", "symbol", "side", "notional_usd", "limit_price", "thesis_ref",
        "reason", "max_loss_pct", "source", "status", "gate_notes", "broker_order_id",
        "decided_by", "expires_at", "created_at", "updated_at")}


@router.get("/trade/status")
async def trade_status():
    return await desk.status()


async def _account(refresh: bool) -> tuple[dict | None, float | None, str | None]:
    """(status, as_of, error). Cached briefly: in live mode each read is a
    restricted Claude run, and the screen refreshes often."""
    current = desk.mode()
    hit = _account_cache.get(current)
    if hit and not refresh and time.time() - hit[0] < ACCOUNT_TTL:
        return hit[1], hit[0], None
    try:
        status = await asyncio.wait_for(desk.status(), ACCOUNT_TIMEOUT)
    except asyncio.TimeoutError:
        return (hit[1] if hit else None), (hit[0] if hit else None), \
            "The account is taking a while to read; showing the last known values."
    except Exception:
        return (hit[1] if hit else None), (hit[0] if hit else None), \
            "Could not read the account just now."
    _account_cache[current] = (time.time(), status)
    return status, _account_cache[current][0], None


def _reports() -> list[dict]:
    folder = Path(config.FINANCE_PROJECT_DIR) / "reports"
    try:
        files = [p for p in folder.glob("*.md") if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"path": f"reports/{p.name}", "title": _report_title(p.stem),
             "modified": p.stat().st_mtime} for p in files[:20]]


def _report_title(stem: str) -> str:
    """'2026-11-12-weekly-screen' -> 'Weekly screen · 2026-11-12'."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)$", stem)
    if not m:
        return stem.replace("-", " ").capitalize()
    return f"{m.group(2).replace('-', ' ').capitalize()} · {m.group(1)}"


@router.get("/trade/overview")
async def trade_overview(refresh: bool = False,
                         x_shell_device: str | None = Header(default=None)):
    """Everything the Trading screen shows, in one response."""
    current = desk.mode()
    status, as_of, account_error = await _account(refresh)
    limits = risk.Limits(max_order_usd=config.TRADE_MAX_ORDER_USD,
                         max_daily_usd=config.TRADE_MAX_DAILY_USD)
    confirmed = store.confirmed_watchlist()
    open_tickets = store.list_tickets(current, store.OPEN_STATUSES, limit=50)
    recent = [t for t in store.list_tickets(current, None, limit=40)
              if t["status"] not in store.OPEN_STATUSES][:20]
    bound = shell_store.binding()
    return {
        "enabled": config.TRADING_ENABLED, "mode": current, "paused": desk.is_paused(),
        "auto_approve": desk.auto_approve(),
        "mood": store.get_state("mood"),
        "committed_today": store.committed_today(current),
        "limits": {"max_order_usd": limits.max_order_usd, "max_daily_usd": limits.max_daily_usd,
                   "max_positions": limits.max_positions,
                   "max_position_pct": limits.max_position_pct,
                   "cash_floor_pct": limits.cash_floor_pct,
                   "ticket_hours": config.TRADE_TICKET_TTL_HOURS},
        "account": None if status is None else {
            k: status[k] for k in ("equity", "cash", "positions", "peak_equity", "drawdown")},
        "account_as_of": as_of, "account_error": account_error,
        "open_tickets": [_view(t) for t in open_tickets],
        "recent_tickets": [_view(t) for t in recent],
        "watchlist": [{"symbol": sym, "status": e["status"], "memo": e["memo"],
                       "confirmed": sym in confirmed}
                      for sym, e in store.watchlist().items()],
        "reports": _reports(),
        "phone": {"paired": bool(bound), "this_device": owner_device(x_shell_device)},
        "server_time": time.time(),
    }


@router.post("/trade/watchlist/{symbol}/confirm")
def trade_confirm(symbol: str):
    symbol = symbol.strip().upper()
    entry = store.watchlist().get(symbol)
    if not entry or entry["status"] != "ELIGIBLE":
        raise HTTPException(409, f"{symbol} is not marked eligible by research yet.")
    store.confirm_watchlist(symbol, actor="app")
    return {"symbol": symbol, "confirmed": True}


@router.delete("/trade/watchlist/{symbol}/confirm")
def trade_unconfirm(symbol: str):
    symbol = symbol.strip().upper()
    store.unconfirm_watchlist(symbol, actor="app")
    return {"symbol": symbol, "confirmed": False}


@router.post("/trade/pause")
def trade_pause():
    desk.pause(actor="app")
    return {"paused": True}


@router.post("/trade/resume")
def trade_resume():
    desk.resume(actor="app")
    return {"paused": False}


@router.get("/trade/doc")
def trade_doc(path: str):
    """A research memo or report (markdown) from the finance project, read-only."""
    if not _DOC.match(path or ""):
        raise HTTPException(400, "Only memos/<name>.md and reports/<name>.md can be opened.")
    root = Path(config.FINANCE_PROJECT_DIR).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "Not found.")
    if target.stat().st_size > MAX_DOC_BYTES:
        raise HTTPException(413, "That document is too large to show.")
    return {"path": path, "text": target.read_text(encoding="utf-8", errors="replace"),
            "modified": target.stat().st_mtime}


@router.get("/trade/tickets")
def trade_tickets(status: str = "open", limit: int = 50):
    statuses = store.OPEN_STATUSES if status == "open" else None if status == "all" else (status,)
    if statuses and any(s not in store.TRANSITIONS for s in statuses):
        raise HTTPException(422, "unknown status")
    return {"tickets": [_view(t) for t in store.list_tickets(
        desk.mode(), statuses, limit=max(1, min(limit, 200)))]}


@router.get("/trade/tickets/{ticket_id}")
def trade_ticket(ticket_id: int):
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(404, "no such ticket")
    return {"ticket": _view(ticket), "history": store.audit_log(ticket_id)}


def owner_device(x_shell_device: str | None) -> bool:
    """True when the request carries the paired phone's key. Live approvals need
    it; the API token alone is shared with Telegram, widgets and coding runs."""
    return bool(x_shell_device) and shell_store.matches(x_shell_device)


async def decide(ticket_id: int, decision: str, actor: str, *,
                 device: bool = False) -> dict:
    """Shared by the two routes below."""
    try:
        if decision == "approve":
            ticket = await desk.approve(ticket_id, actor=actor, owner_device=device)
        else:
            ticket = desk.reject(ticket_id, actor=actor)
    except desk.NeedsOwnerDevice as exc:
        raise HTTPException(403, str(exc)) from exc
    except desk.DeskDisabled as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "no such ticket") from exc
    except ValueError as exc:               # IllegalTransition: already decided
        raise HTTPException(409, str(exc)) from exc
    return _view(ticket)


@router.post("/trade/tickets/{ticket_id}/approve")
async def trade_approve(ticket_id: int, x_shell_device: str | None = Header(default=None)):
    return {"ticket": await decide(ticket_id, "approve", "app",
                                   device=owner_device(x_shell_device))}


@router.post("/trade/tickets/{ticket_id}/reject")
async def trade_reject(ticket_id: int):
    return {"ticket": await decide(ticket_id, "reject", "app")}
