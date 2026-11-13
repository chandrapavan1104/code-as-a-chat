"""trade skill — the owner's controls for the trading desk.

Subcommands (passed via prompt):
  status            account, limits, mood, pause state
  list [all]        tickets waiting for a decision (or the latest 20)
  show <id>         one ticket with its full history
  approve <id>      approve and submit a ticket (paper mode only; live orders are
                    approved from the paired Gajala phone)
  reject <id>       turn a ticket down
  pause / resume    kill switch for all new orders
  watchlist         followed names, and which of them a buy may target
  watchlist confirm|unconfirm <SYM>
                    your own yes for a name research marked ELIGIBLE; a buy needs both

This skill is NOT exposed to the shell agent. Approving an order has to be the
owner typing /trade or tapping a ticket, never a model deciding mid-conversation
that it would be helpful.
"""

from __future__ import annotations

import time

from server.db import trading_store as store
from server.skills import register
from server.skills.base import Skill
from server.trading import desk


def _line(t: dict) -> str:
    tag = "paper" if t["mode"] == "paper" else "LIVE"
    return (f"#{t['id']} [{t['status']}] {tag} {t['side'].upper()} ${t['notional_usd']:.2f} "
            f"{t['symbol']} @ ${t['limit_price']:.2f}")


def _detail(t: dict) -> str:
    lines = [_line(t)]
    if t["reason"]:
        lines.append(f"Why: {t['reason']}")
    if t["thesis_ref"]:
        lines.append(f"Thesis: {t['thesis_ref']}")
    if t["max_loss_pct"] is not None:
        lines.append(f"Max loss: {t['max_loss_pct']:g}%")
    if t["gate_notes"]:
        lines.append(f"Rules: {t['gate_notes']}")
    if t["status"] == "proposed" and t["expires_at"]:
        hours = max(0.0, (t["expires_at"] - time.time()) / 3600)
        lines.append(f"Expires in {hours:.1f}h")
    lines.append("")
    for a in store.audit_log(t["id"]):
        stamp = time.strftime("%b %d %H:%M", time.localtime(a["at"]))
        lines.append(f"  {stamp}  {a['event']}" + (f"  ({a['actor']})" if a["actor"] else ""))
    return "\n".join(lines)


def _ticket_id(arg: str) -> int | None:
    arg = arg.strip().lstrip("#")
    return int(arg) if arg.isdigit() else None


class TradeSkill(Skill):
    name = "trade"
    description = "Trading desk: review, approve or reject order tickets"
    help_line = "/trade status | list | show <id> | approve <id> | reject <id> | pause | resume"
    expose_to_agent = False      # explicit owner command only; see module docstring
    final_output = True

    async def run(self, prompt: str = "", **kwargs) -> str:
        parts = (prompt or "").strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "status"
        arg = parts[1] if len(parts) > 1 else ""
        actor = kwargs.get("session_id") or "owner"
        try:
            if sub == "status":
                return await self._status()
            if sub == "list":
                return self._list(show_all=arg.strip().lower() == "all")
            if sub == "watchlist":
                words = arg.split()
                if len(words) == 2 and words[0].lower() in ("confirm", "unconfirm"):
                    symbol = words[1].upper()
                    if words[0].lower() == "unconfirm":
                        store.unconfirm_watchlist(symbol, actor=actor)
                        return f"{symbol} is no longer confirmed; buys of it are blocked."
                    entry = store.watchlist().get(symbol)
                    if not entry or entry["status"] != "ELIGIBLE":
                        return (f"{symbol} is not marked ELIGIBLE by research, so there is "
                                "nothing to confirm.")
                    store.confirm_watchlist(symbol, actor=actor)
                    return f"Confirmed {symbol}: buy tickets for it can now pass the gate."
                names = store.watchlist()
                if not names:
                    return ("The watchlist is empty. Buys are blocked until the weekly "
                            "screen fills it.")
                confirmed = store.confirmed_watchlist()

                def label(symbol: str, status: str) -> str:
                    if status != "ELIGIBLE":
                        return status
                    return "ELIGIBLE" if symbol in confirmed else "UNCONFIRMED"
                return ("Watchlist (a buy needs ELIGIBLE from research AND your "
                        "/trade watchlist confirm):\n" + "\n".join(
                            f"  {s:<6} {label(s, e['status']):<11} {e['memo']}".rstrip()
                            for s, e in names.items()))
            if sub in ("pause", "resume"):
                (desk.pause if sub == "pause" else desk.resume)(actor)
                return ("Trading paused. No ticket can be approved until /trade resume."
                        if sub == "pause" else "Trading resumed.")
            if sub in ("show", "approve", "reject"):
                ticket_id = _ticket_id(arg)
                if ticket_id is None:
                    return f"Usage: /trade {sub} <ticket id>"
                if sub == "show":
                    ticket = store.get_ticket(ticket_id)
                    return _detail(ticket) if ticket else f"No ticket #{ticket_id}."
                if sub == "reject":
                    return "Rejected.\n" + _line(desk.reject(ticket_id, actor=actor))
                ticket = await desk.approve(ticket_id, actor=actor)
                note = f"\n{ticket['gate_notes']}" if ticket["status"] == "rejected" else ""
                return _line(ticket) + note
        except (desk.DeskDisabled, desk.NeedsOwnerDevice) as exc:
            return str(exc)
        except (KeyError, ValueError) as exc:     # IllegalTransition is a ValueError
            return str(exc).strip("'\"")
        return self.help_line or ""

    async def _status(self) -> str:
        s = await desk.status()
        if not s["enabled"]:
            return "Trading desk is off (TRADING_ENABLED=false)."
        mode = "PAPER (simulated)" if s["mode"] == "paper" else "LIVE"
        lines = [
            f"Trading desk: {mode}" + ("  — PAUSED" if s["paused"] else ""),
            f"Account ${s['equity']:.2f}  (cash ${s['cash']:.2f}, peak ${s['peak_equity']:.2f}, "
            f"drawdown {s['drawdown']:.0%})",
            f"Market mood: {s['mood'] or 'unknown (treated as risk_off)'}",
            f"Today's buys: ${s['committed_today']:.2f} of ${s['limits']['max_daily_usd']:.2f}  "
            f"(per order ${s['limits']['max_order_usd']:.2f})",
            f"Approval: {'automatic' if s['auto_approve'] else 'you approve each ticket'}"
            + ("  (TRADE_AUTO_APPROVE is ignored in live mode)"
               if s.get("auto_approve_ignored") else ""),
            f"Open tickets: {s['open_tickets']}   Watchlist: {len(s['watchlist'])} names, "
            f"{sum(1 for k, v in s['watchlist'].items() if v == 'ELIGIBLE' and k in s['confirmed'])}"
            " buy-eligible (confirmed by you)",
        ]
        for symbol, value in sorted(s["positions"].items()):
            lines.append(f"  {symbol}  ${value:.2f}")
        return "\n".join(lines)

    def _list(self, *, show_all: bool) -> str:
        tickets = store.list_tickets(desk.mode(), None if show_all else store.OPEN_STATUSES,
                                     limit=20)
        if not tickets:
            return "No tickets." if show_all else "No tickets are waiting."
        return "\n".join(_line(t) for t in tickets)


register(TradeSkill())
