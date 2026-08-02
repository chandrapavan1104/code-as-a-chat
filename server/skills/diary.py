"""
diary skill — your personal diary + life mentor, "Anna" (elder brother).

A deliberately DIFFERENT persona from the shell agent: no hype, no memes.
Anna is the strict-but-loving elder brother — listens, remembers, questions
your decisions, calls out patterns, scolds when you deserve it, and pushes
accountability. Health, finance, love life, desires, future planning.

All entries live locally in ~/.codeasachat/diary.db. Entry text is sent to
the Claude API only to generate Anna's reply (same as every LLM call here).

Subcommands (passed via prompt):
  <free text>                talk to Anna — stored as a diary entry, he replies
  recent | show              last entries (both sides of the conversation)
  health|finance|love|career|desires|future|general
                             read back one category
  search <query>             find past entries
  review                     Anna's honest weekly review of the last 7 days
  stats                      entry counts per category
"""

import datetime as dt
import time

from server import config
from server.skills.base import Skill
from server.skills import register
from server.skills.shell import _haiku, _parse_json_decision, _salvage_reply
from server.db import diary_store as store


CONTEXT_ENTRIES = 14       # how much past conversation Anna sees per turn
ENTRY_TRUNCATE = 400       # cap per context entry


ANNA_SYSTEM = """\
You are Anna — the user's elder brother and life mentor, inside their private diary.

You receive past diary context and a NEW ENTRY. You reply as Anna.

Output ONLY one single-line JSON object (escape newlines in strings as \\n):
{"category":"health|finance|love|career|desires|future|general","reply":"<your reply>"}
"category" = the best fit for the NEW ENTRY.

WHO YOU ARE:
- Telugu elder brother. Tinglish is natural (anna, ra, chudu, artham chesko)
  but grounded and mature — NO memes, NO hype words, minimal emojis.
- A MENTOR, not a cheerleader. You never flatter. Praise is rare and earned,
  one line at most.
- Direct, honest, judgmental in the way family is: you question bad decisions,
  you say "idi tappu ra" when it's wrong, you scold firmly when deserved —
  always from love, never cruel, never insulting.
- MEMORY IS YOUR POWER: use the past context. Call out contradictions and
  repeated patterns explicitly ("Two weeks back you said you'd stop this.
  This is the third time."). Hold them to their own words.
- Push accountability: end with a pointed question or a concrete ask —
  what will they DO, by when. Small commitments, not lectures.
- Money: be the conservative voice. Question impulse spends, push savings.
- Love/relationships: listen first, judge actions not feelings, be honest
  even when it's uncomfortable.
- Health: take it seriously. Sleep, food, exercise — no excuses culture.
- WHEN TO SOFTEN: real pain, grief, fear, mental-health struggles — drop all
  strictness instantly. Be the brother who sits next to them first. For
  serious medical, legal, or large financial matters, say plainly that they
  should see a professional — you are a brother, not a doctor or advisor.
- Keep replies under ~150 words. Plain text, short lines, Telegram-friendly.
  No markdown bold/headers. Facts, dates, and amounts from context stay exact.
"""


REVIEW_INSTRUCTION = """\
The user asked for their WEEKLY REVIEW. Based on the diary entries from the
last 7 days (in context), write Anna's honest weekly assessment:
- What actually moved forward, in one or two lines.
- Patterns you don't like — name them bluntly, with evidence from the entries.
- Broken commitments — quote them back.
- One clear priority for next week, and one pointed question.
Same output format: {"category":"general","reply":"..."}
"""


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _context_block() -> str:
    rows = store.recent(CONTEXT_ENTRIES)
    if not rows:
        return "(diary is empty — this is the user's first entry)"
    lines = []
    for r in rows:
        who = "USER" if r["role"] == "user" else "ANNA"
        text = r["content"]
        if len(text) > ENTRY_TRUNCATE:
            text = text[:ENTRY_TRUNCATE - 1] + "…"
        lines.append(f"[{_when(r['created_at'])}] [{r['category']}] {who}: {text}")
    return "\n".join(lines)


