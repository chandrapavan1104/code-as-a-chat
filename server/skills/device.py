"""device skill — do things ON the owner's phone, Google-Assistant style.

Alarms, timers, music, media keys, volume, apps, messages, calls, calendar
events, flashlight, settings panels, navigation, Do Not Disturb and reading
notifications. Requests travel over the live app stream like the `phone`
skill (server/phone_bridge.py), so they work from typed chat, voice, and the
agent's own multi-step plans. The phone owns the safety switches: sensitive
actions (direct SMS, Do Not Disturb, reading notifications) stay off until the
owner enables them there.

This module only parses the agent's short command into validated structured
arguments; the phone performs the action.
"""

import json
import re
from datetime import datetime

from server import phone_bridge
from server.skills import register
from server.skills.base import Skill, SkillResult

USAGE = (
    'Usage: device alarm 6:30 am [label] | timer 10m [label] | play <what> [on spotify|'
    'youtube music|youtube] | media pause|play|next|previous | volume up|down|mute|'
    'unmute | open <app> | message <to> via sms|whatsapp: <text> | sms <to>: <text> | '
    'call <name or number> | event <title> | <start ISO> | [end ISO] | [location] | '
    'flashlight on|off | settings wifi|bluetooth|internet|volume|nfc | navigate <place|'
    'home|work> | dnd on|off | notifications | camera [selfie]')

_UNIT = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
         "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
_APPS = {"spotify": "spotify", "youtube music": "youtube_music", "yt music": "youtube_music",
         "youtube": "youtube"}


class BadCommand(ValueError):
    pass


def _clock(text: str) -> tuple[int, int, str]:
    m = re.match(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b\s*(.*)$", text.strip(), re.I)
    if not m:
        raise BadCommand("alarm needs a time like 6:30 am or 18:45")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower().replace(".", "")
    if meridiem:
        if not 1 <= hour <= 12:
            raise BadCommand("hour must be 1-12 with am/pm")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        raise BadCommand("not a valid time of day")
    return hour, minute, m.group(4).strip()


def _duration(text: str) -> tuple[int, str]:
    total, rest = 0, text.strip()
    for m in re.finditer(r"(\d+)\s*([a-z]+)", rest.lower()):
        unit = _UNIT.get(m.group(2))
        if unit is None:
            break
        total += int(m.group(1)) * unit
    if total == 0 and rest.isdigit():
        total = int(rest)   # bare number = seconds
        rest = ""
    if total <= 0 or total > 24 * 3600:
        raise BadCommand("timer needs a length like 10m, 1h30m or 90s (max 24h)")
    label = re.sub(r"^(\s*\d+\s*[a-z]+)+\s*", "", text.strip(), flags=re.I) if rest else ""
    return total, label


def _iso(value: str, what: str) -> str:
    try:
        return datetime.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise BadCommand(f"{what} must be an ISO date-time like 2026-10-09T16:00") from None


def parse(prompt: str) -> tuple[str, dict]:
    """Agent command → (phone action, args). Raises BadCommand."""
    text = prompt.strip()
    verb, _, rest = text.partition(" ")
    verb, rest = verb.lower(), rest.strip()

    if verb == "alarm":
        hour, minute, label = _clock(rest)
        return "action.alarm", {"hour": hour, "minute": minute, "label": label}
    if verb == "timer":
        seconds, label = _duration(rest)
        return "action.timer", {"seconds": seconds, "label": label}
    if verb == "play":
        app = None
        m = re.search(r"\s+on\s+(spotify|youtube music|yt music|youtube)\s*$", rest, re.I)
        if m:
            app = _APPS[m.group(1).lower()]
            rest = rest[:m.start()].strip()
        if not rest:
            raise BadCommand("play needs something to play")
        return "action.play", {"query": rest, "app": app}
    if verb == "media":
        key = rest.lower()
        if key not in {"pause", "play", "next", "previous", "toggle"}:
            raise BadCommand("media takes pause, play, next, previous or toggle")
        return "action.media", {"key": key}
    if verb == "volume":
        level = rest.lower()
        if level not in {"up", "down", "mute", "unmute"}:
            raise BadCommand("volume takes up, down, mute or unmute")
        return "action.volume", {"change": level}
    if verb == "open":
        if not rest:
            raise BadCommand("open needs an app name")
        return "action.open_app", {"name": rest}
    if verb == "message":
        m = re.match(r"^(.+?)\s+via\s+(sms|whatsapp)\s*:\s*(.+)$", rest, re.I | re.S)
        if not m:
            raise BadCommand("message needs: <to> via sms|whatsapp: <text>")
        return "action.compose", {"to": m.group(1).strip(), "via": m.group(2).lower(),
                                  "text": m.group(3).strip()}
    if verb == "sms":
        to, sep, body = rest.partition(":")
        if not sep or not to.strip() or not body.strip():
            raise BadCommand("sms needs: <to>: <text>")
        return "action.sms_send", {"to": to.strip(), "text": body.strip()}
    if verb == "call":
        if not rest:
            raise BadCommand("call needs a name or number")
        return "action.call", {"to": rest}
    if verb == "event":
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) < 2 or not parts[0]:
            raise BadCommand("event needs: <title> | <start ISO> [| end ISO] [| location]")
        args = {"title": parts[0], "start": _iso(parts[1], "start")}
        if len(parts) > 2 and parts[2]:
            args["end"] = _iso(parts[2], "end")
            if args["end"] <= args["start"]:
                raise BadCommand("end must be after start")
        if len(parts) > 3 and parts[3]:
            args["location"] = parts[3]
        return "action.calendar_add", args
    if verb in ("flashlight", "torch"):
        if rest.lower() not in {"on", "off"}:
            raise BadCommand("flashlight on or off")
        return "action.flashlight", {"on": rest.lower() == "on"}
    if verb == "settings":
        panel = rest.lower()
        if panel not in {"wifi", "bluetooth", "internet", "volume", "nfc"}:
            raise BadCommand("settings takes wifi, bluetooth, internet, volume or nfc")
        return "action.settings", {"panel": panel}
    if verb == "navigate":
        if not rest:
            raise BadCommand("navigate needs a destination, home or work")
        return "action.navigate", {"to": rest}
    if verb == "dnd":
        if rest.lower() not in {"on", "off"}:
            raise BadCommand("dnd on or off")
        return "action.dnd", {"on": rest.lower() == "on"}
    if verb == "notifications":
        return "action.notifications", {}
    if verb == "camera":
        return "action.camera", {"selfie": rest.lower() == "selfie"}
    raise BadCommand(USAGE)


