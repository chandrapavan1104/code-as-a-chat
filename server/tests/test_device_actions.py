import asyncio

import pytest

from server import phone_bridge
from server.skills import device


@pytest.mark.parametrize("prompt, command, args", [
    ("alarm 6:30 am", "action.alarm", {"hour": 6, "minute": 30, "label": ""}),
    ("alarm 7 pm gym", "action.alarm", {"hour": 19, "minute": 0, "label": "gym"}),
    ("alarm 18:45", "action.alarm", {"hour": 18, "minute": 45, "label": ""}),
    ("alarm 12 am", "action.alarm", {"hour": 0, "minute": 0, "label": ""}),
    ("timer 10m tea", "action.timer", {"seconds": 600, "label": "tea"}),
    ("timer 1h30m", "action.timer", {"seconds": 5400, "label": ""}),
    ("timer 10 minutes for pasta", "action.timer", {"seconds": 600, "label": "for pasta"}),
    ("timer 90", "action.timer", {"seconds": 90, "label": ""}),
    ("play lo-fi beats on spotify", "action.play", {"query": "lo-fi beats", "app": "spotify"}),
    ("play Arijit Singh", "action.play", {"query": "Arijit Singh", "app": None}),
    ("play shape of you on youtube music", "action.play",
     {"query": "shape of you", "app": "youtube_music"}),
    ("media next", "action.media", {"key": "next"}),
    ("volume mute", "action.volume", {"change": "mute"}),
    ("open WhatsApp", "action.open_app", {"name": "WhatsApp"}),
    ("message Amma via whatsapp: reaching in 10 mins", "action.compose",
     {"to": "Amma", "via": "whatsapp", "text": "reaching in 10 mins"}),
    ("sms +919876543210: on my way", "action.sms_send",
     {"to": "+919876543210", "text": "on my way"}),
    ("call Gowtham", "action.call", {"to": "Gowtham"}),
    ("event Dentist | 2026-10-09T16:00 | 2026-10-09T17:00 | Apollo", "action.calendar_add",
     {"title": "Dentist", "start": "2026-10-09T16:00:00", "end": "2026-10-09T17:00:00",
      "location": "Apollo"}),
    ("event Standup | 2026-10-09T09:30", "action.calendar_add",
     {"title": "Standup", "start": "2026-10-09T09:30:00"}),
    ("flashlight on", "action.flashlight", {"on": True}),
    ("torch off", "action.flashlight", {"on": False}),
    ("settings bluetooth", "action.settings", {"panel": "bluetooth"}),
    ("navigate home", "action.navigate", {"to": "home"}),
    ("dnd on", "action.dnd", {"on": True}),
    ("notifications", "action.notifications", {}),
    ("camera selfie", "action.camera", {"selfie": True}),
])
def test_parse(prompt, command, args):
    assert device.parse(prompt) == (command, args)


@pytest.mark.parametrize("bad", [
    "alarm sometime", "alarm 25:00", "alarm 13 pm", "timer soon", "timer 30h",
    "play", "media louder", "volume 11", "open", "message mom hello",
    "sms mom", "event Dentist", "event Dentist | friday", "flashlight bright",
    "settings airplane", "dnd maybe", "teleport home",
    "event X | 2026-10-09T17:00 | 2026-10-09T16:00",
])
def test_bad_commands_are_rejected(bad):
    with pytest.raises(device.BadCommand):
        device.parse(bad)


def _run(prompt, answer, frames):
    async def on_event(ev):
        if ev.get("type") == "phone_request":
            frames.append(ev)
            asyncio.get_running_loop().call_later(0.01, phone_bridge.deliver, ev["id"], answer)
    return asyncio.run(device.DeviceSkill().run(prompt, on_event=on_event))


def test_round_trip_reports_what_the_phone_did():
    frames = []
    out = _run("alarm 6:30 am", {"ok": True, "data": {"done": "Alarm set for 6:30 AM."}}, frames)
    assert frames[0]["command"] == "action.alarm"
    assert out.status == "succeeded" and out.message == "Alarm set for 6:30 AM." and out.changed


def test_disabled_sensitive_action():
    out = _run("sms Amma: hi", {"ok": False, "disabled": True,
                                "error": "Direct SMS is off"}, [])
    assert out.status == "needs_permission" and "Direct SMS is off" in out.message


def test_reading_notifications_is_not_a_change():
    out = _run("notifications", {"ok": True, "data": {"notifications": []}}, [])
    assert out.status == "succeeded" and not out.changed


def test_bad_command_never_reaches_the_phone():
    frames = []
    out = _run("alarm whenever", {"ok": True}, frames)
    assert out.status == "failed" and frames == []
