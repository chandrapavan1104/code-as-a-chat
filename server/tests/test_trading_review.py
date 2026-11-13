"""Review fixes for the trading desk (PR #50).

1. Research can mark a name ELIGIBLE, but only the owner's confirm makes it buyable,
   and the research run's files and broker tools are confined.
2. Live approvals need the paired phone's key, not just the shared API token.
3. TRADE_AUTO_APPROVE never applies to live orders.
4. Unattended runs load no connector; other brokerage connectors are denied whole.
5. The executor pauses if another recent order in the same name shows up.
6. Only "approve"/"yes" approves a ticket from a reply.

Fixtures come from test_trading.py: nothing reaches a broker or the network.
"""

import asyncio
import json

import pytest

from server import broker_guard, config
from server.db import notifications_store, shell_store, trading_store as store
from server.tests import test_trading as base
from server.tests.test_trading import RecordingBroker, StubCLI, buy, live_ticket, run
from server.trading import desk
from server.trading.broker import Account, OrderResult

# Reuse the desk fixtures (throwaway databases, fake quotes, no broker).
trading = base.trading
project = base.project

KEY = "k" * 43


def go_live(trading, monkeypatch, broker=None):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    trading.data.acct = Account(equity=100.0, cash=100.0, positions={})
    if broker is not None:
        monkeypatch.setattr(desk, "_live_broker", broker)


# ── 1. owner-confirmed watchlist, confined research run ───────────────────────

def test_research_eligible_alone_is_not_buyable(trading):
    store.unconfirm_watchlist("ABCD")
    refused = run(desk.propose(buy()))
    assert refused["status"] == "rejected"
    assert "have not confirmed" in refused["gate_notes"]
    store.confirm_watchlist("ABCD")
    assert run(desk.propose(buy()))["status"] == "proposed"


def test_confirm_survives_a_watchlist_refresh_and_needs_eligible(trading):
    from server.skills.trade import TradeSkill
    skill = TradeSkill()
    store.replace_watchlist([{"symbol": "ABCD", "status": "ELIGIBLE"},
                             {"symbol": "WXYZ", "status": "WATCH"}])
    assert "ABCD" in store.confirmed_watchlist()          # a refresh keeps the owner's yes
    assert "nothing to confirm" in run(skill.run("watchlist confirm WXYZ"))
    assert "UNCONFIRMED" not in run(skill.run("watchlist"))
    run(skill.run("watchlist unconfirm ABCD"))
    assert "UNCONFIRMED" in run(skill.run("watchlist"))


def test_new_eligible_names_ask_the_owner(project, monkeypatch):
    seen = []
    monkeypatch.setattr(project.runner, "_schedule_eligible_notice", seen.append)
    (project.root / "watchlist.json").write_text(json.dumps({"symbols": [
        {"symbol": "ABCD", "status": "ELIGIBLE"}, {"symbol": "NEWW", "status": "ELIGIBLE"}]}))
    project.runner._watchlist_mtime = 0.0
    project.runner.ingest_watchlist()
    assert seen == [["NEWW"]]                              # ABCD is already confirmed


def test_research_run_is_confined_to_its_project(project):
    argv = project.runner.research_argv("go")
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    for bare in ("Read", "Write", "Edit", "Glob", "Grep"):
        assert bare not in allowed                         # unscoped file access is gone
    root = str(project.root.resolve())
    assert f"Edit(/{root}/**)" in allowed
    assert config.BROKER_TOOL_PREFIX.rstrip("_") not in allowed   # no whole-server grant
    assert not any("place_" in t or "cancel_" in t for t in allowed)
    assert not any("bank" in t or "credit" in t for t in allowed)
    assert "Read(./**/.env)" in denied
    assert config.BROKER_TOOL_PREFIX + "place_equity_order" in denied


# ── 2. live approval needs the paired phone ───────────────────────────────────

