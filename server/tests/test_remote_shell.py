"""Gajala Terminal: pairing, device gating, live sessions and the audit log."""

import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from server import config, notifier, remote_shell, shell_api
from server.db import shell_store

KEY = "k" * 40
OTHER = "o" * 40


@pytest.fixture
def shell(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_store, "DB_PATH", tmp_path / "shell.db")
    shell_store._init()
    monkeypatch.setattr(config, "REMOTE_SHELL_ENABLED", True)
    pushes = []

    async def fake_notify(*args, **kwargs):
        pushes.append(args)
        return 1

    monkeypatch.setattr(notifier, "notify_app", fake_notify)
    manager = remote_shell.ShellManager()
    monkeypatch.setattr(shell_api, "manager", manager)
    from server.main import require_token
    app = FastAPI()
    app.include_router(shell_api.router, prefix="/api", dependencies=[Depends(require_token)])
    with TestClient(app) as client:
        client.pushes = pushes
        yield client
    manager.shutdown()


def _h(key=KEY):
    return {"X-API-Token": config.API_TOKEN, "X-Shell-Device": key}


def _run(client, sid, command):
    """Send [command] then `exit`, and collect the whole streamed transcript."""
    assert client.post(f"/api/shell/sessions/{sid}/input", headers=_h(),
                       json={"data": f"{command}\nexit\n"}).status_code == 200
    out, frames = "", []
    with client.stream("GET", f"/api/shell/sessions/{sid}/output?offset=0", headers=_h()) as r:
        for line in r.iter_lines():
            frame = json.loads(line)
            frames.append(frame)
            if frame["type"] == "output":
                out += frame["data"]
    return out, frames


def test_needs_the_api_token(shell):
    assert shell.get("/api/shell/status").status_code == 401
    assert shell.post("/api/shell/sessions", json={},
                      headers={"X-Shell-Device": KEY}).status_code == 401


def test_first_device_pairs_and_others_are_refused(shell):
    assert shell.post("/api/shell/sessions", headers=_h(), json={}).status_code == 403
    assert shell.post("/api/shell/pair", headers=_h(), json={"label": "Pixel"}).status_code == 200
    assert shell.pushes and shell.pushes[0][0] == "shell_paired"
    assert shell.post("/api/shell/pair", headers=_h(OTHER), json={}).status_code == 409
    refused = shell.post("/api/shell/sessions", headers=_h(OTHER), json={})
    assert refused.status_code == 403 and "Pixel" in refused.json()["detail"]
    assert shell.get("/api/shell/status", headers=_h()).json()["this_device"] is True
    assert shell.get("/api/shell/status", headers=_h(OTHER)).json()["this_device"] is False
    assert shell.post("/api/shell/pair", headers=_h("short"), json={}).status_code == 403


def test_kill_switch(shell, monkeypatch):
    shell.post("/api/shell/pair", headers=_h(), json={})
    monkeypatch.setattr(config, "REMOTE_SHELL_ENABLED", False)
    assert shell.post("/api/shell/sessions", headers=_h(), json={}).status_code == 403


def test_session_runs_commands_keeps_cwd_and_ends(shell, tmp_path):
    shell.post("/api/shell/pair", headers=_h(), json={})
    (tmp_path / "proj").mkdir()
    created = shell.post("/api/shell/sessions", headers=_h(), json={"project": str(tmp_path)})
    assert created.status_code == 200, created.text
    sid = created.json()["id"]
    out, frames = _run(shell, sid, "cd proj && pwd && echo ok-$((40+2))")
    assert f"{tmp_path}/proj".replace("/private", "") in out.replace("/private", "")
    assert "ok-42" in out
    assert frames[0]["type"] == "hello" and frames[-1]["type"] == "exit"
    # Resuming past the end yields nothing new, just the exit.
    end = frames[-1]["next"]
    with shell.stream("GET", f"/api/shell/sessions/{sid}/output?offset={end}", headers=_h()) as r:
        tail = [json.loads(line)["type"] for line in r.iter_lines()]
    assert tail == ["hello", "exit"]
    audit = [(row["kind"], row["text"]) for row in shell_store.recent_audit()]
    assert ("input", "cd proj && pwd && echo ok-$((40+2))\nexit") in audit


def test_hidden_input_is_not_audited(shell, tmp_path):
    shell.post("/api/shell/pair", headers=_h(), json={})
    sid = shell.post("/api/shell/sessions", headers=_h(),
                     json={"project": str(tmp_path)}).json()["id"]
    import time
    shell.post(f"/api/shell/sessions/{sid}/input", headers=_h(), json={"data": "read -s secret\n"})
    time.sleep(1.0)
    shell.post(f"/api/shell/sessions/{sid}/input", headers=_h(), json={"data": "hunter2\n"})
    texts = [row["text"] for row in shell_store.recent_audit()]
    assert "[hidden input]" in texts
    assert not any("hunter2" in (t or "") for t in texts)


def test_session_limit_and_unpair_ends_sessions(shell, tmp_path):
    shell.post("/api/shell/pair", headers=_h(), json={})
    for _ in range(remote_shell.MAX_SESSIONS):
        assert shell.post("/api/shell/sessions", headers=_h(),
                          json={"project": str(tmp_path)}).status_code == 200
    assert shell.post("/api/shell/sessions", headers=_h(),
                      json={"project": str(tmp_path)}).status_code == 429
    assert shell.delete("/api/shell/pair", headers=_h()).status_code == 200
    assert shell_api.manager.sessions == {}
    assert shell.get("/api/shell/sessions", headers=_h()).status_code == 403


def test_utf8_tail_is_held_back():
    euro = "€".encode()
    assert remote_shell._utf8_safe_end(b"ab" + euro[:2]) == 2
    assert remote_shell._utf8_safe_end(b"ab" + euro) == 5
    assert remote_shell._utf8_safe_end(b"plain") == 5


def test_routes_are_mounted_on_the_real_app():
    from server.main import app
    assert TestClient(app).get("/api/shell/status").status_code == 401
