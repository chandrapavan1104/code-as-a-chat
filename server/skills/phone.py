"""phone skill — read things from the owner's phone during a Gajala chat.

Every ability is off on the phone until the owner turns it on, and each use is
shown in the chat. The phone answers with structured data; this skill only
formats it for the agent.
"""

import json

from server import phone_bridge
from server.skills import register
from server.skills.base import Skill, SkillResult

# command → (phone ability, seconds to wait). A photo waits for the owner to
# frame and press the shutter.
_COMMANDS = {
    "location": ("location.get", 45),
    "calendar": ("calendar.events", 30),
    "contacts": ("contacts.search", 30),
    "photo": ("camera.snap", 120),
    "status": ("device.status", 20),
}


def _parse(prompt: str) -> tuple[str, dict] | None:
    words = prompt.strip().split(None, 1)
    if not words:
        return None
    name = words[0].lower()
    rest = words[1].strip() if len(words) > 1 else ""
    if name not in _COMMANDS:
        return None
    if name == "calendar":
        span = rest.lower() or "today"
        return name, {"range": span if span in ("today", "tomorrow", "week") else "today"}
    if name == "contacts":
        return (name, {"query": rest}) if rest else None
    if name == "photo":
        return name, {"purpose": rest}
    return name, {}


def _phone_channel(kwargs: dict):
    """The event sink only reaches the phone on an app /run/stream turn. Plain
    /run (Telegram, widgets) also passes a sink, but it goes nowhere: using it
    would wait out the full timeout instead of failing fast."""
    return kwargs.get("on_event") if kwargs.get("phone_stream") else None


class PhoneSkill(Skill):
    name = "phone"
    description = "Read from your phone: location, calendar, contacts, a photo, status"
    agent_doc = (
        "The owner's Android phone, only during a Gajala app chat. args: "
        '"location" | "calendar today|tomorrow|week" | "contacts <name>" | '
        '"photo <what to capture>" (the owner takes it; you get an image path to '
        'read with claude) | "status" (battery, network). Each ability must be '
        "enabled by the owner on the phone; if it is off, say how to enable it "
        "rather than retrying.")

    async def run(self, prompt: str = "", **kwargs) -> SkillResult:
        parsed = _parse(prompt)
        if parsed is None:
            return SkillResult("failed", 'Usage: phone location | calendar today|'
                                         'tomorrow|week | contacts <name> | photo '
                                         '<what> | status')
        name, args = parsed
        command, timeout = _COMMANDS[name]
        try:
            answer = await phone_bridge.request(
                _phone_channel(kwargs), command, args, timeout=timeout)
        except phone_bridge.PhoneUnavailable as exc:
            return SkillResult("failed", str(exc), data={"command": command})
        if not answer.get("ok"):
            status = "needs_permission" if answer.get("disabled") else "failed"
            return SkillResult(status, f"Phone could not {name}: "
                                       f"{answer.get('error') or 'unknown error'}",
                               data={"command": command})
        data = answer.get("data") or {}
        text = f"PHONE {name.upper()}:\n{json.dumps(data, ensure_ascii=False, indent=1)}"
        if command == "camera.snap" and data.get("path"):
            text += f"\n[image: {data['path']}]"
        return SkillResult("succeeded", text, data=data,
                           evidence=[f"phone answered {command}"])


register(PhoneSkill())
