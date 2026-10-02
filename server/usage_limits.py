"""Recognise "you are out of quota" in a provider's output and say so plainly.

A quota error used to reach the phone as `[claude error code 1]` plus raw JSON,
and the fallback brain then guessed at a sign-in problem. Quota and auth need
opposite responses (wait vs. re-login), so the distinction is made here, once,
for both the coding CLIs and the shell brain.
"""

import re
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

_LIMIT = re.compile(
    r"hit your (?:\w+ )?limit|usage limit|session limit|rate limit|"
    r"quota (?:exceeded|reached|exhausted)|\b429\b", re.I)
# e.g. "resets 5:50pm (America/Los_Angeles)"
_RESET = re.compile(
    r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)", re.I)


def is_usage_limit(text: str) -> bool:
    return bool(_LIMIT.search(text or ""))


def local_reset(text: str, *, now: datetime | None = None,
                local_tz: tzinfo | None = None) -> str | None:
    """The provider's reset time in this machine's timezone, e.g. "6:20 AM
    tomorrow". None when the text carries no parseable reset time."""
    m = _RESET.search(text or "")
    if not m:
        return None
    try:
        zone = ZoneInfo(m.group(4).strip())
    except Exception:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
    there = (now or datetime.now(zone)).astimezone(zone)
    reset = there.replace(hour=hour, minute=int(m.group(2) or 0), second=0, microsecond=0)
    if reset <= there:
        reset += timedelta(days=1)
    here_reset = reset.astimezone(local_tz)
    here_now = there.astimezone(local_tz)
    day = "" if here_reset.date() == here_now.date() else " tomorrow"
    return here_reset.strftime("%I:%M %p").lstrip("0") + day


def notice(engine: str, text: str, *, now: datetime | None = None,
           local_tz: tzinfo | None = None) -> str | None:
    """A user-facing sentence when [text] is a usage-limit error, else None."""
    if not is_usage_limit(text):
        return None
    when = local_reset(text, now=now, local_tz=local_tz)
    reset = f" It resets around {when}." if when else ""
    return (f"{engine}'s usage limit is reached.{reset} This is a usage quota, "
            "not a sign-in problem, so signing in again will not help. Wait for "
            "the reset or use another engine.")
