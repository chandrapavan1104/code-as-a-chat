"""The gate: every money rule, as plain code.

Nothing here calls a model, the network, or the database. `check()` takes a
proposal, a snapshot of the account, and the limits, and returns the list of
rules the proposal breaks. An empty list is the only way an order goes further.

The gate deliberately fails closed: a missing quote, an unknown market mood, or
a number that is not a finite positive value is a violation, never a pass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_SYMBOL = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_SELL_TOLERANCE = 1.05   # a sell may exceed the marked position value by 5% (price drift)


@dataclass(frozen=True)
class Limits:
    max_order_usd: float = 25.0
    max_daily_usd: float = 50.0
    max_positions: int = 5
    max_position_pct: float = 0.25      # of account equity, per symbol
    cash_floor_pct: float = 0.20        # of account equity kept in cash
    max_limit_deviation_pct: float = 0.03
    max_spread_pct: float = 0.02
    min_price: float = 2.0
    block_buys_drawdown: float = 0.15   # from peak equity
    halt_drawdown: float = 0.25
    mood_ceiling: dict[str, float] = field(default_factory=lambda: {
        "risk_on": 0.80, "neutral": 0.50, "risk_off": 0.20})


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    last: float


@dataclass(frozen=True)
class Proposal:
    symbol: str
    side: str                 # "buy" | "sell"
    notional_usd: float
    limit_price: float
    thesis_ref: str = ""
    asset_class: str = "stock"


@dataclass(frozen=True)
class Snapshot:
    equity: float                         # cash + marked positions
    cash: float
    positions: dict[str, float]           # symbol -> market value in dollars
    peak_equity: float
    committed_today: float                # buy dollars already committed today
    mood: str | None                      # risk_on | neutral | risk_off | None
    paused: bool
    watchlist: frozenset[str]             # names marked ELIGIBLE: the only buyable ones
    quote: Quote | None
    watching: frozenset[str] = frozenset()   # followed but not buy-eligible
    unconfirmed: frozenset[str] = frozenset()  # research says ELIGIBLE, owner has not confirmed


def _positive(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def drawdown(snapshot: Snapshot) -> float:
    if not _positive(snapshot.peak_equity):
        return 0.0
    return max(0.0, 1.0 - snapshot.equity / snapshot.peak_equity)


def check(p: Proposal, s: Snapshot, limits: Limits) -> list[str]:
    """Return every rule this proposal breaks. Empty list = allowed."""
    v: list[str] = []

    # ── shape: reject anything that is not a plain stock limit order ─────────
    if not _SYMBOL.match(p.symbol or ""):
        return [f"'{p.symbol}' is not a plain stock ticker"]
    if p.side not in ("buy", "sell"):
        return [f"side must be buy or sell, got '{p.side}'"]
    if p.asset_class != "stock":
        v.append(f"only stocks are allowed, got {p.asset_class}")
    if not _positive(p.notional_usd):
        v.append("order amount must be a positive number")
    if not _positive(p.limit_price):
        v.append("limit price must be a positive number")
    if v:
        return v

    # ── account-wide brakes ──────────────────────────────────────────────────
    if s.paused:
        v.append("trading is paused")
    dd = drawdown(s)
    if dd >= limits.halt_drawdown:
        v.append(f"account is down {dd:.0%} from its peak; everything is halted "
                 f"at {limits.halt_drawdown:.0%}")
    if not _positive(s.equity):
        v.append("account value is unknown or zero")

    # ── quote sanity (both sides) ────────────────────────────────────────────
    q = s.quote
    if q is None or not (_positive(q.bid) and _positive(q.ask) and _positive(q.last)):
        v.append("no live quote for this symbol")
        q = None
    elif q.ask < q.bid:
        v.append("quote is crossed (ask below bid)")
        q = None
    else:
        mid = (q.bid + q.ask) / 2
        spread = (q.ask - q.bid) / mid
        if spread > limits.max_spread_pct:
            v.append(f"bid-ask spread {spread:.1%} is wider than "
                     f"{limits.max_spread_pct:.1%}")

    if p.side == "buy":
        v += _check_buy(p, s, limits, q, dd)
    else:
        v += _check_sell(p, s, limits, q)
    return v


def _check_buy(p: Proposal, s: Snapshot, limits: Limits, q: Quote | None,
               dd: float) -> list[str]:
    v: list[str] = []
    if p.symbol in s.unconfirmed and p.symbol not in s.watchlist:
        v.append(f"{p.symbol} is marked eligible by research but you have not confirmed it "
                 f"(/trade watchlist confirm {p.symbol})")
    elif p.symbol in s.watching and p.symbol not in s.watchlist:
        v.append(f"{p.symbol} is on the watchlist but not marked buy-eligible "
                 "(its trust checks have not all passed)")
    elif p.symbol not in s.watchlist:
        v.append(f"{p.symbol} is not on the vetted watchlist")
    if not (p.thesis_ref or "").strip():
        v.append("no thesis memo is attached")
    if limits.block_buys_drawdown <= dd < limits.halt_drawdown:
        v.append(f"account is down {dd:.0%} from its peak; buys are blocked "
                 f"at {limits.block_buys_drawdown:.0%}")
    if p.notional_usd > limits.max_order_usd:
        v.append(f"${p.notional_usd:.2f} is over the ${limits.max_order_usd:.2f} per-order cap")
    if s.committed_today + p.notional_usd > limits.max_daily_usd:
        v.append(f"would bring today's buys to ${s.committed_today + p.notional_usd:.2f}, "
                 f"over the ${limits.max_daily_usd:.2f} daily cap")
    if p.notional_usd > s.cash:
        v.append(f"only ${s.cash:.2f} cash is available")

    if q is not None:
        if q.last < limits.min_price:
            v.append(f"price ${q.last:.2f} is under the ${limits.min_price:.2f} minimum")
        ceiling = q.ask * (1 + limits.max_limit_deviation_pct)
        if p.limit_price > ceiling:
            v.append(f"limit ${p.limit_price:.2f} is more than "
                     f"{limits.max_limit_deviation_pct:.0%} above the ask ${q.ask:.2f}")

    if _positive(s.equity):
        held = s.positions.get(p.symbol, 0.0)
        if held <= 0 and len([x for x in s.positions.values() if x > 0]) >= limits.max_positions:
            v.append(f"already holding {limits.max_positions} positions")
        if held + p.notional_usd > limits.max_position_pct * s.equity + 1e-9:
            v.append(f"{p.symbol} would be {(held + p.notional_usd) / s.equity:.0%} of the "
                     f"account, over the {limits.max_position_pct:.0%} cap")
        if s.cash - p.notional_usd < limits.cash_floor_pct * s.equity - 1e-9:
            v.append(f"would leave cash under the {limits.cash_floor_pct:.0%} floor")
        # An unknown or unrecognised mood is treated as the most cautious one.
        ceiling_pct = limits.mood_ceiling.get(s.mood or "", min(limits.mood_ceiling.values()))
        invested = sum(x for x in s.positions.values() if x > 0)
        if invested + p.notional_usd > ceiling_pct * s.equity + 1e-9:
            mood = s.mood if s.mood in limits.mood_ceiling else "unknown (treated as risk_off)"
            v.append(f"would put {(invested + p.notional_usd) / s.equity:.0%} of the account "
                     f"in stocks; the ceiling for market mood {mood} is {ceiling_pct:.0%}")
    return v


def _check_sell(p: Proposal, s: Snapshot, limits: Limits, q: Quote | None) -> list[str]:
    v: list[str] = []
    held = s.positions.get(p.symbol, 0.0)
    if held <= 0:
        v.append(f"no {p.symbol} position to sell (short selling is not allowed)")
    elif p.notional_usd > held * _SELL_TOLERANCE:
        v.append(f"${p.notional_usd:.2f} is more than the ${held:.2f} position")
    if q is not None:
        floor = q.bid * (1 - limits.max_limit_deviation_pct)
        if p.limit_price < floor:
            v.append(f"limit ${p.limit_price:.2f} is more than "
                     f"{limits.max_limit_deviation_pct:.0%} below the bid ${q.bid:.2f}")
    return v
