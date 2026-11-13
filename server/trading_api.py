"""Structured trading-desk endpoints for the app (mounted under /api).

Read endpoints plus the two decisions an owner can make on a ticket. There is
deliberately no endpoint that creates a ticket: proposals enter only through the
runner's ingest of the research project's outbox, so a leaked API token cannot
be used to invent an order.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from server.db import shell_store, trading_store as store
from server.trading import desk

router = APIRouter()


def _view(t: dict) -> dict:
    return {k: t[k] for k in (
        "id", "mode", "symbol", "side", "notional_usd", "limit_price", "thesis_ref",
        "reason", "max_loss_pct", "source", "status", "gate_notes", "broker_order_id",
        "decided_by", "expires_at", "created_at", "updated_at")}


@router.get("/trade/status")
async def trade_status():
    return await desk.status()


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
