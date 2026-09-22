"""
reminders skill — time-based Telegram alerts.

Natural language in ("remind me tomorrow at 5pm to deploy DocxChat"); Haiku
extracts an absolute timestamp + the reminder text. The background scheduler
fires it when due and pushes a Telegram message.

Subcommands (passed via prompt):
  <natural language>     create a reminder ("in 2 hours check the deploy")
  add <natural language> explicit create
  list                   upcoming (unfired) reminders
  delete <id>            remove a reminder
"""

import datetime as dt
import os
import re
import time
from zoneinfo import ZoneInfo

from server.skills.base import Skill, SkillResult
from server.skills import register
from server.skills.shell import _haiku, _parse_json_decision   # reuse Haiku helpers
from server.skills.projects import _candidates as _project_candidates
from server.db import reminders_store as store


REMIND_SYSTEM_TEMPLATE = """\
Extract a reminder from the user's text.

Current local time: {now}
Known projects (or null): {projects}

Output ONLY a single JSON object, no prose:
{
  "text":    "<concise reminder text — what to remind the user about>",
  "due_at":  "<absolute local timestamp, format YYYY-MM-DD HH:MM>",
  "project": "<exact project name from the list, or null>",
  "recurrence": "none" or "daily",
  "timezone": "IANA timezone, normally the local timezone",
  "until_note_id": "optional numeric note id; use only when the user says until done"
}

Rules:
- Resolve relative times against the current local time above:
  "in 2 hours", "tomorrow 9am", "friday 5pm", "tonight", "next monday".
- If only a date is given (no time), default to 09:00.
- If no time is parseable at all, set due_at to null. Never guess a time.
- "text" is what to remind about, cleaned (drop "remind me to").
"""


def _chat_id_from_session(session_id: str | None) -> int | None:
    if session_id and session_id.startswith("tg:"):
        try:
            return int(session_id[3:])
        except ValueError:
            return None
    return None


def _projects_for_prompt() -> str:
    cands = _project_candidates()
    return ", ".join(c.name for c in cands) if cands else "(none)"


def _local_timezone() -> str:
    configured = os.environ.get("TZ", "").strip()
    candidates = [configured]
    for path in ("/etc/localtime", "/var/db/timezone/zoneinfo/localtime"):
        try:
            target = os.path.realpath(path)
            marker = "/zoneinfo/"
            if marker in target:
                candidates.append(target.split(marker, 1)[1])
        except OSError:
            pass
    for candidate in candidates:
        if candidate:
            try:
                ZoneInfo(candidate)
                return candidate
            except (KeyError, ValueError):
                continue
    raise ValueError("the local timezone is not available as an IANA timezone")


def _to_unix(due_str: str, timezone: str | None = None) -> float | None:
    """Parse 'YYYY-MM-DD HH:MM' (local) into a unix timestamp."""
    if not isinstance(due_str, str) or not due_str.strip():
        return None
    due_str = due_str.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = dt.datetime.strptime(due_str, fmt)
            if timezone:
                try:
                    return naive.replace(tzinfo=ZoneInfo(timezone)).timestamp()
                except (KeyError, ValueError):
                    return None
            return naive.timestamp()
        except ValueError:
            continue
    return None


def _humanize_until(target_unix: float) -> str:
    secs = int(target_unix - dt.datetime.now().timestamp())
    if secs <= 0:
        return "now/overdue"
    if secs < 3600:
        return f"in {secs // 60}m"
    if secs < 86400:
        return f"in {secs // 3600}h {(secs % 3600) // 60}m"
    days = secs // 86400
    return f"in {days}d {(secs % 86400) // 3600}h"


def _fmt_when(target_unix: float) -> str:
    return dt.datetime.fromtimestamp(target_unix).strftime("%a %m-%d %H:%M")