def test_live_approve_needs_the_owner_device(trading, monkeypatch):
    broker = RecordingBroker(OrderResult(status="submitted", order_id="o1"))
    go_live(trading, monkeypatch, broker)
    ticket = run(desk.propose(buy()))
    with pytest.raises(desk.NeedsOwnerDevice):
        run(desk.approve(ticket["id"]))
    assert broker.seen == [] and store.get_ticket(ticket["id"])["status"] == "proposed"
    assert run(desk.approve(ticket["id"], owner_device=True))["status"] == "submitted"


def test_paper_approve_still_works_from_anywhere(trading):
    assert run(desk.approve(run(desk.propose(buy()))["id"]))["status"] == "filled"


def test_trade_skill_cannot_approve_live(trading, monkeypatch):
    from server.skills.trade import TradeSkill
    go_live(trading, monkeypatch, RecordingBroker(OrderResult(status="submitted", order_id="o")))
    ticket = run(desk.propose(buy()))
    reply = run(TradeSkill().run(f"approve {ticket['id']}"))
    assert "paired Gajala phone" in reply
    assert store.get_ticket(ticket["id"])["status"] == "proposed"


@pytest.fixture
def api(trading, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server.main import app
    monkeypatch.setattr(shell_store, "DB_PATH", tmp_path / "shell.db")
    shell_store._init()
    shell_store.bind(KEY, "Gajala phone")
    return TestClient(app)


def _h(device=None):
    h = {"X-API-Token": config.API_TOKEN}
    if device:
        h["X-Shell-Device"] = device
    return h


def test_api_live_approve_requires_the_paired_key(api, trading, monkeypatch):
    go_live(trading, monkeypatch, RecordingBroker(OrderResult(status="submitted", order_id="o")))
    ticket = run(desk.propose(buy()))
    url = f"/api/trade/tickets/{ticket['id']}/approve"
    assert api.post(url, headers=_h()).status_code == 403
    assert api.post(url, headers=_h("x" * 43)).status_code == 403
    ok = api.post(url, headers=_h(KEY))
    assert ok.status_code == 200 and ok.json()["ticket"]["status"] == "submitted"


def test_notification_reply_refuses_before_recording(api, trading, monkeypatch):
    go_live(trading, monkeypatch, RecordingBroker(OrderResult(status="submitted", order_id="o")))
    ticket = run(desk.propose(buy()))
    notif = notifications_store.add(type="trade_ticket", title="t", body="", ref_id=ticket["id"],
                                    needs_response=True)
    url = f"/api/notifications/{notif}/respond"
    assert api.post(url, headers=_h(), json={"response": "approve"}).status_code == 403
    assert not notifications_store.get(notif).get("response")    # still waiting for an answer
    assert api.post(url, headers=_h(KEY), json={"response": "approve"}).status_code == 200
    assert store.get_ticket(ticket["id"])["status"] == "submitted"


# ── 3. auto-approve is paper only ─────────────────────────────────────────────

def test_auto_approve_never_applies_live(trading, monkeypatch):
    monkeypatch.setattr(config, "TRADE_AUTO_APPROVE", True)
    assert run(desk.propose(buy()))["status"] == "filled"        # paper: allowed
    broker = RecordingBroker(OrderResult(status="submitted", order_id="o"))
    go_live(trading, monkeypatch, broker)
    assert run(desk.propose(buy(notional_usd=10)))["status"] == "proposed"
    assert broker.seen == []
    status = run(desk.status())
    assert status["auto_approve"] is False and status["auto_approve_ignored"] is True


# ── 4. connectors: strict for unattended runs, discovery for coding runs ──────

def test_night_runs_load_no_connector():
    from server import night_exec
    argv = night_exec._argv("claude", "/tmp/repo", "do it", "")
    assert "--strict-mcp-config" in argv and "--disallowedTools" in argv


def test_coding_runs_deny_other_brokers_whole(tmp_path, monkeypatch):
    from server.skills.claude_code import ClaudeCodeSkill
    from server import workspace
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "alpaca-markets": {"command": "alpaca-mcp"}, "notes": {"command": "notes-mcp"}}}))
    monkeypatch.setattr(workspace, "active", lambda: tmp_path)
    monkeypatch.setattr(broker_guard, "_discovered", {"mcp__claude_ai_Schwab"})
    argv = ClaudeCodeSkill().build_command("hi")
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert "mcp__alpaca-markets" in denied and "mcp__claude_ai_Schwab" in denied
    assert "mcp__notes" not in denied
    assert config.BROKER_TOOL_PREFIX + "place_equity_order" in denied
    assert config.BROKER_TOOL_PREFIX.rstrip("_") not in denied   # Robinhood reads stay usable


