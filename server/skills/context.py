"""
context skill — universal project context shared across all AI agents.

Each workspace keeps ONE context document, mirrored into the three files the
CLIs auto-load on startup:

    AGENTS.md   ← canonical (Codex + the cross-agent standard)
    CLAUDE.md   ← Claude Code auto-loads this
    GEMINI.md   ← Gemini CLI auto-loads this

Because all three hold identical content, every agent reads the SAME context —
so you can switch engines mid-project without losing continuity. When an agent
edits its own file during work, `sync` converges them again (newest wins), and
that sync also runs automatically after every claude/codex/gemini call (gated
by CONTEXT_AUTO_SYNC).

Subcommands (passed via prompt):
  (empty) | show       print the current shared context
  status               which files exist + sizes + last modified
  init                 write a starter template instantly
  refresh              have Claude analyze the project and rewrite context
  sync                 converge the 3 files (newest content → the others)
  add <text>           append a timestamped entry to the Changelog, then sync
"""

from datetime import datetime
from pathlib import Path

from server import config
from server.skills.base import Skill
from server.skills import register, get_skill


CONTEXT_FILES = ["AGENTS.md", "CLAUDE.md", "GEMINI.md"]
CANONICAL = "AGENTS.md"
CHANGELOG_HEADING = "## Changelog"


TEMPLATE = """\
# Project Context — {name}

> UNIVERSAL CONTEXT for all AI agents (Claude, Codex, Gemini).
> This file is mirrored to AGENTS.md, CLAUDE.md, and GEMINI.md — keep it as the
> single source of truth for what this project is and where it stands.
>
> AGENTS: when you make a meaningful change to this project, UPDATE the
> "Current State" and "Changelog" sections below before you finish.

## Overview
(What is this project? One paragraph.)

## Tech Stack
(Languages, frameworks, key dependencies.)

## Architecture / Key Files
(Where the important code lives.)

## Conventions
(Code style, naming, testing, commit rules an agent should follow.)

## Current State
(What works, what's in progress, known issues.)

{changelog_heading} (most recent first)
- {date} — context initialized

## TODO / Next Steps
(What to work on next.)
"""


# ── paths ─────────────────────────────────────────────────────────────────────

def _workspace() -> Path:
    return config.WORKSPACE_DIR


def _path(name: str = CANONICAL, workspace: Path | None = None) -> Path:
    return (workspace or _workspace()) / name


# ── sync (also called automatically from cli_base after agent runs) ───────────

def sync_context_files(workspace: Path | None = None) -> str:
    """
    Converge AGENTS.md / CLAUDE.md / GEMINI.md within a workspace.
    The most-recently-modified file is the source of truth; its content is
    copied into the others. Returns a short status string.
    """
    ws = workspace or _workspace()
    paths = [ws / f for f in CONTEXT_FILES]
    existing = [p for p in paths if p.exists()]
    if not existing:
        return "no-context-files"

    newest = max(existing, key=lambda p: p.stat().st_mtime)
    try:
        content = newest.read_text(errors="replace")
    except OSError:
        return "read-failed"

    synced = []
    for p in paths:
        if p == newest:
            continue
        try:
            current = p.read_text(errors="replace") if p.exists() else None
        except OSError:
            current = None
        if current != content:
            try:
                p.write_text(content)
                synced.append(p.name)
            except OSError:
                continue
    return (f"{newest.name} → {', '.join(synced)}" if synced else "already in sync")


# ── views ─────────────────────────────────────────────────────────────────────

def _show() -> str:
    for name in [CANONICAL] + [f for f in CONTEXT_FILES if f != CANONICAL]:
        p = _path(name)
        if p.exists():
            text = p.read_text(errors="replace")
            tail = "\n\n…(truncated)" if len(text) > 3000 else ""
            return f"CONTEXT — {_workspace().name} ({p.name}):\n\n{text[:3000]}{tail}"
    return (f"No context file in {_workspace().name}.\n"
            "Run /context init for a template, or /context refresh to have "
            "Claude analyze the project and write one.")


