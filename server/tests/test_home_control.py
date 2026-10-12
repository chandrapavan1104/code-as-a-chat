import asyncio
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from server import home_api, home_control


def run(awaitable):
    return asyncio.run(awaitable)


def online_status(**extra):
    return {"connected": True, "updated_at": time.time(), **extra}


def switch_catalog(*rows):
    return {"available": True, "loaded": True, "switches": list(rows)}


def switch_row(id="kitchen-lamp", name="Kitchen Lamp", **extra):
    return {"id": id, "name": name, "room": "Kitchen", "kind": "dimmer",
            "dimmable": True, "reachable": True, "level": 0, "on": False,
            **extra}


def report_item(cc, command, value, *, node="02"):
    return {"id": "lamp", "name": "Kitchen Lamp", "state": value,
            "state_confirmed": True, "observed_at": time.time() + .01,
            "last_command_at": time.time(), "on": value > 0,
            "level": value, "observed_fields": {"mode": time.time() + .01},
            "cc": cc, "command": command, "node": node}


@pytest.fixture(autouse=True)
def clear_confirmation_tickets():
    with home_api._lock:
        home_api._tickets.clear()
    yield
    with home_api._lock:
        home_api._tickets.clear()


def fake_request(monkeypatch, handler):
    calls = []

    async def request(path, payload=None, *, method=None):
        calls.append((path, payload, method))
        return await handler(path, payload, method)

    monkeypatch.setattr(home_control, "request", request)
    return calls


def test_catalog_connected_fresh_and_exact_switch_target_can_be_confirmed(monkeypatch):
    starts = []

    async def request(path, payload=None, method=None):
        if path == "/api/status":
            return online_status()
        if path == "/api/switches":
            return switch_catalog(switch_row())
        if path == "/api/switch":
            starts.append(payload)
            return {"switches": [switch_row(on=True, level=55,
                observed_at=time.time(), last_command_at=time.time() - .1,
                state_confirmed=True)]}
        raise AssertionError(f"unexpected request {path}")

    calls = fake_request(monkeypatch, request)
    result = run(home_control.perform(home_control.HomeAction(
        action="switch", target="kitchen-lamp", state=True, level=55)))

    assert result.status == "succeeded"
    assert starts == [{"target": "kitchen-lamp", "state": True, "level": 55}]
    assert [path for path, _, _ in calls] == ["/api/status", "/api/switches", "/api/switch"]


def test_target_resolution_requires_exact_unique_match():
    rows = [switch_row("lamp-1", "Living Lamp"), switch_row("lamp-2", "Lamp West")]
    assert home_control.resolve(rows, "lamp-1")["id"] == "lamp-1"
    with pytest.raises(home_control.HomeError, match="More than one"):
        home_control.resolve(rows, "lamp")
    with pytest.raises(home_control.HomeError, match="No device matches"):
        home_control.resolve(rows, "garage")


@pytest.mark.parametrize("status", [
    {"connected": False, "updated_at": time.time()},
    {"connected": True, "updated_at": time.time() - 90},
    {"connected": True, "updated_at": time.time() + 2},
])
def test_stale_or_disconnected_panel_sends_no_command(monkeypatch, status):
    calls = fake_request(monkeypatch, lambda path, payload, method: _return_status(status))

    async def run_action():
        return await home_control.perform(home_control.HomeAction(
            action="switch", target="lamp", state=True))

    result = run(run_action())
    assert result.status == "blocked"
    assert "no command was sent" in result.message
    assert all(path != "/api/switch" for path, _, _ in calls)


async def _return_status(status):
    return status


def test_known_unreachable_switch_is_not_written(monkeypatch):
    row = switch_row(reachable=False)
    calls = []

    async def request(path, payload=None, *, method=None):
        calls.append(path)
        raise AssertionError("an unreachable switch must not be written")

    monkeypatch.setattr(home_control, "request", request)
    result = run(home_control.set_switch(row, True))
    assert result.status == "blocked"
    assert calls == []