def test_discovery_reads_the_init_message_and_keeps_old_answer_on_failure(monkeypatch):
    class Proc:
        def __init__(self, line):
            self.stdout = type("S", (), {"readline": lambda s: self._line()})()
            self.line = line

        async def _line(self):
            return self.line

        def kill(self):
            pass

        async def wait(self):
            return 0

    def spawn(line):
        async def fake(*argv, **kw):
            proc = Proc(line)
            proc.stdout.readline = proc._line
            return proc
        return fake

    init = {"type": "system", "subtype": "init", "mcp_servers": [
        {"name": "claude.ai Robinhood", "status": "pending"},
        {"name": "claude.ai Webull", "status": "pending"},
        {"name": "claude.ai Canva", "status": "pending"}]}
    monkeypatch.setattr(broker_guard, "_discovered", set())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn(json.dumps(init).encode()))
    assert run(broker_guard.discover()) == {"mcp__claude_ai_Webull"}
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn(b"not json"))
    assert run(broker_guard.discover()) == {"mcp__claude_ai_Webull"}   # never shrinks on a bad probe


# ── 5. executor: a second recent order pauses trading ─────────────────────────

FILLED = {"found": True, "symbol": "ABCD", "side": "buy", "quantity": 2, "limit_price": 9.5,
          "state": "filled", "filled_quantity": 2, "average_price": 9.48}


@pytest.mark.parametrize("orders_reply, needle", [
    ({"orders": [{"id": "ord-9", "state": "filled"}, {"id": "ord-10", "state": "queued"}]},
     "ord-10"),
    (None, "whether other"),
])
def test_executor_pauses_on_a_possible_duplicate(trading, monkeypatch, orders_reply, needle):
    from server.trading import robinhood_cli as rh
    monkeypatch.setattr(config, "TRADE_ACCOUNT_LAST4", "1234")
    stub = StubCLI({"status": "submitted", "order_id": "ord-9", "detail": "placed"}, FILLED,
                   orders_reply)
    monkeypatch.setattr(rh, "run_restricted", stub)
    result = run(rh.ClaudeRobinhoodBroker().submit(live_ticket()))
    assert result.status == "submitted" and needle in result.detail
    assert desk.is_paused()


def test_cancelled_siblings_do_not_pause(trading, monkeypatch):
    from server.trading import robinhood_cli as rh
    monkeypatch.setattr(config, "TRADE_ACCOUNT_LAST4", "1234")
    stub = StubCLI({"status": "submitted", "order_id": "ord-9", "detail": "placed"}, FILLED,
                   {"orders": [{"id": "ord-9", "state": "filled"},
                               {"id": "ord-1", "state": "cancelled"}]})
    monkeypatch.setattr(rh, "run_restricted", stub)
    assert run(rh.ClaudeRobinhoodBroker().submit(live_ticket())).status == "filled"
    assert not desk.is_paused()


# ── 6. only an explicit yes approves ──────────────────────────────────────────

@pytest.mark.parametrize("word", ["ok", "go", "y", "OK!", "sure"])
def test_casual_replies_never_approve(trading, word):
    ticket = run(desk.propose(buy()))
    with pytest.raises(ValueError):
        run(desk.respond(ticket["id"], word))
    assert store.get_ticket(ticket["id"])["status"] == "proposed"


def test_yes_approves(trading):
    ticket = run(desk.propose(buy()))
    assert run(desk.respond(ticket["id"], "Yes."))["status"] == "filled"
