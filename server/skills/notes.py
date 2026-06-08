"""
notes skill — your phone whiteboard.

Frictionless capture: you type a thought in natural language, the skill calls
Haiku once to extract (kind, project, title, body, tags), and stores it.
Project gets auto-matched against /projects so notes tag themselves.

Subcommands (passed via prompt):
  (empty) | list                  list recent OPEN notes (any project, any kind)
  capture <text>                  add a note — LLM extracts project + kind + tags
  add <text> | new <text>         aliases for capture
  show <id>                       view a single note in full
  done <id>                       mark closed (status=done)
  drop <id>                       drop (status=dropped)
  reopen <id>                     status=open
  delete <id>                     permanent delete
  search <query>                  full-text LIKE on title/body/tags
  stats                           counts by project × kind × status
  <kind>                          filter by kind: bug|feature|idea|todo|note|question
                                     (plural forms also work: bugs|features|…)
  <project-name>                  filter to a known project (substring ok)
"""

import json
import time
from server.skills.base import Skill
from server.skills import register
from server.skills.shell import _haiku                       # reuse haiku subprocess helper
from server.skills.projects import _candidates as _project_candidates
from server.db import notes_store as store


# ── constants ─────────────────────────────────────────────────────────────────

KINDS = {"bug", "feature", "idea", "todo", "note", "question"}
DEFAULT_KIND = "note"
LIST_LIMIT = 15
PLURAL_KIND = {
    "bugs": "bug", "features": "feature", "ideas": "idea",
    "todos": "todo", "notes": "note", "questions": "question",
}


# ── LLM extraction ────────────────────────────────────────────────────────────

CAPTURE_SYSTEM_TEMPLATE = """\
You are extracting structured notes from the user's free-form thought.

Known projects (pick exact name from this list, or null):
{project_list}

Output ONLY a single JSON object on one line. No prose, no markdown fences.

TWO valid shapes — pick based on input:

SHAPE A — single note (one cohesive thought, bug, idea, or todo):
{
  "kind":    "bug" | "feature" | "idea" | "todo" | "note" | "question",
  "project": "<exact name from list above>" | null,
  "title":   "<5-10 word plain-text summary>",
  "body":    "<the user's content, lightly cleaned>",
  "tags":    ["tag1", "tag2"]
}

SHAPE B — multiple notes (brain-dump covering 2+ distinct topics or 2+ named
projects — each item gets its own note):
{
  "notes": [
    { "kind", "project", "title", "body", "tags" },
    { "kind", "project", "title", "body", "tags" }
  ]
}

How to decide:
- Single thought → SHAPE A.
- User lists multiple projects with what's pending on each → SHAPE B, one note
  per project.
- User dumps multiple unrelated ideas → SHAPE B, one note each.

Rules (apply to every note):
- "kind" defaults to "note" if unclear. Use "bug" for problems / things broken,
  "feature" or "idea" for additions/improvements (prefer "idea" for blue-sky,
  "feature" when scoped to a known project), "todo" for action items,
  "question" for things the user wants to figure out.
- "project" is null unless the user explicitly named a project that matches the
  list. Case-insensitive, substring matching is OK — but only return the exact
  name from the list, never invent one.
- Title is plain text, 5-10 words, no markdown.
- Body is the user's words for that topic, lightly cleaned (drop preamble like
  "I think", "remember", "Chandu:").
- Tags are optional. Only include when clearly relevant (e.g. "ui", "perf",
  "auth", "mobile"). Keep them lowercase, single-word.
"""


def _project_list_for_prompt() -> str:
    cands = _project_candidates()
    if not cands:
        return "(no projects configured)"
    return "\n".join(f"- {c.name}" for c in cands)


def _parse_json_decision(raw: str) -> dict | None:
    if not raw:
        return None
    brace = raw.find("{")
    if brace < 0:
        return None
    try:
        return json.loads(raw[brace:])
    except json.JSONDecodeError:
        depth = 0
        for i, ch in enumerate(raw[brace:], start=brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[brace:i + 1])
                    except json.JSONDecodeError:
                        return None
        return None


