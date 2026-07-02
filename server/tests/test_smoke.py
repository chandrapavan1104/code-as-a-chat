"""Smoke tests — cheap sanity checks that the server wiring imports and holds
together, with no external CLIs or network. Keeps CI meaningful and green."""


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
