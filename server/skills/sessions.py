"""
sessions skill — browse past chats stored by the three CLIs.

Subcommands (passed via prompt):
  (empty)                    list recent sessions across all engines
  list                       same as empty
  claude | codex | gemini    list sessions from one engine only
  show <id-prefix>           show the conversation in a session
  count                      counts per engine

Storage formats parsed:
  Claude:  ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
  Codex:   ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl
  Gemini:  ~/.gemini/tmp/<project>/chats/session-<ts>-<id>.jsonl

Robust against natural-language confusion from the shell router: a non-hex
argument like "me" or "my chats" falls through to a list view rather than
returning an error.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from server.skills.base import Skill
from server.skills import register


CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
GEMINI_DIR = Path.home() / ".gemini"

MAX_LIST = 15
PREVIEW_LEN = 80
TURNS_IN_SHOW = 3
TURN_CHAR_CAP = 500


# ── helpers ───────────────────────────────────────────────────────────────────

def _preview(text: str, n: int = PREVIEW_LEN) -> str:
    text = " ".join((text or "").split())
    return text[:n - 1] + "…" if len(text) > n else text


def _short_id(uuid: str, n: int = 8) -> str:
    return (uuid or "?")[:n]


def _when(mtime: float) -> str:
    return datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")


def _trunc(s: str, n: int = TURN_CHAR_CAP) -> str:
    return s[:n - 1] + "…" if len(s) > n else s


def _read_jsonl(path: Path):
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        return


# ── per-engine listers ────────────────────────────────────────────────────────

def _list_claude() -> list[dict]:
    out = []
    if not CLAUDE_PROJECTS.exists():
        return out
    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            first_msg = None
            cwd = None
            for ev in _read_jsonl(jsonl):
                if not cwd:
                    cwd = ev.get("cwd")
                if ev.get("type") == "user":
                    msg = ev.get("message", {})
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        first_msg = content
                        break
            out.append({
                "engine": "claude",
                "id": jsonl.stem,
                "mtime": mtime,
                "cwd": cwd or project_dir.name,
                "preview": _preview(first_msg or "(no user msg)"),
                "path": str(jsonl),
            })
    return out


def _list_codex() -> list[dict]:
    out = []
    if not CODEX_SESSIONS.exists():
        return out
    for jsonl in CODEX_SESSIONS.rglob("rollout-*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        sid = cwd = first_msg = None
        for ev in _read_jsonl(jsonl):
            t = ev.get("type")
            if t == "session_meta":
                payload = ev.get("payload", {})
                sid = sid or payload.get("id")
                cwd = cwd or payload.get("cwd")
            elif t == "response_item":
                p = ev.get("payload", {})
                if p.get("type") == "message" and p.get("role") == "user":
                    for c in p.get("content", []):
                        text = c.get("text", "")
                        if text and "<environment_context>" not in text:
                            first_msg = text
                            break
                    if first_msg:
                        break
        if sid:
            out.append({
                "engine": "codex",
                "id": sid,
                "mtime": mtime,
                "cwd": cwd or "?",
                "preview": _preview(first_msg or "(no user msg)"),
                "path": str(jsonl),
            })
    return out


def _list_gemini() -> list[dict]:
    out = []
    if not GEMINI_DIR.exists():
        return out
    for jsonl in GEMINI_DIR.glob("tmp/*/chats/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        sid = first_msg = None
        for ev in _read_jsonl(jsonl):
            if not sid and "sessionId" in ev:
                sid = ev["sessionId"]
            if ev.get("type") == "user":
                for c in ev.get("content", []):
                    text = c.get("text", "")
                    if text:
                        first_msg = text
                        break
                if first_msg:
                    break
        if not sid:
            continue
        try:
            proj = jsonl.parts[jsonl.parts.index("tmp") + 1]
        except (ValueError, IndexError):
            proj = "?"
        out.append({
            "engine": "gemini",
            "id": sid,
            "mtime": mtime,
            "cwd": f"~/{proj}",
            "preview": _preview(first_msg or "(no user msg)"),
            "path": str(jsonl),
        })
    return out


def _all_sessions() -> list[dict]:
    return _list_claude() + _list_codex() + _list_gemini()


# ── formatting ────────────────────────────────────────────────────────────────

def _format_row(s: dict) -> str:
    cwd_short = s["cwd"]
    home = str(Path.home())
    if cwd_short.startswith(home):
        cwd_short = "~" + cwd_short[len(home):]
    if len(cwd_short) > 40:
        cwd_short = "…" + cwd_short[-39:]
    return (
        f"[{s['engine'].upper():<6}] {_short_id(s['id'])}  {_when(s['mtime'])}\n"
        f"  {cwd_short}\n"
        f"  {s['preview']}"
    )


def _list_view(engine_filter: str | None = None) -> str:
    sessions = _all_sessions()
    if engine_filter:
        sessions = [s for s in sessions if s["engine"] == engine_filter]
    if not sessions:
        return "No sessions found."

    sessions.sort(key=lambda s: s["mtime"], reverse=True)

    counts: dict[str, int] = {}
    for s in sessions:
        counts[s["engine"]] = counts.get(s["engine"], 0) + 1
    count_str = "  ".join(f"{e}:{n}" for e, n in counts.items())

    lines = [f"SESSIONS ({len(sessions)} total — {count_str})", ""]
    lines.extend(_format_row(s) for s in sessions[:MAX_LIST])
    if len(sessions) > MAX_LIST:
        lines.append(f"\n…{len(sessions) - MAX_LIST} more")
    lines.append("")
    lines.append("USAGE:")
    lines.append("• /sessions <engine>     filter")
    lines.append("• /sessions show <id>    view chat")
    return "\n".join(lines)


def _count_view() -> str:
    sessions = _all_sessions()
    counts: dict[str, int] = {}
    for s in sessions:
        counts[s["engine"]] = counts.get(s["engine"], 0) + 1
    if not counts:
        return "No sessions found."
    return "SESSION COUNTS:\n" + "\n".join(f"• {e}: {n}" for e, n in counts.items())


# ── show ──────────────────────────────────────────────────────────────────────

def _find_session(prefix: str) -> dict | None:
    prefix = prefix.lower().strip().lstrip("/")
    for s in _all_sessions():
        if s["id"].lower().startswith(prefix):
            return s
    return None


def _extract_turns_claude(path: Path) -> list[tuple[str, str]]:
    turns = []
    cur_user = None
    cur_assistant_parts: list[str] = []
    for ev in _read_jsonl(path):
        t = ev.get("type")
        msg = ev.get("message", {})
        if t == "user" and isinstance(msg.get("content"), str):
            if cur_user is not None:
                turns.append((cur_user, "\n".join(cur_assistant_parts).strip()))
            cur_user = msg["content"]
            cur_assistant_parts = []
        elif t == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        cur_assistant_parts.append(c.get("text", ""))
    if cur_user is not None:
        turns.append((cur_user, "\n".join(cur_assistant_parts).strip()))
    return turns


def _extract_turns_codex(path: Path) -> list[tuple[str, str]]:
    turns = []
    cur_user = None
    cur_assistant_parts: list[str] = []
    for ev in _read_jsonl(path):
        if ev.get("type") != "response_item":
            continue
        p = ev.get("payload", {})
        if p.get("type") != "message":
            continue
        role = p.get("role")
        texts = [
            c.get("text", "") for c in p.get("content", [])
            if c.get("text") and "<environment_context>" not in c.get("text", "")
        ]
        combined = "\n".join(texts).strip()
        if not combined:
            continue
        if role == "user":
            if cur_user is not None:
                turns.append((cur_user, "\n".join(cur_assistant_parts).strip()))
            cur_user = combined
            cur_assistant_parts = []
        elif role == "assistant":
            cur_assistant_parts.append(combined)
    if cur_user is not None:
        turns.append((cur_user, "\n".join(cur_assistant_parts).strip()))
    return turns


def _gemini_content_text(content) -> str:
    """Gemini events sometimes store content as a string, sometimes as [{'text': ...}]."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("text"):
                parts.append(c["text"])
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts).strip()
    return ""


