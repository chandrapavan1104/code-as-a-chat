"""Trading runner — the background half of the desk.

Started from main.py's lifespan when TRADING_ENABLED is on. Each tick it:
  1. expires tickets nobody answered,
  2. ingests what the research project left for it (watchlist, mood, proposals),
  3. checks resting live orders for fills,
  4. starts any research routine that is due (only if FINANCE_ROUTINES_ENABLED).

The research project is a separate folder (FINANCE_PROJECT_DIR). It can only
influence the desk through three small JSON files, each parsed strictly here:

  watchlist.json        {"symbols": [{"symbol": "ABCD", "memo": "memos/abcd.md",
                                      "status": "WATCH" | "ELIGIBLE"}]}
                        Only ELIGIBLE names can be bought; a missing status is WATCH.
  outbox/mood.json      {"mood": "risk_on" | "neutral" | "risk_off"}
  outbox/tickets.json   {"routine": "...", "tickets": [{symbol, side, notional_usd,
                         limit_price, thesis_ref, reason, max_loss_pct}]}

A proposals file is moved to outbox/processed/ once read, so it is never
ingested twice. Research runs are started with read-only tools: every order
tool is denied, and so is the shell.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from pathlib import Path

from server import config
from server.db import trading_store as store
from server.trading import desk

log = logging.getLogger("trading.runner")

MAX_FILE_BYTES = 64 * 1024
MAX_TICKETS_PER_FILE = 10

# name -> (weekdays it runs on, config attribute holding HH:MM). Monday is 0.
ROUTINES: dict[str, tuple[frozenset[int], str]] = {
    "daily-check": (frozenset(range(5)), "FINANCE_DAILY_CHECK_AT"),
    "weekly-screen": (frozenset({5}), "FINANCE_WEEKLY_SCREEN_AT"),
    "weekly-report": (frozenset({6}), "FINANCE_WEEKLY_REPORT_AT"),
}
# ToolSearch is the loader for connector tools; without it a run has none.
RESEARCH_BUILTINS = ("Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
                     "ToolSearch")
# The research run reads the open web and social posts (x-signals), so it is
# treated as steerable by whatever it reads. Its file access is limited to the
# finance project (an Edit rule covers Write and Edit; reads outside the working
# directory are refused unless allowed), and env files there are off limits.
RESEARCH_DENY_FILES = ("Read(./**/.env)", "Read(./**/.env.*)", "Read(./**/*.pem)",
                       "Read(./**/*.key)")
# Brokerage tools it may use, as an allowlist: a tool the connector adds later
# stays unavailable until it is listed here. Market data and the owner's own
# holdings only: no banking or card data, which a web-reading run could leak.
RESEARCH_BROKER_TOOLS = (
    "search", "get_accounts", "get_portfolio", "get_equity_positions", "get_equity_orders",
    "get_equity_quotes", "get_equity_historicals", "get_equity_fundamentals",
    "get_equity_analyst_ratings", "get_equity_price_book", "get_equity_technical_indicators",
    "get_equity_tradability", "get_financials", "get_earnings_calendar", "get_earnings_results",
    "get_index_quotes", "get_index_historicals", "get_indexes", "get_sec_filing",
    "get_sec_filing_index", "get_sec_filing_facts", "get_sec_filing_facts_catalog",
    "get_politician_trades", "get_popular_watchlists", "get_watchlists", "get_watchlist_items",
    "get_scans", "get_scanner_datapoints", "get_scanner_filter_specs", "preview_scan",
    "run_scan", "get_option_chains", "get_option_quotes", "get_option_instruments",
    "get_option_historicals", "get_realized_pnl", "get_pnl_trade_history",
)
# The finance project's own MCP server (see its .mcp.json).
RESEARCH_PROJECT_MCP = ("mcp__x-signals",)

_running: dict[str, asyncio.Task] = {}
_watchlist_mtime: float = 0.0


def _project() -> Path:
    return Path(config.FINANCE_PROJECT_DIR)


def _read_json(path: Path) -> dict | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ── ingest ────────────────────────────────────────────────────────────────────

def ingest_watchlist() -> int | None:
    """Sync the vetted watchlist when the file changed. Returns the new size."""
    global _watchlist_mtime
    path = _project() / "watchlist.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if mtime == _watchlist_mtime:
        return None
    data = _read_json(path)
    if data is None or not isinstance(data.get("symbols"), list):
        return None
    _watchlist_mtime = mtime
    before = {s for s, e in store.watchlist().items() if e["status"] == "ELIGIBLE"}
    size = desk.sync_watchlist(data["symbols"])
    fresh = sorted({s for s, e in store.watchlist().items() if e["status"] == "ELIGIBLE"}
                   - before - store.confirmed_watchlist())
    if fresh:
        _schedule_eligible_notice(fresh)
    return size


def _schedule_eligible_notice(symbols: list[str]) -> None:
    """Tell the owner research wants to make names buyable; they confirm them."""
    from server.notifier import notify_app
    body = ("Research marked these buy-eligible. Nothing can be bought until you confirm: "
            + ", ".join(f"/trade watchlist confirm {s}" for s in symbols))
    try:
        asyncio.get_running_loop().create_task(notify_app(
            "trade_status", title=f"Watchlist: {', '.join(symbols)} awaiting your OK",
            body=body, telegram=True))
    except RuntimeError:        # no loop (a direct call in a script or test)
        log.info("eligible names awaiting confirmation: %s", symbols)


def ingest_mood() -> str | None:
    data = _read_json(_project() / "outbox" / "mood.json")
    mood = str((data or {}).get("mood") or "")
    if mood in desk.MOODS and mood != store.get_state("mood"):
        desk.set_mood(mood, actor="researcher")
        return mood
    return None


async def ingest_tickets() -> list[dict]:
    """Turn a proposals file into tickets, then move the file aside."""
    path = _project() / "outbox" / "tickets.json"
    if not path.is_file():
        return []
    data = _read_json(path)
    processed = path.parent / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    # Move first: a file that crashes the ingest must not be retried forever.
    path.rename(processed / f"tickets-{time.strftime('%Y%m%d-%H%M%S')}.json")
    if data is None or not isinstance(data.get("tickets"), list):
        log.warning("outbox/tickets.json was not a valid proposals file; ignored")
        return []
    source = str(data.get("routine") or "researcher")[:40]
    tickets: list[dict] = []
    for raw in data["tickets"][:MAX_TICKETS_PER_FILE]:
        try:
            tickets.append(await desk.propose(raw, source=source))
        except desk.DeskDisabled:
            raise
        except ValueError as exc:
            log.warning("skipped a malformed proposal: %s", exc)
    return tickets


async def reconcile() -> list[dict]:
    """Book fills for live orders that were resting when they were sent."""
    broker = desk._live_broker
    lookup = getattr(broker, "lookup", None)
    if desk.mode() != "live" or lookup is None:
        return []
    done: list[dict] = []
    for ticket in store.list_tickets("live", ("submitted",)):
        if not ticket["broker_order_id"]:
            continue
        seen = await lookup(ticket["broker_order_id"])
        if seen is None:
            continue
        if seen["state"] == "filled" and seen["filled_quantity"] > 0:
            done.append(await desk.record_fill(ticket["id"], quantity=seen["filled_quantity"],
                                               price=seen["average_price"]))
        elif seen["state"] in ("cancelled", "canceled", "rejected", "failed", "expired"):
            done.append(store.transition(ticket["id"], "failed", actor="broker",
                                         detail={"state": seen["state"]}))
    return done


# ── research routines ─────────────────────────────────────────────────────────

def research_argv(prompt: str) -> list[str]:
    """Command line for a read-only research run in the finance project."""
    project = _project().resolve()
    # `//` makes the rule an absolute path. Read, Glob and Grep inside the working
    # directory need no rule; outside it they are refused in a headless run.
    allowed = [f"Edit(/{project}/**)", "WebSearch", "WebFetch", "ToolSearch",
               *(config.BROKER_TOOL_PREFIX + t for t in RESEARCH_BROKER_TOOLS),
               *RESEARCH_PROJECT_MCP]
    denied = [*(config.BROKER_TOOL_PREFIX + t for t in config.BROKER_ORDER_TOOLS),
              *RESEARCH_DENY_FILES]
    return ["claude", "-p", prompt, "--output-format", "json",
            "--model", config.FINANCE_RESEARCH_MODEL,
            "--tools", ",".join(RESEARCH_BUILTINS),      # no Bash, no agents
            "--allowedTools", ",".join(allowed),
            "--disallowedTools", ",".join(denied)]


def due(now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now()
    today = now.strftime("%Y-%m-%d")
    out = []
    for name, (weekdays, attr) in ROUTINES.items():
        if now.weekday() not in weekdays or name in _running:
            continue
        try:
            hour, minute = (int(x) for x in str(getattr(config, attr)).split(":"))
        except ValueError:
            continue
        if (now.hour, now.minute) < (hour, minute):
            continue
        if store.get_state(f"routine:{name}:last_run") == today:
            continue
        out.append(name)
    return out


async def run_routine(name: str) -> bool:
    """Run one routine prompt from the finance project. Returns True on a clean exit."""
    prompt_file = _project() / "routines" / f"{name}.md"
    if name not in ROUTINES or not prompt_file.is_file():
        log.warning("routine %s has no prompt file at %s", name, prompt_file)
        return False
    store.set_state(f"routine:{name}:last_run", dt.datetime.now().strftime("%Y-%m-%d"),
                    actor="runner")
    prompt = (f"Run the routine described in routines/{name}.md in this project. "
              "Follow CLAUDE.md and RULEBOOK.md. You have read-only market tools; "
              "you cannot place orders. Write proposals only to outbox/tickets.json.")
    try:
        proc = await asyncio.create_subprocess_exec(
            *research_argv(prompt), cwd=str(_project()), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=config.FINANCE_ROUTINE_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as exc:
        log.warning("routine %s failed: %r", name, exc)
        return False
    return proc.returncode == 0


def _start_due() -> None:
    for name in due():
        task = asyncio.create_task(run_routine(name))
        _running[name] = task
        task.add_done_callback(lambda _t, n=name: _running.pop(n, None))


# ── loop ──────────────────────────────────────────────────────────────────────

async def tick() -> None:
    desk.expire_stale()
    ingest_watchlist()
    ingest_mood()
    await ingest_tickets()
    await reconcile()
    if config.FINANCE_ROUTINES_ENABLED:
        _start_due()


def install_live_adapters() -> None:
    """Register the Robinhood quote source and, in live mode, the executor."""
    from server.trading.robinhood_cli import ClaudeRobinhoodBroker, ClaudeRobinhoodData
    desk.configure(data=ClaudeRobinhoodData(),
                   live_broker=ClaudeRobinhoodBroker() if desk.mode() == "live" else None)


async def trading_loop() -> None:
    if not config.TRADING_ENABLED:
        log.info("Trading desk is off")
        return
    install_live_adapters()
    log.info("Trading runner started (mode=%s, tick=%ss, routines=%s)", desk.mode(),
             config.TRADE_TICK, "on" if config.FINANCE_ROUTINES_ENABLED else "off")
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:        # one bad tick must not stop the desk
            log.error("trading tick error: %s", exc)
        await asyncio.sleep(config.TRADE_TICK)
