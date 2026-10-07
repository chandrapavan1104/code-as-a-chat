import asyncio
import time

from server.skills import mac


def test_wake_keeps_the_display_on_long_enough_to_type(monkeypatch):
    spawned = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            spawned.append((cmd, kw))

    monkeypatch.setattr(mac.subprocess, "Popen", FakePopen)
    start = time.monotonic()
    reply = asyncio.run(mac._wake())
    assert time.monotonic() - start < 1          # the phone is not kept waiting
    cmd, kw = spawned[0]
    assert cmd == ["caffeinate", "-d", "-u", "-t", "60"]
    assert kw["start_new_session"] is True      # survives the request finishing
    assert "kept on for 60 s" in reply


def test_wake_reports_a_spawn_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("caffeinate missing")
    monkeypatch.setattr(mac.subprocess, "Popen", boom)
    assert asyncio.run(mac._wake()).startswith("[mac] wake failed")
