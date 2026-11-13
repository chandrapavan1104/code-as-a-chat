"""The trading desk: the only code path from a proposal to an order.

    propose()  ── gate ──►  ticket (proposed)  ── phone ──►  approve()
                                                               │ gate again
                                                               ▼
                                                            broker.submit()

Rules that hold everywhere in this module:
  • Nothing moves unless TRADING_ENABLED is on.
  • The gate (`risk.check`) runs twice: when a proposal arrives and again at the
    moment of approval, against a fresh quote. Prices move while a ticket waits.
  • A paper ticket can only ever reach the paper broker, and a live ticket only
    the live one. Switching TRADING_MODE strands open tickets rather than
    re-routing them.
  • Every refusal is stored with its reasons, so the weekly report can show what
    was turned down and why.
"""

from __future__ import annotations

import logging
import math
import time

from server import config
from server.db import trading_store as store
from server.trading import risk
from server.trading.broker import (Broker, MarketData, NoMarketData, OrderResult,
                                   PaperBroker, UnconfiguredBroker)

log = logging.getLogger("trading.desk")

MOODS = ("risk_on", "neutral", "risk_off")

_data: MarketData = NoMarketData()
_live_broker: Broker = UnconfiguredBroker()
_paper_broker: Broker = PaperBroker()


class DeskDisabled(RuntimeError):
    """Trading is switched off in config."""


class NeedsOwnerDevice(PermissionError):
    """A live order may only be approved from the owner's paired phone."""


def configure(*, data: MarketData | None = None, live_broker: Broker | None = None) -> None:
    """Register the quote source and/or the live broker (done once at startup)."""
    global _data, _live_broker
    if data is not None:
        _data = data
    if live_broker is not None:
        _live_broker = live_broker


# ── settings ──────────────────────────────────────────────────────────────────

def mode() -> str:
    return "live" if config.TRADING_MODE == "live" else "paper"


def auto_approve() -> bool:
    """Auto-approve only ever applies to paper. Proposals come from a research run
    that reads the web, so in live mode a person approves every order."""
    return config.TRADE_AUTO_APPROVE and mode() == "paper"


def limits() -> risk.Limits:
    return risk.Limits(max_order_usd=config.TRADE_MAX_ORDER_USD,
                       max_daily_usd=config.TRADE_MAX_DAILY_USD)


def is_paused() -> bool:
    return store.get_state("paused", "0") == "1"


def _require_enabled() -> None:
    if not config.TRADING_ENABLED:
        raise DeskDisabled("Trading is off. Set TRADING_ENABLED=true to use the desk.")


def pause(actor: str = "owner") -> None:
    store.set_state("paused", "1", actor=actor)


def resume(actor: str = "owner") -> None:
    store.set_state("paused", "0", actor=actor)


def set_mood(mood: str, actor: str = "") -> None:
    if mood not in MOODS:
        raise ValueError(f"mood must be one of {', '.join(MOODS)}")
    store.set_state("mood", mood, actor=actor)


# ── account view ──────────────────────────────────────────────────────────────

async def _account(current_mode: str, focus: str | None = None
                   ) -> tuple[float, float, dict[str, float], risk.Quote | None]:
    """(equity, cash, {symbol: market value}, quote for `focus`)."""
    focus_quote = await _data.quote(focus) if focus else None
    if current_mode == "live":
        acct = await _data.account()
        if acct is None:
            return 0.0, 0.0, {}, focus_quote       # unknown account: the gate refuses
        return acct.equity, acct.cash, dict(acct.positions), focus_quote

    held = store.position_quantities("paper")
    quotes = await _data.quotes(sorted(held)) if held else {}
    if focus and focus_quote:
        quotes[focus] = focus_quote
    last_fill = {f["symbol"]: f["price"] for f in store.fills("paper")}
    positions = {}
    for symbol, qty in held.items():
        # A held name with no quote is marked at its last fill price, so a
        # quote outage cannot make the paper account look empty.
        price = quotes[symbol].last if symbol in quotes else last_fill.get(symbol, 0.0)
        positions[symbol] = qty * price
    cash = config.TRADE_PAPER_START_CASH - store.net_cash_flow("paper")
    return cash + sum(positions.values()), cash, positions, focus_quote


def _peak(current_mode: str, equity: float) -> float:
    key = f"peak_equity:{current_mode}"
    peak = float(store.get_state(key, "0") or 0)
    if math.isfinite(equity) and equity > peak:
        store.set_state(key, f"{equity:.4f}", actor="desk")
        return equity
    return peak


async def snapshot(symbol: str | None = None) -> risk.Snapshot:
    current = mode()
    equity, cash, positions, quote = await _account(current, symbol)
    names = store.watchlist()
    confirmed = store.confirmed_watchlist()
    eligible = {s for s, e in names.items() if e["status"] == "ELIGIBLE"}
    return risk.Snapshot(
        equity=equity, cash=cash, positions=positions,
        peak_equity=_peak(current, equity),
        committed_today=store.committed_today(current),
        mood=store.get_state("mood"), paused=is_paused(),
        watchlist=frozenset(eligible & confirmed),
        watching=frozenset(s for s, e in names.items() if e["status"] != "ELIGIBLE"),
        unconfirmed=frozenset(eligible - confirmed),
        quote=quote)


