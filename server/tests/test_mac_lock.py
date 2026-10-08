import asyncio

from server.skills import mac


def test_lock_uses_the_real_macos_lock_shortcut(monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=30):
        calls.append(cmd)
        return 0, "", ""

    monkeypatch.setattr(mac, "_run", fake_run)
    assert asyncio.run(mac._lock()) == "Mac locked."
    assert calls == [[
        "osascript", "-e",
        'tell application "System Events" to key code 12 using '
        '{control down, command down}',
    ]]


def test_lock_falls_back_to_display_sleep_without_claiming_it_locked(monkeypatch):
    calls = []

    async def fake_run(cmd, timeout=30):
        calls.append(cmd)
        if cmd[0] == "osascript":
            return 1, "", "not authorized to send Apple events"
        return 0, "", ""

    monkeypatch.setattr(mac, "_run", fake_run)
    reply = asyncio.run(mac._lock())
    assert calls[-1] == ["pmset", "displaysleepnow"]
    assert reply.startswith("[mac] explicit lock was denied")
    assert "Accessibility" in reply


def test_lock_reports_when_both_methods_fail(monkeypatch):
    async def fake_run(cmd, timeout=30):
        return 1, "", f"{cmd[0]} failed"

    monkeypatch.setattr(mac, "_run", fake_run)
    assert asyncio.run(mac._lock()) == "[mac] lock failed: pmset failed"
