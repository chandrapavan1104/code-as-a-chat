"""Token and cost tally for one agent turn, shown under the reply in Gajala.

A ContextVar so concurrent turns (phone, Telegram, Night Shift) never mix
counts. Every model call made while the turn runs — the routing brain, nested
reviews, and the coding CLIs — adds an entry; outside a turn it is a no-op.
Cost is only reported where the provider states it (the Claude CLI does);
other calls count tokens, and the summary says when cost is incomplete.
"""

from contextvars import ContextVar

_calls: ContextVar[list[dict] | None] = ContextVar("run_usage", default=None)


def start() -> None:
    _calls.set([])


def add(source: str, model: str | None, *, input_tokens: int = 0,
        output_tokens: int | None = None, cost_usd: float | None = None) -> None:
    calls = _calls.get()
    if calls is None:
        return
    calls.append({"source": source, "model": model or "default",
                  "input_tokens": int(input_tokens or 0),
                  "output_tokens": None if output_tokens is None else int(output_tokens),
                  "cost_usd": None if cost_usd is None else round(float(cost_usd), 6)})


def summary() -> dict | None:
    calls = _calls.get()
    if not calls:
        return None
    known = [c["cost_usd"] for c in calls if c["cost_usd"] is not None]
    return {
        "calls": calls,
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
        "cost_usd": round(sum(known), 6) if known else None,
        "cost_complete": len(known) == len(calls),
    }