# ── proposals ─────────────────────────────────────────────────────────────────

def _parse(raw: dict) -> tuple[risk.Proposal, dict]:
    """Turn an untrusted proposal dict into typed fields, or raise ValueError.
    Only these fields are read; anything else in the dict is ignored."""
    if not isinstance(raw, dict):
        raise ValueError("a proposal must be an object")
    try:
        proposal = risk.Proposal(
            symbol=str(raw["symbol"]).strip().upper(),
            side=str(raw["side"]).strip().lower(),
            notional_usd=float(raw["notional_usd"]),
            limit_price=float(raw["limit_price"]),
            thesis_ref=str(raw.get("thesis_ref") or "").strip()[:200],
            asset_class=str(raw.get("asset_class") or "stock").strip().lower())
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed proposal: {exc}") from exc
    max_loss = raw.get("max_loss_pct")
    extras = {
        "reason": " ".join(str(raw.get("reason") or "").split())[:200],
        "max_loss_pct": float(max_loss) if isinstance(max_loss, (int, float)) else None,
    }
    return proposal, extras


def _headline(t: dict) -> str:
    tag = "PAPER " if t["mode"] == "paper" else ""
    return (f"{tag}{t['side'].upper()} ${t['notional_usd']:.2f} {t['symbol']} "
            f"@ limit ${t['limit_price']:.2f}")


async def _notify(kind: str, ticket: dict, body: str, *, needs_response: bool = False) -> None:
    from server.notifier import notify_app
    titles = {"ticket": f"Trade ticket #{ticket['id']}", "filled": f"Filled #{ticket['id']}",
              "submitted": f"Order sent #{ticket['id']}", "failed": f"Order failed #{ticket['id']}",
              "refused": f"Ticket #{ticket['id']} refused"}
    try:
        await notify_app("trade_ticket" if kind == "ticket" else "trade_status",
                         title=f"{titles[kind]}: {_headline(ticket)}", body=body,
                         needs_response=needs_response, ref_kind="trade_ticket",
                         ref_id=ticket["id"], telegram=True)
    except Exception as exc:   # a push problem must never change a ticket's fate
        log.warning("trade notification failed: %s", exc)


async def propose(raw: dict, *, source: str = "") -> dict:
    """Run a proposal through the gate and store the outcome as a ticket."""
    _require_enabled()
    proposal, extras = _parse(raw)
    current = mode()
    violations = risk.check(proposal, await snapshot(proposal.symbol), limits())
    common = dict(mode=current, symbol=proposal.symbol, side=proposal.side,
                  notional_usd=proposal.notional_usd, limit_price=proposal.limit_price,
                  thesis_ref=proposal.thesis_ref, source=source, **extras)
    if violations:
        ticket = store.create_ticket(status="rejected", gate_notes="; ".join(violations), **common)
        log.info("proposal refused by gate: %s", ticket["gate_notes"])
        return ticket

    ticket = store.create_ticket(
        status="proposed", expires_at=time.time() + config.TRADE_TICKET_TTL_HOURS * 3600, **common)
    if auto_approve():
        return await approve(ticket["id"], actor="auto")
    body = "\n".join(x for x in (
        ticket["reason"], f"Thesis: {ticket['thesis_ref']}" if ticket["thesis_ref"] else "",
        f"Reply 'approve' or 'reject', or /trade approve {ticket['id']}") if x)
    await _notify("ticket", ticket, body, needs_response=True)
    return ticket


# ── decisions ─────────────────────────────────────────────────────────────────