@pytest.mark.parametrize("body", [
    {"action": "switch", "target": "lamp", "state": True, "level": 100},
    {"action": "switch", "target": "lamp", "state": True, "level": 0},
    {"action": "climate", "target": "thermostat", "heat": float("nan")},
    {"action": "climate", "target": "thermostat", "cool": float("inf")},
])
def test_invalid_dimming_and_nonfinite_values_rejected(body):
    with pytest.raises(ValidationError):
        home_control.HomeAction(**body)


def test_non_dimmable_switch_and_out_of_range_climate_never_post(monkeypatch):
    posts = []

    async def request(path, payload=None, *, method=None):
        if path == "/api/status":
            return online_status()
        if path == "/api/switches":
            return switch_catalog(switch_row(kind="switch", dimmable=False))
        if path == "/api/climate":
            if payload is None:
                return {"thermostats": [{"id": "t1", "name": "Hall", "unit": "F",
                                         "heat": 68, "cool": 76}]}
            posts.append((path, payload))
            return {"thermostats": []}
        if payload is not None:
            posts.append((path, payload))
        raise AssertionError(path)

    monkeypatch.setattr(home_control, "request", request)
    switch_result = run(home_control.perform(home_control.HomeAction(
        action="switch", target="kitchen-lamp", state=True, level=40)))
    climate_result = run(home_control.perform(home_control.HomeAction(
        action="climate", target="t1", heat=100)))
    assert switch_result.status == "blocked"
    assert climate_result.status == "blocked"
    assert posts == []


def test_matching_ack_without_read_is_unverified_then_fresh_read_confirms(monkeypatch):
    row = switch_row(on=True, level=60, state_confirmed=False, observed_at=None)
    report = False

    async def request(path, payload=None, method=None):
        if path == "/api/switch":
            if report:
                row.update(state_confirmed=True, observed_at=time.time() + .001,
                           last_command_at=time.time() - .01)
            else:
                row.update(state_confirmed=False, observed_at=None,
                           last_command_at=time.time())
            return {"switches": [dict(row)]}
        raise AssertionError(path)

    monkeypatch.setattr(home_control, "request", request)
    original_time = time.time
    result = run(home_control.set_switch(switch_row(), True, 60))
    assert result.status == "unverified"
    report = True
    # The action's request result is a distinct fresh panel read after command.
    result = run(home_control.set_switch(switch_row(), True, 60))
    assert result.status == "succeeded"
    assert result.data["device"]["observed_at"] >= original_time()


@pytest.mark.parametrize("uncertain", [True, False])
def test_command_transport_failure_is_not_retried_and_uncertain_failure_is_unverified(monkeypatch, uncertain):
    posts = []

    async def request(path, payload=None, method=None):
        if path == "/api/status":
            return online_status()
        if path == "/api/switches":
            return switch_catalog(switch_row())
        if path == "/api/switch":
            posts.append(payload)
            raise home_control.HomeError("transport failed", uncertain=uncertain)
        raise AssertionError(path)

    monkeypatch.setattr(home_control, "request", request)
    result = run(home_control.perform(home_control.HomeAction(
        action="switch", target="kitchen-lamp", state=True)))

    assert result.status == ("unverified" if uncertain else "blocked")
    assert len(posts) == 1


