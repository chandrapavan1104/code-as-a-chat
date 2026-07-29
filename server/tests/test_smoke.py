"""Smoke tests — cheap sanity checks that the server wiring imports and holds
together, with no external CLIs or network. Keeps CI meaningful and green."""

import asyncio


def test_config_imports_and_has_token():
    from server import config
    assert isinstance(config.API_TOKEN, str) and config.API_TOKEN
    assert isinstance(config.BATTERY_ALERTS, bool)


def test_api_v2_router_mounts():
    from server.api_v2 import router
    paths = {r.path for r in router.routes}
    assert "/api/system" in paths
    assert "/api/devices" in paths


def test_fcm_available_is_bool_without_key():
    # No service-account key on CI → available() must return False, not raise.
    from server import fcm
    assert isinstance(fcm.available(), bool)


def test_skills_register():
    from server.skills import discover, registry
    discover()  # import every skill module so each self-registers
    assert {"shell", "mac", "notes", "reminders"} <= set(registry)


def test_shell_accepts_tool_name_as_action(monkeypatch):
    from server.skills import registry
    from server.skills.base import Skill
    from server.skills import shell

    class ProbeSkill(Skill):
        name = "probe"
        description = "test probe"
        final_output = True

        async def run(self, prompt="", **kwargs):
            return f"probe ran: {prompt}"

    async def fake_haiku(*args, **kwargs):
        return '{"action":"probe","args":"status","final":true}'

    monkeypatch.setitem(registry, "probe", ProbeSkill())
    monkeypatch.setattr(shell, "_haiku", fake_haiku)

    result = asyncio.run(shell.ShellSkill().run("check status"))

    assert result == "probe ran: status"


def test_usage_ignores_expired_rate_limits(monkeypatch):
    from server import api_v2

    monkeypatch.setattr(api_v2.time, "time", lambda: 2_000)
    report = {"limitUsage": [
        {"window": "5h", "usedPercent": 80, "resetsAt": 1_999},
        {"window": "7d", "usedPercent": 25, "resetsAt": 2_001},
    ]}

    assert api_v2._rate_pcts(report) == (None, 25)
    assert api_v2._limits(report) == [
        {"label": "weekly", "pct": 25.0, "detail": None},
    ]