def _match_project(name: str | None) -> str | None:
    if not name:
        return None
    name_l = name.lower().strip()
    cands = _project_candidates()
    for c in cands:
        if c.name.lower() == name_l:
            return c.name
    matches = [c.name for c in cands if name_l in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


# ── formatting ────────────────────────────────────────────────────────────────

def _relative(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    days = int(diff / 86400)
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _row(n: dict) -> str:
    proj = n["project"] or "—"
    status_marker = "" if n["status"] == "open" else f" [{n['status']}]"
    return (f"#{n['id']:<3} {n['kind']:<7} · {proj} · {_relative(n['updated_at'])}{status_marker}\n"
            f"   {n['title']}")


def _format_list(notes: list[dict], header: str) -> str:
    if not notes:
        return f"{header}: (none)"
    lines = [f"{header}  ({len(notes)})", ""]
    for n in notes:
        lines.append(_row(n))
        lines.append("")
    lines += [
        "USAGE:",
        "• /notes show <id>     full body",
        "• /notes done <id>     mark done",
        "• /notes drop <id>     drop",
    ]
    return "\n".join(lines)


def _format_full(n: dict) -> str:
    tags_line = f"\nTags: {n['tags']}" if n.get("tags") else ""
    return (
        f"#{n['id']}  [{n['kind']}]  ({n['status']})\n"
        f"Project: {n['project'] or '—'}\n"
        f"Created: {_relative(n['created_at'])}    "
        f"Updated: {_relative(n['updated_at'])}{tags_line}\n\n"
        f"{n['title']}\n\n"
        f"{n['body']}"
    )


# ── capture (the smart bit) ───────────────────────────────────────────────────

def _insert_extracted(d: dict, source_session: str | None,
                      raw_fallback: str = "") -> tuple[int, dict]:
    """Insert one extracted note dict. Returns (id, normalized_dict)."""
    kind = (d.get("kind") or DEFAULT_KIND).lower()
    if kind not in KINDS:
        kind = DEFAULT_KIND

    project = _match_project(d.get("project"))
    title = (d.get("title") or raw_fallback[:80] or "(untitled)").strip()
    body = (d.get("body") or raw_fallback or title).strip()

    tags_raw = d.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t).strip().lower() for t in tags_raw if str(t).strip()]
    else:
        tags = []

    nid = store.add(
        project=project, kind=kind, title=title, body=body, tags=tags,
        source_session=source_session,
    )
    return nid, {"kind": kind, "project": project, "title": title, "tags": tags}


async def _capture(text: str, session_id: str | None) -> str:
    text = text.strip()
    if not text:
        return "Usage: /notes capture <your thought>"

    system = CAPTURE_SYSTEM_TEMPLATE.replace("{project_list}", _project_list_for_prompt())
    user_msg = f"User said: {text}\n\nExtract the structured note(s)."

    extracted = None
    try:
        raw = await _haiku(system, user_msg, timeout=45)
        extracted = _parse_json_decision(raw)
    except Exception:
        pass

    if not extracted:
        # Fallback: store as plain note
        nid, _ = _insert_extracted({}, source_session=session_id, raw_fallback=text)
        return (f"✓ Captured #{nid}  (plain note — LLM couldn't structure)\n"
                f"   {text[:80]}")

    # ── multi-note shape ─────────────────────────────────────────────────────
    if "notes" in extracted and isinstance(extracted["notes"], list) \
       and extracted["notes"]:
        created: list[tuple[int, dict]] = []
        for note_data in extracted["notes"]:
            if not isinstance(note_data, dict):
                continue
            nid, info = _insert_extracted(note_data, source_session=session_id,
                                          raw_fallback=text)
            created.append((nid, info))

        if not created:
            # Fall back to single
            nid, _ = _insert_extracted({}, source_session=session_id, raw_fallback=text)
            return f"✓ Captured #{nid} (multi-extract returned empty list)"

        lines = [f"✓ Captured {len(created)} notes:", ""]
        for nid, info in created:
            proj = info["project"] or "—"
            lines.append(f"  #{nid}  {info['kind']:<7} · {proj}  →  {info['title']}")
        return "\n".join(lines)

    # ── single-note shape ────────────────────────────────────────────────────
    nid, info = _insert_extracted(extracted, source_session=session_id,
                                  raw_fallback=text)
    proj_label = info["project"] or "—"
    tag_line = f"\nTags: {', '.join(info['tags'])}" if info["tags"] else ""
    return (
        f"✓ Captured #{nid}\n"
        f"Kind: {info['kind']} · Project: {proj_label}\n"
        f"Title: {info['title']}{tag_line}"
    )


# ── /notes wipe — bulk delete ─────────────────────────────────────────────────

def _parse_wipe_targets(s: str) -> list[int]:
    """Parse 'all' | '1 2 3' | '1-6' (or mix) into a list of note IDs."""
    s = s.strip().lower()
    if not s:
        return []
    if s == "all":
        rows = store.list_notes(status=None, limit=10_000)
        return [r["id"] for r in rows]

    targets: list[int] = []
    for token in s.replace(",", " ").split():
        token = token.lstrip("#")
        if "-" in token:
            try:
                lo_s, hi_s = token.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                targets.extend(range(lo, hi + 1))
            except ValueError:
                continue
        else:
            try:
                targets.append(int(token))
            except ValueError:
                continue
    return targets


# ── helpers for parsing IDs ───────────────────────────────────────────────────

def _parse_id(s: str) -> int | None:
    s = s.strip().lstrip("#")
    try:
        return int(s)
    except ValueError:
        return None


# ── skill ─────────────────────────────────────────────────────────────────────