def test_all_off_stops_posting_after_time_budget_and_reports_skipped_devices(monkeypatch):
    rows = [switch_row(f"lamp-{i}", f"Lamp {i}") for i in range(3)]
    writes = []

    async def request(path, payload=None, method=None):
        if path == "/api/status":
            return online_status()
        if path == "/api/switches":
            return switch_catalog(*rows)
        if path == "/api/switch":
            writes.append(payload)
            row = next(row for row in rows if row["id"] == payload["target"])
            return {"switches": [{**row, "on": False, "level": 0,
                "state_confirmed": True, "observed_at": time.time() + .01,
                "last_command_at": time.time() - .01}]}
        raise AssertionError(path)

    ticks = iter([0.0, 0.0, 91.0, 91.0])
    monkeypatch.setattr(home_control, "time", type("FakeTime", (), {
        "monotonic": staticmethod(lambda: next(ticks)),
        "time": staticmethod(time.time),
    }))
    monkeypatch.setattr(home_control, "request", request)
    result = run(home_control.perform(home_control.HomeAction(action="all_off")))

    assert len(writes) == 1
    per_device = result.data["results"]
    assert [entry["status"] for entry in per_device] == ["succeeded", "blocked", "blocked"]
    assert all("not attempted" in entry["message"] for entry in per_device[1:])
    assert "time budget reached" in result.message


def test_climate_requires_observation_for_each_requested_field(monkeypatch):
    mode_reported = False

    async def request(path, payload=None, method=None):
        if path == "/api/status":
            return online_status()
        if path == "/api/climate" and payload is None:
            return {"thermostats": [{"id": "hall", "name": "Hall", "unit": "F",
                                     "mode": "heat", "fan": "auto", "heat": 68, "cool": 75}]}
        if path == "/api/climate":
            stamps = {"fan": time.time() + .01}
            if mode_reported:
                stamps["mode"] = time.time() + .01
            return {"thermostats": [{"id": "hall", "name": "Hall", "mode": "heat",
                                     "fan": "on", "observed_fields": stamps}]}
        raise AssertionError(path)

    monkeypatch.setattr(home_control, "request", request)
    body = home_control.HomeAction(action="climate", target="hall", mode="heat", fan="on")
    first = run(home_control.perform(body))
    assert first.status == "unverified"  # mode matches, but its read is stale/missing
    mode_reported = True
    second = run(home_control.perform(body))
    assert second.status == "succeeded"


def test_room_returns_partial_results_and_never_posts_for_unreachable_member(monkeypatch):
    posts = []
    rows = [switch_row("dead", "Dead Lamp", reachable=False),
            switch_row("working", "Working Lamp", level=0, on=False)]

    async def request(path, payload=None, method=None):
        if path == "/api/status": return online_status()
        if path == "/api/switches": return switch_catalog(*rows)
        if path == "/api/switch":
            posts.append(payload)
            return {"switches": [{**rows[1], "on": True, "level": 100,
                "state_confirmed": False, "observed_at": None}]}
        raise AssertionError(path)

    monkeypatch.setattr(home_control, "request", request)
    result = run(home_control.perform(home_control.HomeAction(
        action="room", room="Kitchen", state=True)))
    assert result.status == "unverified"
    assert len(result.data["results"]) == 2
    assert posts == [{"target": "working", "state": True, "level": None}]
    assert any("not responding" in row["message"] for row in result.data["results"])


def test_alarm_and_lock_actions_are_not_in_the_public_contract():
    for action in ("alarm", "lock"):
        with pytest.raises(ValidationError):
            home_control.HomeAction(action=action)


def test_garage_presence_is_required_cancelled_or_missing_phone_handler_sends_nothing(monkeypatch):
    async def online(): return online_status()
    async def request(path, payload=None, method=None):
        if path == "/api/status": return online_status()
        if path == "/api/garage": return {"doors": [{"id": "garage", "name": "Garage",
            "state": "closed", "observed_at": time.time()}]}
        if path == "/api/garage" and payload is not None:
            raise AssertionError("no physical write expected")
        return {}
    monkeypatch.setattr(home_control, "request", request)
    monkeypatch.setattr(home_control, "require_online", online)
    calls = []

    async def canceled(*args, **kwargs):
        calls.append((args, kwargs))
        return {"confirmed": False, "present": False}

    monkeypatch.setattr("server.skills.home.phone_bridge.request", canceled)
    from server.skills.home import HomeSkill
    skill = HomeSkill()
    canceled_result = awaitable_run(skill.run('{"action":"garage","target":"garage","state":true}', on_event=lambda e: None))
    assert canceled_result.status == "blocked"
    assert len(calls) == 1

    async def no_handler(*args, **kwargs):
        from server.phone_bridge import PhoneUnavailable
        raise PhoneUnavailable("no live phone")

    monkeypatch.setattr("server.skills.home.phone_bridge.request", no_handler)
    no_handler_result = awaitable_run(skill.run('{"action":"garage","target":"garage","state":true}'))
    assert no_handler_result.status == "needs_permission"


