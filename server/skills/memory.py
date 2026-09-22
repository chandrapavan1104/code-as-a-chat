"""
memory skill — inspect and manage the shell's conversation memory.

Subcommands:
  show | list | (empty)   show the last 10 turns of THIS conversation
  search <text>           find matching messages in THIS conversation
  search all <text>       find across this client's project conversations
  get <id>                show one complete stored message by id
  count                   how many turns are in memory
  clear | forget | reset  wipe memory for THIS conversation
"""

from server.skills.base import Skill
from server.skills import register
from server.db import store


PREVIEW_CAP = 200


class MemorySkill(Skill):
    name = "memory"
    description = "Search and manage exact conversation memory: show | search | get | count | clear"
    agent_doc = (
        "Search the user's own conversation memory and retrieve exact saved text. "
        "Use for finding prior drafts, decisions, or original wording. "
        "args: search <words> (current conversation), search all <words> "
        "(same client across projects), get <message id> (full exact message), "
        "show, count, clear. Search results are previews; use get for the full text."
    )

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        if not session_id:
            return ("This skill needs a session_id — call it from a client "
                    "that supplies one (e.g. the Telegram bot).")

        parts = prompt.strip().split()
        cmd = (parts[:1] or ["show"])[0].lower()

        if cmd in ("clear", "forget", "reset", "wipe"):
            n = store.clear(session_id)
            return f"Cleared {n} turn(s) from this conversation."

        if cmd == "count":
            return f"Turns in this conversation: {store.count(session_id)}"

        if cmd in ("search", "find"):
            if len(parts) < 2:
                return "Usage: search <words> [page N] [limit N] or search all <words>"
            all_client = parts[1].lower() in {"all", "client", "projects"}
            start = 2 if all_client else 1
            words = []
            offset = 0
            limit = 20
            i = start
            while i < len(parts):
                if parts[i].lower() in {"page", "offset"} and i + 1 < len(parts):
                    try:
                        offset = max(0, (int(parts[i + 1]) - 1) * limit) if parts[i].lower() == "page" else max(0, int(parts[i + 1]))
                        i += 2
                        continue
                    except ValueError:
                        pass
                if parts[i].lower() == "limit" and i + 1 < len(parts):
                    try:
                        limit = min(100, max(1, int(parts[i + 1])))
                        i += 2
                        continue
                    except ValueError:
                        pass
                words.append(parts[i])
                i += 1
            query = " ".join(words).strip()
            if not query:
                return "Usage: search <words> [page N] [limit N] or search all <words>"
            matches = store.search(session_id, query, limit=limit, offset=offset,
                                   all_client=all_client)
            if not matches:
                return f"No memory matches for: {query}"
            scope = "client conversations" if all_client else "this conversation"
            lines = [f"MEMORY MATCHES ({scope}, page {offset // limit + 1}):"]
            for item in matches:
                preview = item["content"].replace("\n", " ")
                if len(preview) > PREVIEW_CAP:
                    preview = preview[:PREVIEW_CAP - 1] + "…"
                project = f" [{item['session_id']}]" if all_client else ""
                lines += [f"#{item['id']} {item['role'].upper()}{project}:", preview]
            lines.append("Use memory get <id> for the complete exact message.")
            return "\n".join(lines)

        if cmd in ("get", "exact", "message"):
            all_client = len(parts) == 3 and parts[1].lower() in {"all", "client", "projects"}
            id_part = parts[2] if all_client else (parts[1] if len(parts) == 2 else "")
            if not id_part.isdigit():
                return "Usage: get <message id> or get all <message id>"
            item = store.get_message(int(id_part), session_id, all_client=all_client)
            if not item:
                return "That message is not in this conversation. Use search all to find another project message."
            return f"MESSAGE #{item['id']} ({item['role']}):\n{item['content']}"

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