def _extract_turns_gemini(path: Path) -> list[tuple[str, str]]:
    turns = []
    cur_user = None
    cur_assistant_parts: list[str] = []
    for ev in _read_jsonl(path):
        t = ev.get("type")
        if t not in ("user", "gemini", "model"):
            continue
        combined = _gemini_content_text(ev.get("content"))
        if not combined:
            continue
        if t == "user":
            if cur_user is not None:
                turns.append((cur_user, "\n".join(cur_assistant_parts).strip()))
            cur_user = combined
            cur_assistant_parts = []
        else:  # "gemini" or "model"
            cur_assistant_parts.append(combined)
    if cur_user is not None:
        turns.append((cur_user, "\n".join(cur_assistant_parts).strip()))
    return turns


_HEX_ID = re.compile(r"^[a-f0-9-]{4,}$", re.IGNORECASE)


def _show_view(prefix: str) -> str:
    if not prefix:
        return "Usage: /sessions show <id-prefix>  (use /sessions to see IDs)"

    # Defensive: if it doesn't look like a UUID prefix, the caller (probably the
    # shell router) misclassified a natural-language query. Show the list view.
    if not _HEX_ID.match(prefix):
        return (f"'{prefix}' isn't a session id — showing recent list instead.\n\n"
                + _list_view())

    s = _find_session(prefix)
    if s is None:
        return f"No session matches prefix '{prefix}'. Run /sessions to list IDs."

    path = Path(s["path"])
    engine = s["engine"]
    if engine == "claude":
        turns = _extract_turns_claude(path)
    elif engine == "codex":
        turns = _extract_turns_codex(path)
    elif engine == "gemini":
        turns = _extract_turns_gemini(path)
    else:
        return f"Unknown engine: {engine}"

    if not turns:
        return f"Session {_short_id(s['id'])} has no readable turns."

    shown = turns[-TURNS_IN_SHOW:]
    skipped = len(turns) - len(shown)

    lines = [
        f"SESSION {_short_id(s['id'])}  [{engine.upper()}]",
        f"When: {_when(s['mtime'])}    Dir: {s['cwd']}",
        f"Turns: {len(turns)}  (showing last {len(shown)})",
        "",
    ]
    if skipped:
        lines.append(f"… {skipped} earlier turn(s) hidden")
        lines.append("")

    for i, (user_msg, asst_msg) in enumerate(shown, start=1):
        idx = len(turns) - len(shown) + i
        lines.append(f"── turn {idx} ──")
        lines.append("YOU:")
        lines.append(_trunc(user_msg))
        lines.append("")
        lines.append(f"{engine.upper()}:")
        lines.append(_trunc(asst_msg) if asst_msg else "(no response captured)")
        lines.append("")

    return "\n".join(lines).rstrip()


# ── skill ─────────────────────────────────────────────────────────────────────

class SessionsSkill(Skill):
    name = "sessions"
    description = "Browse past chats: /sessions | /sessions <engine> | /sessions show <id>"
    final_output = True
    agent_doc = ('Browse past chats from claude/codex/gemini. '
                 'args: "" (list recent) | "claude"|"codex"|"gemini" (filter) | '
                 '"show <id-prefix>" | "count"')

    async def run(self, prompt: str = "", **kwargs) -> str:
        args = prompt.strip().split()
        if not args:
            return _list_view()

        cmd = args[0].lower()

        if cmd in ("list", "ls"):
            return _list_view(args[1].lower() if len(args) > 1 else None)
        if cmd in ("claude", "codex", "gemini"):
            return _list_view(cmd)
        if cmd == "show":
            return _show_view(args[1] if len(args) > 1 else "")
        if cmd == "count":
            return _count_view()

        # Fallback: treat the whole arg as an id-prefix to show
        return _show_view(cmd)


register(SessionsSkill())