async def _create(text: str, session_id: str | None) -> str | SkillResult:
    text = text.strip()
    if not text:
        return "Usage: /remind <when> <what>  e.g. 'in 2h check the deploy'"

    try:
        timezone = _local_timezone()
    except ValueError as exc:
        return SkillResult("failed", f"[remind] {exc}")
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M (%A)")
    system = (REMIND_SYSTEM_TEMPLATE
              .replace("{now}", now)
              .replace("{projects}", _projects_for_prompt()))
    try:
        raw = await _haiku(system, f"User said: {text}", timeout=30, task="reminders",
                           validate=lambda o: _parse_json_decision(o) is not None)
    except Exception as exc:
        return f"[remind] couldn't parse time: {exc}"

    data = _parse_json_decision(raw)
    if not data:
        return f"[remind] couldn't understand the time in: {text}"

    due_unix = _to_unix(data.get("due_at", ""), data.get("timezone") or timezone)
    if due_unix is None:
        return SkillResult("failed", f"[remind] couldn't resolve a date from: {text}",
                           evidence=["The reminder time was absent or not parseable; no reminder was stored."])
    if due_unix <= time.time():
        return SkillResult("failed", "[remind] that time is in the past; no reminder was stored.",
                           evidence=["Reminder dates must resolve to a future instant."])
    weekday_match = re.search(r"\b(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", text, re.I)
    if weekday_match:
        names = {"mon": 0, "monday": 0, "tue": 1, "tuesday": 1,
                 "wed": 2, "wednesday": 2, "thu": 3, "thursday": 3,
                 "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
                 "sun": 6, "sunday": 6}
        try:
            actual = dt.datetime.fromtimestamp(due_unix, ZoneInfo(data.get("timezone") or timezone)).weekday()
        except (KeyError, ValueError):
            actual = -1
        if actual != names[weekday_match.group(1).lower()]:
            return SkillResult("failed", "[remind] the resolved date does not match the weekday requested; no reminder was stored.",
                               evidence=["Weekday/date mismatch detected in the parsed reminder."])

    rem_text = (data.get("text") or text).strip()
    project = data.get("project")
    if project and project not in {c.name for c in _project_candidates()}:
        project = None

    chat_id = _chat_id_from_session(session_id)
    recurrence_raw = data.get("recurrence")
    recurrence = str(recurrence_raw or "none").lower()
    if recurrence not in {"none", "daily"}:
        return SkillResult("failed", f"[remind] unsupported recurrence: {recurrence}")
    explicit_daily = bool(re.search(r"\b(?:daily|every\s+day|each\s+day)\b", text, re.I))
    if recurrence == "daily" and not explicit_daily:
        return SkillResult("failed", "[remind] the request does not specify a daily recurrence; no reminder was stored.")
    if explicit_daily:
        recurrence = "daily"
    tz_name = str(data.get("timezone") or timezone)
    until_note_id = data.get("until_note_id")
    try:
        until_note_id = int(until_note_id) if until_note_id is not None else None
    except (TypeError, ValueError):
        until_note_id = None
    asks_until_done = bool(re.search(r"\buntil\s+(?:it(?:'s| is)\s+)?done\b|\bwhen\s+done\b", text, re.I))
    explicit_note = re.search(r"\bnote\s*#?\s*(\d+)\b", text, re.I)
    if until_note_id is not None and (not explicit_note or int(explicit_note.group(1)) != until_note_id):
        until_note_id = None
    if asks_until_done and until_note_id is None:
        return SkillResult(
            "failed",
            "[remind] I need a linked note id to stop this reminder when the work is done; no reminder was stored.",
            evidence=["Automatic completion is supported only for an explicitly linked note."],
        )
    if until_note_id is not None:
        from server.db import notes_store
        if notes_store.get(until_note_id) is None:
            return SkillResult("failed", f"[remind] note #{until_note_id} does not exist; no reminder was stored.")
    try:
        rid = store.add(rem_text, due_unix, chat_id=chat_id, project=project,
                        recurrence=recurrence, timezone=tz_name,
                        until_note_id=until_note_id)
    except ValueError as exc:
        return SkillResult("failed", f"[remind] {exc}")

    proj_line = f"\nProject: {project}" if project else ""
    cadence = " daily" if recurrence == "daily" else ""
    until = f" until note #{until_note_id} is done" if until_note_id else ""
    return SkillResult("succeeded", (f"⏰ Reminder #{rid} set{cadence}{until}\n"
            f"When: {_fmt_when(due_unix)}  ({_humanize_until(due_unix)})\n"
            f"What: {rem_text}{proj_line}"), changed=True,
        data={"id": rid, "due_at": due_unix, "recurrence": recurrence,
              "timezone": tz_name, "until_note_id": until_note_id},
        evidence=[f"Stored reminder #{rid} for {dt.datetime.fromtimestamp(due_unix).isoformat()}"])


def _list() -> str:
    pending = store.list_pending()
    if not pending:
        return "No upcoming reminders."
    lines = [f"UPCOMING REMINDERS ({len(pending)}):", ""]
    for r in pending:
        proj = f" · {r['project']}" if r.get("project") else ""
        lines.append(f"#{r['id']}  {_fmt_when(r['due_at'])}  ({_humanize_until(r['due_at'])}){proj}")
        lines.append(f"   {r['text']}")
    return "\n".join(lines)


class RemindersSkill(Skill):
    name = "reminders"
    command = "remind"
    aliases = ["reminders"]
    description = "Time-based Telegram alerts: /remind <when> <what> | list | delete <id>"
    final_output = True
    agent_doc = ("Time-based Telegram alerts. The user gets a push when due. "
                 'args: "<when> <what>" to create (e.g. "in 2 hours check the deploy", '
                 '"tomorrow 9am standup") | "list" (upcoming) | "delete <id>"')

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        raw = prompt.strip()
        if not raw:
            return _list()

        first = raw.split()[0].lower()

        if first in ("list", "ls", "upcoming"):
            return _list()

        if first in ("delete", "del", "rm", "cancel"):
            rest = raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else ""
            m = re.search(r"\d+", rest)
            if not m:
                return "Usage: /remind delete <id>"
            rid = int(m.group())
            return f"Reminder #{rid} deleted." if store.delete(rid) else f"No reminder #{rid}"

        if first in ("add", "set", "new"):
            rest = raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else ""
            return await _create(rest, session_id)

        # Bare input → treat the whole thing as a reminder to create
        return await _create(raw, session_id)


register(RemindersSkill())