# Seconds to wait for the phone: confirmations and contact lookups take longer.
_WAIT = {"action.calendar_add": 120, "action.sms_send": 60, "action.compose": 60,
         "action.call": 60}


def _phone_channel(kwargs: dict):
    """The event sink only reaches the phone on an app /run/stream turn. Plain
    /run (Telegram, widgets) also passes a sink, but it goes nowhere: using it
    would wait out the full timeout instead of failing fast."""
    return kwargs.get("on_event") if kwargs.get("phone_stream") else None


class DeviceSkill(Skill):
    name = "device"
    description = "Do things on your phone: alarms, timers, music, apps, messages, flashlight…"
    agent_doc = (
        "Act ON the owner's Android phone (only during a Gajala app chat). One action per "
        "call. args: \"alarm 6:30 am [label]\" | \"timer 10m [label]\" | \"play <song/artist/"
        "playlist> [on spotify|youtube music|youtube]\" | \"media pause|play|next|previous\" | "
        "\"volume up|down|mute|unmute\" | \"open <app>\" | \"message <name or number> via "
        "sms|whatsapp: <text>\" (opens it pre-filled; owner taps send) | \"sms <name or "
        "number>: <text>\" (sends immediately — only when the owner explicitly asked to send, "
        "never on a guess) | \"call <name or number>\" | \"event <title> | <start ISO> | "
        "[end ISO] | [location]\" (owner confirms on the phone) | \"flashlight on|off\" | "
        "\"settings wifi|bluetooth|internet|volume|nfc\" | \"navigate <place|home|work>\" | "
        "\"dnd on|off\" | \"notifications\" (read current ones) | \"camera [selfie]\". Use "
        "Gajala's own reminders (not this) for \"remind me\". If the phone says an action is "
        "off, tell the owner where to enable it rather than retrying.")

    async def run(self, prompt: str = "", **kwargs) -> SkillResult:
        try:
            command, args = parse(prompt)
        except BadCommand as exc:
            return SkillResult("failed", str(exc))
        try:
            answer = await phone_bridge.request(
                _phone_channel(kwargs), command, args, timeout=_WAIT.get(command, 30))
        except phone_bridge.PhoneUnavailable as exc:
            return SkillResult("failed", str(exc), data={"command": command})
        if not answer.get("ok"):
            status = "needs_permission" if answer.get("disabled") else "failed"
            return SkillResult(status, f"Phone could not do it: {answer.get('error') or 'unknown error'}",
                               data={"command": command})
        data = answer.get("data") or {}
        done = data.get("done") or "Done on the phone."
        extra = {k: v for k, v in data.items() if k != "done"}
        text = done + (f"\n{json.dumps(extra, ensure_ascii=False, indent=1)}" if extra else "")
        return SkillResult("succeeded", text, changed=command != "action.notifications",
                           data=data, evidence=[f"phone performed {command}"])


register(DeviceSkill())
