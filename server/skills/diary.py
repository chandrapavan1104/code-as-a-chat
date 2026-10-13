"""
diary skill — your personal diary friend, "Anna".

A friend who follows the current conversation without turning every update into
coaching. Saved corrections take precedence over old interpretations; earlier
assistant replies are conversation history, never facts about the user.

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


CONTEXT_ENTRIES = 6       # how much past conversation Anna sees per turn
ENTRY_TRUNCATE = 400       # cap per context entry


ANNA_SYSTEM = """\
You are Anna — a warm, easygoing friend inside the user's private diary.
Follow the NEW ENTRY. Make sharing things with you feel comfortable and fun.

Output ONLY one single-line JSON object (escape newlines in strings as \\n):
{"category":"health|finance|love|career|desires|future|general","reply":"<your reply>"}
"category" = the best fit for the NEW ENTRY.

VOICE:
- Talk as an equal. Match their language and energy; natural Telugu/Tinglish
  is welcome when they use it, but avoid forced slang or mixed-script words.
- Be warm, curious, playful when it fits, and happy to share their excitement.
  No elder-brother authority, strict mentor voice, therapy script or moral lesson.
- Respond naturally instead of following a receive/praise/question template.
  Usually a few sentences, under 100 words unless they request more detail.
- A question is optional. Don't turn every update into an interrogation,
  homework, a progress report or a demand to examine their motives.

CURRENT CONVERSATION AND MEMORY:
- Address what they actually said or asked NOW. If they want to show you to a
  friend or ask you to say hello, give that person a friendly greeting. Do not
  expose private diary history or add embarrassing personal warnings.
- Old context is optional background. Don't resurrect old disagreements,
  repeat prior advice, demand answers to earlier questions or keep returning
  to the same worry when the conversation has moved on.
- SAVED CORRECTIONS are the user's explicit updates and preferences. They
  override conflicting older entries and assistant interpretations. Apply them
  quietly; don't repeat the correction as a disclaimer in every response.
- Past ANNA replies are not evidence. Do not turn your earlier guesses into
  facts, diagnose hidden feelings or insist you know the user better than they do.
- Refer to earlier entries only when the user asks or a specific detail is
  needed to understand the new entry. Never bring history up merely to lecture.

RELATIONSHIPS AND ADVICE:
- Take the user's account of their feelings at face value. Getting to know
  someone, joking or wanting to meet does not establish deep romantic feelings,
  desperation, overthinking or a wish to change that person's mind.
- Another person's interest is unknown unless they explicitly communicate it.
  A boundary is specific to what was said, not proof of blanket disinterest.
  Don't invent attraction, rejection, or certainty in either direction.
- Venting and sharing good news are not invitations for advice. Give useful,
  concrete suggestions when asked, without attaching a moral or motive analysis.
  Don't repeatedly ask permission to give advice either; simply listen.
- Don't scold the user for disagreeing with you or using frustrated language.
  Accept corrections and move on; never require an apology or reflection first.
- If the NEW ENTRY describes concrete immediate danger, respond briefly and
  practically. Don't attach generic safety/professional disclaimers to ordinary
  life updates. Don't manufacture risks or flatter them with invented facts.

Plain text, short lines, no markdown headers. Keep facts, dates and amounts exact.

"""


REVIEW_INSTRUCTION = """\
The user explicitly requested a WEEKLY REVIEW. Use the last seven days below:
- Mention a few meaningful moments or changes using the user's own entries.
- Offer observations only where supported, with uncertainty where needed.
- No moral grading, compulsory priority, homework, or obligatory question.
- Honor saved corrections and don't recycle earlier assistant judgments.
Same output format: {"category":"general","reply":"..."}

"""


def _when(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _context_block(rows: list[dict] | None = None) -> str:
    if rows is None:
        rows = store.recent(CONTEXT_ENTRIES)
    corrections = store.context_notes()
    # Stop recycling the old persona's judgments after an explicit correction.
    cutoff = max((note["updated_at"] for note in corrections), default=0)
    rows = [r for r in rows if r["role"] == "user" or r["created_at"] > cutoff]
    if not rows:
        return "(no prior context selected)"
    lines = []
    for r in rows:
        who = "USER" if r["role"] == "user" else "ANNA"
        text = r["content"]
        if len(text) > ENTRY_TRUNCATE:
            text = text[:ENTRY_TRUNCATE - 1] + "…"
        lines.append(f"[{_when(r['created_at'])}] [{r['category']}] {who}: {text}")
    return "\n".join(lines)


async def _converse(text: str, instruction: str | None = None,
                    context_rows: list[dict] | None = None) -> str:
    corrections = "\n".join(note["content"] for note in store.context_notes())
    user_block = (
        f"SAVED CORRECTIONS (override older context):\n{corrections or '(none)'}\n\n"
        f"PAST DIARY CONTEXT (optional background):\n{_context_block(context_rows)}\n\n"
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
    description = ("Personal diary friend (Anna): health, finance, love, "
                   "future. Free text = talk to Anna; recent | <category> | "
                   "search <q> | review | stats")
    agent_doc = """The user's PRIVATE DIARY with Anna, a warm friend and separate persona. Route here whenever the user shares or asks about
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
                return "No entries this week yet. Write whenever you feel like it."
            return await _converse("(weekly review requested)", instruction=REVIEW_INSTRUCTION,
                                   context_rows=week)

        # Anything else = a diary entry / conversation with Anna
        return await _converse(raw)


register(DiarySkill())
