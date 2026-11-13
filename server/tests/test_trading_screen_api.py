"""Endpoints behind the Gajala Trading screen: overview, watchlist confirm,
pause/resume, research documents. Fixtures come from test_trading.py, so
nothing reaches a broker, Telegram or FCM."""

import pytest

from server import config
from server.db import shell_store, trading_store as store
from server.tests import test_trading as base
from server.tests.test_trading import buy, run
from server.trading import desk

trading = base.trading
KEY = "k" * 43


@pytest.fixture
def api(trading, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import trading_api
    from server.main import app
    monkeypatch.setattr(shell_store, "DB_PATH", tmp_path / "shell.db")
    shell_store._init()
    monkeypatch.setattr(trading_api, "_account_cache", {})
    project = tmp_path / "finance-agent"
    (project / "memos").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "memos" / "ABCD.md").write_text("# ABCD\nCheap and unloved.")
    (project / "reports" / "2026-11-12-weekly-screen.md").write_text("# Weekly screen")
    (tmp_path / "secret.md").write_text("do not show")
    monkeypatch.setattr(config, "FINANCE_PROJECT_DIR", project)
    return TestClient(app)


def h(device=None):
    out = {"X-API-Token": config.API_TOKEN}
    if device:
        out["X-Shell-Device"] = device
    return out


def test_overview_has_everything_the_screen_needs(api):
    ticket = run(desk.propose(buy()))
    run(desk.propose(buy(symbol="ZZZZ")))                    # refused by the gate
    body = api.get("/api/trade/overview", headers=h()).json()
    assert body["enabled"] is True and body["mode"] == "paper" and body["paused"] is False
    assert [t["id"] for t in body["open_tickets"]] == [ticket["id"]]
    assert body["recent_tickets"][0]["status"] == "rejected"
    assert body["recent_tickets"][0]["gate_notes"]
    assert body["watchlist"] == [{"symbol": "ABCD", "status": "ELIGIBLE",
                                  "memo": "memos/abcd.md", "confirmed": True}]
    assert body["account"]["cash"] == pytest.approx(100.0)
    assert body["limits"]["max_order_usd"] == 25.0 and body["limits"]["max_positions"] == 5
    assert body["reports"][0]["path"] == "reports/2026-11-12-weekly-screen.md"
    assert body["reports"][0]["title"] == "Weekly screen · 2026-11-12"
    assert body["phone"] == {"paired": False, "this_device": False}


def test_overview_reports_whether_this_phone_is_paired(api):
    shell_store.bind(KEY, "Gajala phone")
    assert api.get("/api/trade/overview", headers=h(KEY)).json()["phone"] == {
        "paired": True, "this_device": True}
    assert api.get("/api/trade/overview", headers=h("x" * 43)).json()["phone"][
        "this_device"] is False


def test_overview_needs_the_token(api):
    assert api.get("/api/trade/overview").status_code == 401


def test_overview_works_while_the_desk_is_off(api, monkeypatch):
    monkeypatch.setattr(config, "TRADING_ENABLED", False)
    body = api.get("/api/trade/overview", headers=h()).json()
    assert body["enabled"] is False and body["watchlist"]       # research stays readable


def test_desk_off_shows_the_research_watchlist_file(api, monkeypatch):
    import json
    monkeypatch.setattr(config, "TRADING_ENABLED", False)
    store.replace_watchlist([])                                  # nothing synced yet
    (config.FINANCE_PROJECT_DIR / "watchlist.json").write_text(json.dumps({"symbols": [
        {"symbol": "elmd", "status": "WATCH", "memo": "memos/ELMD.md"},
        {"symbol": "bad ticker!", "status": "ELIGIBLE"}]}))
    body = api.get("/api/trade/overview", headers=h()).json()
    assert body["watchlist"] == [{"symbol": "ELMD", "status": "WATCH",
                                  "memo": "memos/ELMD.md", "confirmed": False}]


def test_account_reads_are_cached_and_a_slow_read_keeps_the_last_value(api, monkeypatch):
    from server import trading_api
    calls = []
    real = desk.status

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(desk, "status", counted)
    api.get("/api/trade/overview", headers=h())
    api.get("/api/trade/overview", headers=h())
    assert len(calls) == 1                                   # second one is cached
    api.get("/api/trade/overview?refresh=true", headers=h())
    assert len(calls) == 2

    import asyncio

    async def slow():
        await asyncio.sleep(1)

    monkeypatch.setattr(desk, "status", slow)
    monkeypatch.setattr(trading_api, "ACCOUNT_TIMEOUT", 0.01)
    body = api.get("/api/trade/overview?refresh=true", headers=h()).json()
    assert body["account"]["cash"] == pytest.approx(100.0) and body["account_error"]


def test_confirm_needs_research_eligibility_and_can_be_undone(api):
    store.replace_watchlist([{"symbol": "ABCD", "status": "ELIGIBLE"},
                             {"symbol": "WXYZ", "status": "WATCH"}])
    assert api.post("/api/trade/watchlist/WXYZ/confirm", headers=h()).status_code == 409
    assert api.delete("/api/trade/watchlist/abcd/confirm", headers=h()).json()["confirmed"] is False
    assert "ABCD" not in store.confirmed_watchlist()
    assert api.post("/api/trade/watchlist/abcd/confirm", headers=h()).json()["confirmed"] is True
    assert "ABCD" in store.confirmed_watchlist()


def test_pause_and_resume(api):
    assert api.post("/api/trade/pause", headers=h()).json() == {"paused": True}
    assert desk.is_paused()
    assert api.post("/api/trade/resume", headers=h()).json() == {"paused": False}
    assert not desk.is_paused()


def test_research_documents_are_read_only_and_confined(api):
    ok = api.get("/api/trade/doc", params={"path": "memos/ABCD.md"}, headers=h())
    assert ok.status_code == 200 and "Cheap and unloved" in ok.json()["text"]
    for bad in ("../secret.md", "memos/../../secret.md", "/etc/passwd", "memos/ABCD.txt",
                "outbox/tickets.json", "memos/.env"):
        assert api.get("/api/trade/doc", params={"path": bad}, headers=h()).status_code in (400, 404)
    assert api.get("/api/trade/doc", params={"path": "memos/NOPE.md"},
                   headers=h()).status_code == 404