def _status() -> str:
    lines = [f"CONTEXT FILES — {_workspace().name}:", ""]
    any_exist = False
    for name in CONTEXT_FILES:
        p = _path(name)
        if p.exists():
            any_exist = True
            st = p.stat()
            when = datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")
            lines.append(f"  ✓ {name:<11} {st.st_size:>5}B   {when}")
        else:
            lines.append(f"  ✗ {name:<11} (missing)")
    if not any_exist:
        lines.append("\nNone yet — run /context init")
    else:
        lines.append("\n/context sync to converge · /context show to view")
    return "\n".join(lines)


def ensure_context(workspace: Path | None = None) -> bool:
    """
    Create template context files if NONE exist in the workspace.
    Returns True if it just created them, False if they already existed
    (or couldn't be written). Safe to call repeatedly.
    """
    ws = workspace or _workspace()
    if any((ws / f).exists() for f in CONTEXT_FILES):
        return False
    content = TEMPLATE.format(
        name=ws.name,
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        changelog_heading=CHANGELOG_HEADING,
    )
    try:
        (ws / CANONICAL).write_text(content)
    except OSError:
        return False
    sync_context_files(ws)
    return True


def _init() -> str:
    if _path().exists():
        return (f"{CANONICAL} already exists in {_workspace().name}.\n"
                "Use /context show to view, or /context refresh to regenerate.")

    if not ensure_context():
        return f"Couldn't create context files in {_workspace().name}."

    return (f"Created {CANONICAL} (template) in {_workspace().name}.\n"
            "Mirrored to CLAUDE.md and GEMINI.md.\n\n"
            "Edit it, or run /context refresh to have Claude fill it in.")


def _add(text: str) -> str:
    if not text:
        return "Usage: /context add <changelog entry>"
    p = _path()
    if not p.exists():
        return f"No {CANONICAL} yet. Run /context init first."

    content = p.read_text(errors="replace")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {stamp} — {text}"

    if CHANGELOG_HEADING in content:
        idx = content.index(CHANGELOG_HEADING)
        line_end = content.index("\n", idx)
        content = content[:line_end + 1] + entry + "\n" + content[line_end + 1:]
    else:
        content += f"\n\n{CHANGELOG_HEADING} (most recent first)\n{entry}\n"

    try:
        p.write_text(content)
    except OSError as exc:
        return f"Write failed: {exc}"

    sync_result = sync_context_files()
    return f"Logged + synced ({sync_result}):\n{entry}"


async def _refresh() -> str:
    claude = get_skill("claude")
    if claude is None:
        return "Claude skill unavailable — can't auto-generate context."

    prompt = (
        "Create or update a project context file named AGENTS.md at the project "
        "root. It must have these sections: Overview, Tech Stack, "
        "Architecture / Key Files, Conventions, Current State, "
        "'## Changelog (most recent first)', and TODO / Next Steps. "
        "Start the file with a short note telling AI agents to update the "
        "Current State and Changelog sections when they make changes. "
        "Keep it under 150 lines and accurate to what's actually in this "
        "directory. After writing AGENTS.md, copy the IDENTICAL content into "
        "CLAUDE.md and GEMINI.md so all agents share the same context."
    )
    result = await claude.run(prompt)
    sync_result = sync_context_files()
    return (f"Context refreshed by Claude (synced: {sync_result}).\n\n"
            f"{result[:800]}")


# ── skill ─────────────────────────────────────────────────────────────────────

class ContextSkill(Skill):
    name = "context"
    description = ("Universal project context shared by all agents "
                   "(AGENTS.md ↔ CLAUDE.md ↔ GEMINI.md)")

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        args = prompt.strip().split()
        cmd = args[0].lower() if args else "show"
        rest = " ".join(args[1:]).strip()

        if cmd in ("show", "view", "cat"):
            return _show()
        if cmd == "status":
            return _status()
        if cmd == "init":
            return _init()
        if cmd in ("refresh", "regen", "regenerate", "analyze"):
            return await _refresh()
        if cmd == "sync":
            return f"Context sync: {sync_context_files()}"
        if cmd in ("add", "log", "note"):
            return _add(rest)

        return ("Subcommands: show | status | init | refresh | sync | add <text>")


register(ContextSkill())