def awaitable_run(awaitable):
    return asyncio.run(awaitable)


def test_api_garage_token_expires_and_is_consumed_before_execution(monkeypatch):
    current = [1000.0]
    monkeypatch.setattr(home_api.time, "time", lambda: current[0])
    monkeypatch.setattr(home_api.secrets, "token_urlsafe", lambda _n: "x" * 24)

    async def online(): return online_status()
    async def request(path, payload=None, method=None):
        if path == "/api/garage":
            return {"doors": [{"id": "garage", "name": "Garage", "state": "closed",
                               "observed_at": 1000.0}]}
        raise AssertionError(path)
    monkeypatch.setattr(home_control, "require_online", online)
    monkeypatch.setattr(home_control, "request", request)
    body = home_api.GarageRequest(target="garage", open=True)
    ticket = asyncio.run(home_api.garage_request(body))
    assert ticket["expires_at"] == 1030.0
    calls = []

    async def uncertain(*args, **kwargs):
        calls.append((args, kwargs))
        raise TimeoutError("command result uncertain")

    monkeypatch.setattr(home_control, "perform", uncertain)
    confirm = home_api.GarageConfirmation(token=ticket["token"], present=True)
    with pytest.raises(TimeoutError):
        asyncio.run(home_api.garage_confirm(confirm))
    with pytest.raises(HTTPException) as error:
        asyncio.run(home_api.garage_confirm(confirm))
    assert error.value.status_code == 409
    assert len(calls) == 1

    ticket2 = asyncio.run(home_api.garage_request(body))
    current[0] = 1031.0
    with pytest.raises(HTTPException) as expired:
        asyncio.run(home_api.garage_confirm(home_api.GarageConfirmation(
            token=ticket2["token"], present=True)))
    assert expired.value.status_code == 409
    assert len(calls) == 1


def test_false_presence_consumes_ticket_and_bypassing_route_is_forbidden(monkeypatch):
    async def online(): return online_status()
    async def request(path, payload=None, method=None):
        return {"doors": [{"id": "garage", "name": "Garage", "state": "closed",
                           "observed_at": time.time()}]}
    monkeypatch.setattr(home_control, "require_online", online)
    monkeypatch.setattr(home_control, "request", request)
    monkeypatch.setattr(home_api.secrets, "token_urlsafe", lambda _n: "y" * 24)
    calls = []
    async def execute(*args, **kwargs): calls.append(1)
    monkeypatch.setattr(home_control, "perform", execute)

    ticket = asyncio.run(home_api.garage_request(home_api.GarageRequest(
        target="garage", open=False)))
    with pytest.raises(HTTPException) as denied:
        asyncio.run(home_api.garage_confirm(home_api.GarageConfirmation(
            token=ticket["token"], present=False)))
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as consumed:
        asyncio.run(home_api.garage_confirm(home_api.GarageConfirmation(
            token=ticket["token"], present=True)))
    assert consumed.value.status_code == 409
    assert calls == []
    with pytest.raises(HTTPException) as bypass:
        asyncio.run(home_api.home_action(home_control.HomeAction(
            action="garage", target="garage", state=True)))
    assert bypass.value.status_code == 403