async def approve(ticket_id: int, *, actor: str = "owner",
                  owner_device: bool = False) -> dict:
    """Approve and submit one ticket. Returns the ticket in its final state for
    this call: filled, submitted, failed, rejected (gate) or expired.

    A live ticket needs `owner_device`: the request carried the paired phone's
    key (server/db/shell_store.py). The shared API token alone is not enough,
    because anything holding it can also start a coding run that writes a
    proposal file, and must not be able to approve its own order."""
    _require_enabled()
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(f"no ticket #{ticket_id}")
    current = mode()
    if current == "live" and not owner_device:
        raise NeedsOwnerDevice(
            "Live orders can only be approved from your paired Gajala phone "
            "(Alerts → the ticket). Telegram and /trade approve work in paper mode only.")
    if ticket["mode"] != current:
        raise store.IllegalTransition(
            f"ticket #{ticket_id} is a {ticket['mode']} ticket; the desk is in {current} mode")
    if ticket["status"] != "proposed":
        raise store.IllegalTransition(f"ticket #{ticket_id} is already {ticket['status']}")
    if ticket["expires_at"] and ticket["expires_at"] <= time.time():
        return store.transition(ticket_id, "expired", actor="desk")

    proposal = risk.Proposal(symbol=ticket["symbol"], side=ticket["side"],
                             notional_usd=ticket["notional_usd"],
                             limit_price=ticket["limit_price"], thesis_ref=ticket["thesis_ref"])
    violations = risk.check(proposal, await snapshot(ticket["symbol"]), limits())
    if violations:
        notes = "; ".join(violations)
        refused = store.transition(ticket_id, "rejected", actor="gate", gate_notes=notes,
                                   detail={"at": "approval", "violations": violations})
        await _notify("refused", refused, f"No longer passes the rules: {notes}")
        return refused

    # From here the ticket is claimed: a second approve() raises IllegalTransition.
    ticket = store.transition(ticket_id, "approved", actor=actor)
    broker = _paper_broker if current == "paper" else _live_broker
    try:
        result = await broker.submit(ticket)
    except Exception as exc:
        log.exception("broker raised on ticket #%s", ticket_id)
        result = OrderResult(status="failed", detail=f"broker error: {exc}")

    if result.status == "failed":
        failed = store.transition(ticket_id, "failed", actor="broker",
                                  detail={"detail": result.detail})
        await _notify("failed", failed, result.detail)
        return failed

    ticket = store.transition(ticket_id, "submitted", actor="broker",
                              broker_order_id=result.order_id, detail={"detail": result.detail})
    if result.status == "filled":
        return await record_fill(ticket_id, quantity=result.quantity, price=result.price)
    await _notify("submitted", ticket, result.detail or "Waiting for the fill.")
    return ticket


async def record_fill(ticket_id: int, *, quantity: float, price: float) -> dict:
    """Book a fill for a submitted ticket (called by the broker path and by
    reconciliation when a resting live order fills later)."""
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(f"no ticket #{ticket_id}")
    if ticket["side"] == "sell":
        # Never book more shares out than the ledger holds.
        held = store.position_quantities(ticket["mode"]).get(ticket["symbol"], 0.0)
        quantity = min(quantity, held)
    filled = store.transition(ticket_id, "filled", actor="broker",
                              detail={"quantity": quantity, "price": price})
    store.record_fill(ticket_id=ticket_id, mode=ticket["mode"], symbol=ticket["symbol"],
                      side=ticket["side"], quantity=quantity, price=price)
    await _notify("filled", filled, f"{quantity:g} shares at ${price:.2f}")
    return filled


def reject(ticket_id: int, *, actor: str = "owner") -> dict:
    return store.transition(ticket_id, "rejected", actor=actor)


APPROVE_WORDS = ("approve", "approved", "yes")


def is_approval(response: str) -> bool:
    return (response or "").strip().lower().rstrip(".!") in APPROVE_WORDS


async def respond(ticket_id: int, response: str, *, actor: str = "owner",
                  owner_device: bool = False) -> dict:
    """Handle a free-text answer to a ticket notification. Only an unambiguous
    yes or no does anything; any other text leaves the ticket waiting. Casual
    acknowledgements ("ok", "go", "y") never move money."""
    word = (response or "").strip().lower().rstrip(".!")
    if word in APPROVE_WORDS:
        return await approve(ticket_id, actor=actor, owner_device=owner_device)
    if word in ("reject", "rejected", "no", "n", "skip", "cancel"):
        return reject(ticket_id, actor=actor)
    raise ValueError("Answer 'approve' or 'reject'.")


def expire_stale() -> list[dict]:
    """Expire proposals nobody answered in time (called by the runner tick)."""
    return [store.transition(t["id"], "expired", actor="desk")
            for t in store.stale_proposals(mode())]


def sync_watchlist(entries: list[dict], *, actor: str = "researcher") -> int:
    """Replace the watchlist. Entries without a plain ticker are dropped, and an
    entry is buy-eligible only if it says so in exactly those words."""
    clean = [{"symbol": str(e.get("symbol") or "").strip().upper(),
              "memo": str(e.get("memo") or "")[:200],
              "status": str(e.get("status") or "")}
             for e in entries if isinstance(e, dict)]
    clean = [e for e in clean if risk._SYMBOL.match(e["symbol"])]
    store.replace_watchlist(clean, actor=actor)
    return len(clean)


async def status() -> dict:
    current = mode()
    equity, cash, positions, _ = await _account(current)
    peak = _peak(current, equity)
    return {
        "enabled": config.TRADING_ENABLED, "mode": current, "paused": is_paused(),
        "auto_approve": auto_approve(),
        "auto_approve_ignored": config.TRADE_AUTO_APPROVE and not auto_approve(),
        "equity": equity, "cash": cash, "positions": positions, "peak_equity": peak,
        "drawdown": max(0.0, 1 - equity / peak) if peak > 0 else 0.0,
        "mood": store.get_state("mood"),
        "committed_today": store.committed_today(current),
        "open_tickets": len(store.list_tickets(current, store.OPEN_STATUSES)),
        "watchlist": {s: e["status"] for s, e in store.watchlist().items()},
        "confirmed": sorted(store.confirmed_watchlist()),
        "limits": {"max_order_usd": config.TRADE_MAX_ORDER_USD,
                   "max_daily_usd": config.TRADE_MAX_DAILY_USD},
    }
