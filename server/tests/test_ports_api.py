import asyncio

import pytest
from fastapi import HTTPException

from server import ports_api


def test_list_and_detail_return_process_information(monkeypatch):
    async def rows():
        return [{"port": 8080, "pid": 4321, "command": "python", "user": "me",
                 "address": "127.0.0.1:8080"}]

    class Process:
        def __init__(self, pid):
            assert pid == 4321

        def cmdline(self): return ["python", "server.py"]
        def create_time(self): return 1234.5
        def uids(self): return type("Uids", (), {"real": 501})()

    monkeypatch.setattr(ports_api, "_list_ports", rows)
    monkeypatch.setattr(ports_api.psutil, "Process", Process)
    listed = asyncio.run(ports_api.list_listeners())
    assert listed["available"] is True
    assert listed["ports"][0]["cmdline"] == "python server.py"
    detail = asyncio.run(ports_api.port_detail(8080))
    assert detail["processes"][0]["started_at"] == 1234.5


def test_termination_revalidates_and_never_signals_stale_target(monkeypatch):
    async def rows():
        return []

    monkeypatch.setattr(ports_api, "_list_ports", rows)
    req = ports_api.TerminateRequest(pid=4321, command="python",
                                     address="127.0.0.1:8080", started_at=1234.5)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ports_api.terminate_listener(8080, req))
    assert exc.value.status_code == 409


def test_termination_signals_only_after_live_identity_is_rechecked(monkeypatch):
    async def rows():
        return [{"port": 8080, "pid": 4321, "command": "python", "user": ports_api.getpass.getuser(),
                 "address": "127.0.0.1:8080"}]

    signaled = []

    class Process:
        def __init__(self, pid): pass
        def create_time(self): return 1234.5
        def uids(self): return type("Uids", (), {"real": __import__("os").getuid()})()
        def send_signal(self, sig): signaled.append(sig)

    monkeypatch.setattr(ports_api, "_list_ports", rows)
    monkeypatch.setattr(ports_api.psutil, "Process", Process)
    req = ports_api.TerminateRequest(pid=4321, command="python",
                                     address="127.0.0.1:8080", started_at=1234.5)
    result = asyncio.run(ports_api.terminate_listener(8080, req))
    assert result["sent"] == "SIGTERM"
    assert len(signaled) == 1


def test_server_process_is_always_protected(monkeypatch):
    req = ports_api.TerminateRequest(pid=ports_api.os.getpid(), command="python",
                                     address="127.0.0.1:8080", started_at=1)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ports_api.terminate_listener(8080, req))
    assert exc.value.status_code == 403
