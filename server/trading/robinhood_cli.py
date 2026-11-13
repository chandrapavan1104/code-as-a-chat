"""Robinhood access through narrowly scoped headless Claude runs.

The brokerage connector lives inside the Claude CLI this server already uses.
Each call here starts a fresh, short run that:
  • runs in an empty directory with every built-in tool switched off except the
    loader for connector tools, so it can read no file and run no command;
  • is allowed only the few connector tools named for that call;
  • receives a fixed prompt built from typed, validated values, never free text
    from a proposal, a post or an article;
  • must answer with one JSON object, which is then re-validated here in code.

Two things use it: `ClaudeRobinhoodData` (read-only quotes and account) and
`ClaudeRobinhoodBroker` (the only place an order tool is ever allowed).

A model sits in these paths, so its answers are treated as claims: quotes are
bounds-checked, the account must match the configured last four digits, and an
order is confirmed by a second, read-only run before it is believed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path

from server import config
from server.db import trading_store as store
from server.trading.broker import Account, OrderResult
from server.trading.risk import Quote

log = logging.getLogger("trading.robinhood")

_CWD = Path.home() / ".codeasachat" / "trading_sandbox"
_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_QUOTE_TTL = 45.0       # seconds; propose and approve re-quote, status reuses

READ_QUOTES = ("get_equity_quotes",)
READ_ACCOUNT = ("get_accounts", "get_portfolio", "get_equity_positions", "get_equity_quotes")
READ_ORDERS = ("get_accounts", "get_equity_orders")
PLACE_ORDER = ("get_accounts", "review_equity_order", "place_equity_order", "get_equity_orders")


def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float, str)):
        return None
    try:
        value = float(x)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _extract_json(text: str) -> dict | None:
    """The first JSON object in a reply (models sometimes wrap it in a fence)."""
    start = (text or "").find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


_ATTEMPTS = 4


def _preamble(tools: tuple[str, ...]) -> str:
    names = ",".join(config.BROKER_TOOL_PREFIX + t for t in tools)
    return ("First load the tools this task needs by calling ToolSearch with the query "
            f'"select:{names}" (it waits for the connector if it is still starting). '
            "Use no other tools than those.\n\n")


async def run_restricted(prompt: str, tools: tuple[str, ...], *, timeout: int = 120,
                         model: str | None = None, report: dict | None = None) -> dict | None:
    """One headless Claude run limited to `tools` from the brokerage connector.
    Returns the JSON object it answered with, or None on any failure.

    The connector is sometimes still connecting when a short run starts, and the
    run then ends without having had any connector tool (seen in roughly one
    start in four). A run that finished in a single turn made no tool call at
    all, so it cannot have read or changed anything, and whatever it answered is
    not based on the broker. Exactly that case is retried. A run that did call a
    tool is never retried, whatever it returned.

    `report`, when given, gets `touched`: False only when every attempt is known
    to have made no tool call. A caller that moves money uses it to tell "nothing
    happened" from "unknown".
    """
    if report is not None:
        report["touched"] = False
    for attempt in range(_ATTEMPTS):
        reply, turns = await _run_once(_preamble(tools) + prompt, tools,
                                       timeout=timeout, model=model)
        if turns is None or turns > 1:
            if report is not None:
                report["touched"] = True
            return reply
        log.info("connector tools were not ready (attempt %s)", attempt + 1)
        await asyncio.sleep(2 * (attempt + 1))
    return None


async def _run_once(prompt: str, tools: tuple[str, ...], *, timeout: int,
                    model: str | None) -> tuple[dict | None, int | None]:
    """(answer, number of turns). Turns is None when the run itself failed."""
    _CWD.mkdir(parents=True, exist_ok=True)
    allowed = ["ToolSearch", *(config.BROKER_TOOL_PREFIX + t for t in tools)]
    denied = [config.BROKER_TOOL_PREFIX + t for t in config.BROKER_ORDER_TOOLS
              if t not in tools]
    argv = ["claude", "-p", prompt, "--output-format", "json",
            "--model", model or config.TRADE_CLI_MODEL,
            # The only built-in is the loader for connector tools: no files, no
            # shell, no web. Connector tools are loaded on demand through it.
            "--tools", "ToolSearch",
            "--allowedTools", ",".join(allowed),
            "--disallowedTools", ",".join(denied)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(_CWD), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        log.warning("restricted run could not start: %r", exc)
        return None, None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        # Kill it: a run left alive could still act after we have given up on it.
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        log.warning("restricted run timed out after %ss and was killed", timeout)
        return None, None
    if proc.returncode != 0:
        log.warning("restricted run exited %s", proc.returncode)
        return None, None
    try:
        envelope = json.loads(stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(envelope, dict) or envelope.get("is_error"):
        # e.g. a usage limit: the reply text is an error message, not an answer.
        if isinstance(envelope, dict):
            log.warning("restricted run returned an error: %s",
                        str(envelope.get("api_error") or envelope.get("result"))[:120])
        return None, None
    turns = envelope.get("num_turns")
    if not isinstance(turns, int) or isinstance(turns, bool):
        return None, None          # cannot tell whether a tool ran: never retry
    return _extract_json(str(envelope.get("result") or "")), turns


# ── read-only market and account data ─────────────────────────────────────────

class ClaudeRobinhoodData:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, Quote]] = {}

    async def quote(self, symbol: str) -> Quote | None:
        return (await self.quotes([symbol])).get(symbol)

    async def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        wanted = sorted({s for s in symbols if _SYMBOL.match(s)})
        now = time.time()
        out = {s: self._cache[s][1] for s in wanted
               if s in self._cache and now - self._cache[s][0] < _QUOTE_TTL}
        missing = [s for s in wanted if s not in out]
        if not missing:
            return out
        reply = await run_restricted(
            "Call the equity quotes tool once for these stock symbols: "
            f"{', '.join(missing)}. Reply with only this JSON object and no other text: "
            '{"quotes": {"<SYMBOL>": {"bid": <number>, "ask": <number>, "last": <number>}}}. '
            "Copy the numbers exactly as the tool returned them. Omit any symbol the tool "
            "did not return a bid, an ask and a last trade price for. Never estimate.",
            READ_QUOTES)
        for symbol, row in ((reply or {}).get("quotes") or {}).items():
            if symbol not in missing or not isinstance(row, dict):
                continue
            bid, ask, last = _num(row.get("bid")), _num(row.get("ask")), _num(row.get("last"))
            if not (bid and ask and last and bid > 0 and ask >= bid and last > 0):
                continue
            # A last price far outside the bid-ask range is a misread, not a quote.
            if not bid * 0.8 <= last <= ask * 1.2:
                continue
            out[symbol] = Quote(bid=bid, ask=ask, last=last)
            self._cache[symbol] = (now, out[symbol])
        return out

    async def account(self) -> Account | None:
        last4 = config.TRADE_ACCOUNT_LAST4
        if not re.fullmatch(r"\d{4}", last4):
            return None
        reply = await run_restricted(
            f"Look up the brokerage account whose account number ends in {last4}. Use only "
            "that account. Reply with only this JSON object and no other text: "
            '{"account_last4": "<last 4 digits>", "equity": <total account value>, '
            '"cash": <cash available to trade>, "positions": {"<SYMBOL>": <market value in '
            'dollars>}}. Copy numbers exactly from the tools. If no account ends in those '
            'digits reply {"account_last4": null}.',
            READ_ACCOUNT)
        if not reply or str(reply.get("account_last4")) != last4:
            return None
        equity, cash = _num(reply.get("equity")), _num(reply.get("cash"))
        if equity is None or cash is None or equity < 0 or cash < 0 or cash > equity * 1.001 + 0.01:
            return None
        positions: dict[str, float] = {}
        for symbol, value in (reply.get("positions") or {}).items():
            amount = _num(value)
            if _SYMBOL.match(str(symbol)) and amount is not None and amount > 0:
                positions[str(symbol)] = amount
        if sum(positions.values()) > equity * 1.05 + 0.01:
            return None                      # the parts cannot exceed the whole
        return Account(equity=equity, cash=cash, positions=positions)


# ── the executor ──────────────────────────────────────────────────────────────

class ClaudeRobinhoodBroker:
    """Submits one approved ticket as a whole-share, regular-hours, day limit order."""

    mode = "live"

    async def submit(self, ticket: dict) -> OrderResult:
        last4 = config.TRADE_ACCOUNT_LAST4
        if not re.fullmatch(r"\d{4}", last4):
            return OrderResult(status="failed", detail="TRADE_ACCOUNT_LAST4 is not set")
        symbol, side = str(ticket["symbol"]), str(ticket["side"])
        limit, notional = _num(ticket["limit_price"]), _num(ticket["notional_usd"])
        if not _SYMBOL.match(symbol) or side not in ("buy", "sell") or not limit or not notional \
                or limit <= 0 or notional <= 0:
            return OrderResult(status="failed", detail="ticket fields failed validation")
        shares = math.floor(notional / limit)
        if shares < 1:
            return OrderResult(status="failed",
                               detail=f"${notional:.2f} does not buy one whole share at ${limit:.2f}")

        report: dict = {}
        reply = await run_restricted(
            "You are the order executor for the account owner's trading desk. The owner "
            "has approved exactly one order. Place it once, exactly as written, and nothing "
            "else.\n"
            f"Account: the brokerage account whose number ends in {last4} (no other account).\n"
            f"Order: {side.upper()} {shares} whole share(s) of the stock {symbol}, order type "
            f"LIMIT at ${limit:.2f}, time in force good for the day, regular market hours "
            "only.\n"
            "Review the order first if a review tool applies, then place it. Do not change "
            "the symbol, side, quantity, price or order type. Do not place, cancel or modify "
            "any other order. If the account is not found, a tool returns an error, or the "
            "order cannot be placed exactly as written, stop without retrying.\n"
            "Reply with only this JSON object and no other text: "
            '{"status": "submitted" or "failed", "order_id": "<id from the tool or null>", '
            '"detail": "<one short sentence>"}.',
            PLACE_ORDER, timeout=180, report=report)
        if reply is None and report.get("touched", True):
            # The run may have called the order tool and then died or answered
            # with nothing usable. Do not guess: stop and hand it to the owner.
            store.set_state("paused", "1", actor="executor")
            return OrderResult(status="failed", detail=(
                "The executor ended without a clear answer, so it is unknown whether an "
                "order was placed. Trading is paused; check the Robinhood app before "
                "resuming."))
        if not reply or reply.get("status") != "submitted" or not reply.get("order_id"):
            detail = str((reply or {}).get("detail") or "the executor did not place the order")
            return OrderResult(status="failed", detail=detail[:200])
        order_id = str(reply["order_id"])[:80]

        seen = await self.lookup(order_id)
        if seen is None or seen["symbol"] != symbol or seen["side"] != side \
                or abs(seen["quantity"] - shares) > 1e-6 or abs(seen["limit_price"] - limit) > 0.011:
            # The broker's own record disagrees with what was approved (or cannot
            # be read). Stop everything and tell the owner rather than guess.
            store.set_state("paused", "1", actor="executor")
            return OrderResult(status="submitted", order_id=order_id, detail=(
                "Order sent but it could not be confirmed against the broker's records. "
                "Trading is paused; check the Robinhood app."))
        # The executor run was allowed the order tool and only told to use it
        # once. Check the broker for any other recent order in this name: a second
        # placement (say, after an ambiguous tool error) would otherwise go unseen.
        others = await self.recent_orders(symbol, exclude=order_id)
        if others is None or others:
            store.set_state("paused", "1", actor="executor")
            found = (f"other recent {symbol} order(s) {', '.join(others)}" if others
                     else f"whether other {symbol} orders were placed")
            return OrderResult(status="submitted", order_id=order_id, detail=(
                f"Order sent, but could not rule out a duplicate ({found}). Trading is "
                "paused; check the Robinhood app."))
        if seen["state"] == "filled" and seen["filled_quantity"] > 0:
            return OrderResult(status="filled", order_id=order_id,
                               quantity=seen["filled_quantity"], price=seen["average_price"],
                               detail="filled")
        return OrderResult(status="submitted", order_id=order_id,
                           detail=f"{shares} share(s) resting at ${limit:.2f}")

    async def recent_orders(self, symbol: str, *, exclude: str,
                            minutes: int = 30) -> list[str] | None:
        """Ids of other equity orders for `symbol` created in the last `minutes`
        that were not cancelled or rejected, or None when that cannot be read."""
        last4 = config.TRADE_ACCOUNT_LAST4
        reply = await run_restricted(
            f"In the brokerage account ending in {last4}, list every equity order for the "
            f"stock {symbol} created in the last {minutes} minutes, in any state. Reply with "
            'only this JSON object and no other text: {"orders": [{"id": "<order id>", '
            '"state": "<the order state from the tool, lower case>"}]}. Use an empty list '
            "if there are none. Copy values exactly from the tool.",
            READ_ORDERS)
        rows = (reply or {}).get("orders")
        if not isinstance(rows, list):
            return None
        dead = ("cancelled", "canceled", "rejected", "failed")
        return [str(r.get("id"))[:80] for r in rows
                if isinstance(r, dict) and str(r.get("id")) != exclude
                and str(r.get("state") or "").lower() not in dead]

    async def lookup(self, order_id: str) -> dict | None:
        """Read one order back from the broker with a read-only run."""
        last4 = config.TRADE_ACCOUNT_LAST4
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", order_id):
            return None
        reply = await run_restricted(
            f"In the brokerage account ending in {last4}, find the equity order with id "
            f"{order_id}. Reply with only this JSON object and no other text: "
            '{"found": true or false, "symbol": "<SYMBOL>", "side": "buy" or "sell", '
            '"quantity": <shares ordered>, "limit_price": <number>, "state": "<the order '
            'state from the tool, lower case>", "filled_quantity": <shares filled so far>, '
            '"average_price": <average fill price or 0>}. Copy values exactly from the tool.',
            READ_ORDERS)
        if not reply or reply.get("found") is not True:
            return None
        quantity, limit = _num(reply.get("quantity")), _num(reply.get("limit_price"))
        if quantity is None or limit is None:
            return None
        return {"symbol": str(reply.get("symbol") or "").upper(),
                "side": str(reply.get("side") or "").lower(),
                "quantity": quantity, "limit_price": limit,
                "state": str(reply.get("state") or "").lower(),
                "filled_quantity": _num(reply.get("filled_quantity")) or 0.0,
                "average_price": _num(reply.get("average_price")) or 0.0}
