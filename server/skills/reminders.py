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
import re

from server.skills.base import Skill
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
  "project": "<exact project name from the list, or null>"
}

Rules:
- Resolve relative times against the current local time above:
  "in 2 hours", "tomorrow 9am", "friday 5pm", "tonight", "next monday".
- If only a date is given (no time), default to 09:00.
- If no time is parseable at all, set due_at to 1 hour from now.
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


def _to_unix(due_str: str) -> float | None:
    """Parse 'YYYY-MM-DD HH:MM' (local) into a unix timestamp."""
    due_str = due_str.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = dt.datetime.strptime(due_str, fmt)
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


async def _create(text: str, session_id: str | None) -> str:
    text = text.strip()
    if not text:
        return "Usage: /remind <when> <what>  e.g. 'in 2h check the deploy'"

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
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

    due_unix = _to_unix(data.get("due_at", ""))
    if due_unix is None:
        return f"[remind] couldn't resolve a date from: {text}"

    rem_text = (data.get("text") or text).strip()
    project = data.get("project")
    if project and project not in {c.name for c in _project_candidates()}:
        project = None

    chat_id = _chat_id_from_session(session_id)
    rid = store.add(rem_text, due_unix, chat_id=chat_id, project=project)

    proj_line = f"\nProject: {project}" if project else ""
    return (f"⏰ Reminder #{rid} set\n"
            f"When: {_fmt_when(due_unix)}  ({_humanize_until(due_unix)})\n"
            f"What: {rem_text}{proj_line}")


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