def test_only_lighting_rules_can_change_and_duplicate_schedule_is_deduplicated(monkeypatch):
    writes = []
    rules = [{"id": "alarm-rule", "action": {"type": "alarm"}}]

    async def request(path, payload=None, method=None):
        if path == "/api/status": return online_status()
        if path == "/api/automations": return {"rules": rules}
        if path.startswith("/api/automations/") and path.endswith("/enabled"):
            writes.append((path, payload, method))
            return {"ok": True}
        if path.startswith("/api/automations/") and method == "PUT":
            writes.append((path, payload, method))
            rules.append(payload)
            return payload
        raise AssertionError((path, payload, method))

    monkeypatch.setattr(home_control, "request", request)
    blocked = run(home_control.perform(home_control.HomeAction(
        action="automation", target="alarm-rule", enabled=False)))
    assert blocked.status == "blocked"
    assert writes == []

    schedule = home_control.HomeAction(action="schedule", state=False,
                                       at="22:00", days=["mon", "wed"])
    first = run(home_control.perform(schedule))
    second = run(home_control.perform(schedule))
    assert first.status == second.status == "succeeded"
    puts = [entry for entry in writes if entry[2] == "PUT"]
    assert len(puts) == 1
    assert puts[0][1]["action"] == {"type": "all_off"}


def test_garage_accepts_only_fresh_phone_envelope(monkeypatch):
    from server import phone_bridge
    from server.skills.home import HomeSkill
    async def online(): return {'connected':True}
    async def get(path): return {'doors':[{'id':'garage','name':'Garage'}]}
    calls=[]
    async def perform(body, **kwargs):
        calls.append(kwargs)
        from server.skills.base import SkillResult
        return SkillResult('unverified','Door moving')
    async def approval(*args,**kwargs):
        return {'ok':True,'data':{'confirmed':True,'present':True}}
    monkeypatch.setattr(home_control,'require_online',online)
    monkeypatch.setattr(home_control,'request',get)
    monkeypatch.setattr(home_control,'perform',perform)
    monkeypatch.setattr(phone_bridge,'request',approval)
    result=asyncio.run(HomeSkill().run('garage open',phone_stream=True,on_event=object()))
    assert result.status=='unverified'
    assert calls==[{'garage_confirmed':True}]


def test_garage_completion_requires_sensor_report_after_command(monkeypatch):
    calls=[]
    sent=[False]
    async def request(path,payload=None,method=None):
        if path=='/api/status': return online_status()
        if payload is not None:
            calls.append(payload)
            sent[0]=True
            return {'doors':[{'id':'garage','state':'opening','state_confirmed':False}]}
        return {'doors':[{'id':'garage','name':'Garage','state':'open' if sent[0] else 'closed',
                         'observed_at':time.time()+.01,'state_confirmed':True}]}
    async def no_wait(*args): pass
    monkeypatch.setattr(home_control,'request',request)
    monkeypatch.setattr(home_control.asyncio,'sleep',no_wait)
    result=asyncio.run(home_control.perform(home_control.HomeAction(action='garage',target='garage',state=True),garage_confirmed=True))
    assert result.status=='succeeded'
    assert 'sensor confirmed' in result.message
    assert len(calls)==1


def test_garage_ack_does_not_repeat_when_final_position_unverified(monkeypatch):
    calls=[]
    async def request(path,payload=None,method=None):
        if path=='/api/status': return online_status()
        if payload is not None:
            calls.append(payload)
            return {'doors':[{'id':'garage','state':'opening','state_confirmed':False}]}
        return {'doors':[{'id':'garage','name':'Garage','state':'closed',
                         'observed_at':time.time()-1,'state_confirmed':True}]}
    async def no_wait(*args): pass
    values=iter([0,1,26])
    monkeypatch.setattr(home_control,'request',request)
    monkeypatch.setattr(home_control.asyncio,'sleep',no_wait)
    monkeypatch.setattr(home_control.time,'monotonic',lambda: next(values,100))
    result=asyncio.run(home_control.perform(home_control.HomeAction(action='garage',target='garage',state=True),garage_confirmed=True))
    assert result.status=='unverified'
    assert len(calls)==1
