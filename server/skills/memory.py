"""
memory skill — inspect and manage the shell's conversation memory.

Subcommands:
  show | list | (empty)   show the last 10 turns of THIS conversation
  count                   how many turns are in memory
  clear | forget | reset  wipe memory for THIS conversation
"""

from server.skills.base import Skill
from server.skills import register
from server.db import store


PREVIEW_CAP = 200


class MemorySkill(Skill):
    name = "memory"
    description = "Manage shell conversation memory: show | count | clear"

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        if not session_id:
            return ("This skill needs a session_id — call it from a client "
                    "that supplies one (e.g. the Telegram bot).")

        cmd = (prompt.strip().split()[:1] or ["show"])[0].lower()

        if cmd in ("clear", "forget", "reset", "wipe"):
            n = store.clear(session_id)
            return f"Cleared {n} turn(s) from this conversation."

        if cmd == "count":
            return f"Turns in this conversation: {store.count(session_id)}"

        if cmd in ("show", "list", ""):
            turns = store.get_recent(session_id, n=10)
            if not turns:
                return "No turns in memory yet."
            lines = [f"LAST {len(turns)} TURNS:"]
            for t in turns:
                role = "YOU" if t["role"] == "user" else "BOT"
                c = t["content"]
                if len(c) > PREVIEW_CAP:
                    c = c[:PREVIEW_CAP - 1] + "…"
                lines.append("")
                lines.append(f"{role}:")
                lines.append(c)
            return "\n".join(lines)

        return "Subcommands: show | count | clear"


register(MemorySkill())
