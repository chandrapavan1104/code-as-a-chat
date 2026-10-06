import asyncio

from fastapi.testclient import TestClient

from server import config, main, phone_bridge, workspace
from server.skills import phone, shell


def _phone_answering(answer: dict, frames: list):
    """An on_event that behaves like the app: record the frame, then answer."""
    async def on_event(ev):
        if ev.get("type") != "phone_request":
            return
        frames.append(ev)
        asyncio.get_running_loop().call_later(
            0.01, phone_bridge.deliver, ev["id"], answer)
    return on_event


def _run(prompt, on_event):
    return asyncio.run(phone.PhoneSkill().run(prompt, on_event=on_event))


def test_location_round_trip():
    frames = []
    out = _run("location", _phone_answering(
        {"ok": True, "data": {"lat": 17.385, "lon": 78.4867, "accuracy_m": 12}}, frames))
    assert frames[0]["command"] == "location.get" and frames[0]["args"] == {}
    assert out.status == "succeeded" and out.data["lat"] == 17.385
    assert "PHONE LOCATION" in out.message


def test_calendar_and_contacts_arguments():
    frames = []
    _run("calendar week", _phone_answering({"ok": True, "data": {"events": []}}, frames))
    _run("contacts Gowtham", _phone_answering({"ok": True, "data": {"contacts": []}}, frames))
    assert frames[0]["args"] == {"range": "week"}
    assert frames[1]["args"] == {"query": "Gowtham"}


def test_photo_result_is_exposed_as_an_image_for_the_agent():
    out = _run("photo the router lights", _phone_answering(
        {"ok": True, "data": {"path": "/uploads/abc.jpg"}}, []))
    assert "[image: /uploads/abc.jpg]" in out.message


def test_disabled_ability_says_so():
    out = _run("location", _phone_answering(
        {"ok": False, "disabled": True, "error": "Location is off in Phone abilities"}, []))
    assert out.status == "needs_permission"
    assert "Location is off in Phone abilities" in out.message


def test_without_the_app_stream_the_phone_is_not_reachable():
    out = _run("location", None)
    assert out.status == "failed" and "talking to Gajala in the app" in out.message


def test_unanswered_request_times_out_cleanly(monkeypatch):
    monkeypatch.setitem(phone._COMMANDS, "status", ("device.status", 0.05))

    async def silent(ev):
        pass

    out = _run("status", silent)
    assert out.status == "failed" and "did not answer in time" in out.message
    assert phone_bridge._pending == {}


def test_bad_arguments():
    assert _run("teleport", None).status == "failed"
    assert _run("contacts", None).status == "failed"


def test_result_endpoint():
    client = TestClient(main.app)
    headers = {"X-API-Token": config.API_TOKEN}
    r = client.post("/api/phone/result/nobody", json={"ok": True}, headers=headers)
    assert r.status_code == 404
    assert client.post("/api/phone/result/x", json={"ok": True}).status_code == 401


def _replay(monkeypatch, decisions):
    seq = iter(decisions)

    async def fake_haiku(*args, **kwargs):
        return next(seq, '{"action":"done","reply":"finished"}')

    monkeypatch.setattr(shell, "_haiku", fake_haiku)


def test_shell_hands_the_phone_channel_only_to_app_streams(monkeypatch, tmp_path):
    seen = []

    async def fake_request(on_event, command, args, *, timeout):
        seen.append(on_event is not None)
        if on_event is None:
            raise phone_bridge.PhoneUnavailable("no app")
        return {"ok": True, "data": {"battery": 80}}

    monkeypatch.setattr(phone_bridge, "request", fake_request)

    async def on_event(ev):
        pass

    for phone_stream in (True, False):
        _replay(monkeypatch, ['{"action":"call","tool":"phone","args":"status"}',
                              '{"action":"done","reply":"ok"}'])

        async def turn():
            with workspace.bound(tmp_path):
                return await shell.ShellSkill().run(
                    "how is my battery", on_event=on_event, phone_stream=phone_stream)
        asyncio.run(turn())
    assert seen == [True, False]
