"""Where an approved ticket goes, and where account facts come from.

Two small interfaces keep the desk independent of any particular broker:

  Broker      submits one approved ticket and reports what happened.
  MarketData  answers "what is this stock quoted at" and, for a live account,
              "what does the account hold".

`PaperBroker` fills instantly at the ticket's limit price and touches nothing
outside the ledger. A live broker and a live data source are registered by
`server.trading.desk.configure()`; until one is, live mode refuses to submit and
quotes are unavailable, which the gate treats as a violation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from server.trading.risk import Quote


@dataclass(frozen=True)
class OrderResult:
    status: str                    # "filled" | "submitted" | "failed"
    order_id: str | None = None
    quantity: float = 0.0
    price: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class Account:
    """A live account as the broker reports it."""
    equity: float
    cash: float
    positions: dict[str, float]    # symbol -> market value in dollars


class Broker(Protocol):
    mode: str

    async def submit(self, ticket: dict) -> OrderResult: ...


class MarketData(Protocol):
    async def quote(self, symbol: str) -> Quote | None: ...

    async def quotes(self, symbols: list[str]) -> dict[str, Quote]: ...

    async def account(self) -> Account | None: ...


class PaperBroker:
    """Simulated fills: the whole order at the limit price, immediately.

    That is kinder than a real thin market, where a limit order may sit
    unfilled or fill in part, so paper results are an upper bound on how well
    execution goes, not a forecast.
    """

    mode = "paper"

    async def submit(self, ticket: dict) -> OrderResult:
        price = float(ticket["limit_price"])
        quantity = round(float(ticket["notional_usd"]) / price, 6)
        return OrderResult(status="filled", order_id=f"paper-{ticket['id']}",
                           quantity=quantity, price=price, detail="simulated fill at limit")


class UnconfiguredBroker:
    """Stands in for live mode until a real broker is registered."""

    mode = "live"

    async def submit(self, ticket: dict) -> OrderResult:
        return OrderResult(status="failed", detail="no live broker is configured")


class NoMarketData:
    """Stands in until a quote source is registered. Everything is unknown."""

    async def quote(self, symbol: str) -> Quote | None:
        return None

    async def quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return {}

    async def account(self) -> Account | None:
        return None