async def _converse(text: str, instruction: str | None = None) -> str:
    user_block = (
        f"PAST DIARY CONTEXT:\n{_context_block()}\n\n"
        f"{instruction or ''}\n"
        f"NEW ENTRY ({dt.datetime.now().strftime('%A %Y-%m-%d %H:%M')}):\n{text}"
    )

    try:
        raw = await _haiku(ANNA_SYSTEM, user_block, timeout=90,
                           model=config.DIARY_MODEL, task="diary")
    except Exception as exc:
        # Never lose a diary entry to an LLM failure
        store.add("user", "general", text)
        return f"(Entry saved. Anna couldn't reply right now: {exc})"

    data = _parse_json_decision(raw)
    if data and data.get("reply"):
        category = data.get("category", "general")
        reply = data["reply"].strip()
    else:
        category = "general"
        reply = (_salvage_reply(raw) or raw or "").strip() \
            or "(Entry saved. Anna had no words this time.)"

    store.add("user", category, text)
    store.add("anna", category, reply)
    return reply


def _format_entries(rows: list[dict], header: str) -> str:
    if not rows:
        return f"{header}: (nothing here yet)"
    lines = [header, ""]
    for r in rows:
        who = "You" if r["role"] == "user" else "Anna"
        text = r["content"]
        if len(text) > 300:
            text = text[:297] + "…"
        lines.append(f"[{_when(r['created_at'])}] [{r['category']}] {who}:")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip()


class DiarySkill(Skill):
    name = "diary"
    aliases = ["anna"]
    passthrough = True
    description = ("Personal diary + life mentor (Anna): health, finance, love, "
                   "future. Free text = talk to Anna; recent | <category> | "
                   "search <q> | review | stats")
    agent_doc = """The user's PRIVATE DIARY + life mentor ("Anna", their elder brother — a
   separate strict-mentor persona, not you). Route here whenever the user shares or asks about
   PERSONAL LIFE topics: health, fitness, sleep, money/spending/savings, love life, relationships,
   feelings, desires, life plans, future decisions, self-reflection. Also when they say "diary",
   "anna", "personal note", or ask for a life "review". Pass the user's words VERBATIM as args —
   do not summarize. Its reply goes to the user untouched (different persona — never reword it).
    args: "<user's full text>" (talk to Anna) | "recent" | "review" |
          "health"|"finance"|"love"|"career"|"desires"|"future" | "search <q>" | "stats\""""

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        raw = prompt.strip()
        if not raw:
            counts = store.counts()
            total = sum(counts.values())
            if not total:
                return ("This is your private diary — Anna is listening.\n"
                        "Health, money, love, plans, anything. Just write.\n\n"
                        "Also: /diary recent · /diary review · /diary <category>")
            cat_line = "  ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
            return (f"DIARY — {total} entries ({cat_line})\n\n"
                    "Write anything to talk to Anna.\n"
                    "Or: recent · review · search <q> · " + " · ".join(sorted(store.CATEGORIES)))

        first = raw.split()[0].lower()
        rest = raw.split(None, 1)[1].strip() if len(raw.split(None, 1)) > 1 else ""

        if first in ("recent", "show", "list", "log"):
            return _format_entries(store.recent(12), "RECENT DIARY")

        if first in store.CATEGORIES:
            return _format_entries(store.by_category(first, 10),
                                   f"DIARY — {first.upper()}")

        if first == "search":
            if not rest:
                return "Usage: /diary search <query>"
            return _format_entries(store.search(rest), f"DIARY SEARCH '{rest}'")

        if first == "stats":
            counts = store.counts()
            if not counts:
                return "Diary is empty."
            lines = ["DIARY STATS (your entries):"]
            for k in sorted(counts, key=counts.get, reverse=True):
                lines.append(f"  {k:<9} {counts[k]}")
            return "\n".join(lines)

        if first == "review":
            week_ago = time.time() - 7 * 86400
            week = store.since(week_ago)
            if not week:
                return "Nothing in the diary this week. Anna can't review silence ra — write something first."
            return await _converse("(weekly review requested)", instruction=REVIEW_INSTRUCTION)

        # Anything else = a diary entry / conversation with Anna
        return await _converse(raw)


register(DiarySkill())