class NotesSkill(Skill):
    name = "notes"
    description = ("Personal whiteboard — capture / browse / track ideas, bugs, "
                   "todos, with auto project tagging")

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        args = prompt.strip().split()
        if not args:
            return _format_list(store.list_notes(limit=LIST_LIMIT), "OPEN NOTES")

        cmd = args[0].lower()
        rest = " ".join(args[1:]).strip()

        # ── browsing ─────────────────────────────────────────────────────────
        if cmd in ("list", "ls"):
            return _format_list(store.list_notes(limit=LIST_LIMIT), "OPEN NOTES")

        if cmd == "all":
            return _format_list(store.list_notes(status=None, limit=LIST_LIMIT),
                                "ALL NOTES")

        if cmd == "show":
            nid = _parse_id(rest)
            if nid is None:
                return "Usage: /notes show <id>"
            n = store.get(nid)
            return _format_full(n) if n else f"No note #{nid}"

        if cmd == "search":
            if not rest:
                return "Usage: /notes search <query>"
            return _format_list(store.search(rest, limit=LIST_LIMIT),
                                f"SEARCH '{rest}'")

        if cmd == "stats":
            rows = store.stats()
            if not rows:
                return "No notes yet."
            by_project: dict[str, list[dict]] = {}
            for r in rows:
                by_project.setdefault(r["project"] or "—", []).append(r)
            lines = ["NOTES STATS", ""]
            for proj in sorted(by_project):
                lines.append(f"{proj}")
                for r in by_project[proj]:
                    lines.append(f"  {r['kind']:<8} {r['status']:<8} {r['c']}")
                lines.append("")
            return "\n".join(lines).rstrip()

        if cmd == "kinds":
            return "Kinds: " + ", ".join(sorted(KINDS))

        # ── capture (explicit) ───────────────────────────────────────────────
        if cmd in ("capture", "add", "new", "remember", "jot", "+"):
            return await _capture(rest, session_id=session_id)

        # ── state changes ────────────────────────────────────────────────────
        if cmd in ("done", "close", "complete", "✓"):
            nid = _parse_id(rest)
            if nid is None:
                return "Usage: /notes done <id>"
            return f"#{nid} marked done." if store.set_status(nid, "done") \
                else f"No note #{nid}"

        if cmd in ("drop", "dismiss", "skip"):
            nid = _parse_id(rest)
            if nid is None:
                return "Usage: /notes drop <id>"
            return f"#{nid} dropped." if store.set_status(nid, "dropped") \
                else f"No note #{nid}"

        if cmd in ("reopen", "open"):
            nid = _parse_id(rest)
            if nid is None:
                return "Usage: /notes reopen <id>"
            return f"#{nid} reopened." if store.set_status(nid, "open") \
                else f"No note #{nid}"

        if cmd in ("delete", "del", "rm"):
            nid = _parse_id(rest)
            if nid is None:
                return "Usage: /notes delete <id>"
            return f"#{nid} deleted." if store.delete(nid) \
                else f"No note #{nid}"

        if cmd in ("wipe", "bulk-delete"):
            if not rest:
                return ("Usage (explicit on purpose, no accidental wipes):\n"
                        "  /notes wipe all          delete every note\n"
                        "  /notes wipe 1 2 5        delete specific IDs\n"
                        "  /notes wipe 1-6          delete a range")
            targets = _parse_wipe_targets(rest)
            if not targets:
                return f"Couldn't parse '{rest}'. Try: all | 1 2 3 | 1-6"
            deleted = sum(1 for nid in targets if store.delete(nid))
            return f"Wiped {deleted} of {len(targets)} target(s)."

        if cmd == "edit":
            sub = rest.split(None, 1)
            if len(sub) < 2:
                return "Usage: /notes edit <id> <new body>"
            nid = _parse_id(sub[0])
            if nid is None:
                return "Usage: /notes edit <id> <new body>"
            return f"#{nid} updated." if store.update_body(nid, sub[1]) \
                else f"No note #{nid}"

        # ── filter by kind ───────────────────────────────────────────────────
        kind = PLURAL_KIND.get(cmd, cmd)
        if kind in KINDS:
            return _format_list(store.list_notes(kind=kind, limit=LIST_LIMIT),
                                f"{kind.upper()}S")

        # ── filter by project ────────────────────────────────────────────────
        matched = _match_project(prompt.strip())
        if matched:
            return _format_list(store.list_notes(project=matched, limit=LIST_LIMIT),
                                f"NOTES — {matched}")

        # ── nothing matched → show help with what we got ─────────────────────
        return (
            "Subcommands:\n"
            "  /notes                       list open notes\n"
            "  /notes capture <text>        new note (LLM auto-tags; "
            "splits brain-dumps into per-topic notes)\n"
            "  /notes <kind>                filter (bugs|features|ideas|todos|questions)\n"
            "  /notes <project>             filter by project name\n"
            "  /notes show <id>             view in full\n"
            "  /notes done|drop|reopen <id> change status\n"
            "  /notes search <query>        full-text search\n"
            "  /notes stats                 counts by project/kind\n"
            "  /notes wipe all|1-6|1 2 3    bulk delete\n"
            "\n"
            f"Got: '{prompt.strip()}' — didn't match a known kind or project."
        )


register(NotesSkill())
