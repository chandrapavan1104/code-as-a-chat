"""
projects skill — switch the active WORKSPACE_DIR from your phone.

Subcommands (passed via prompt):
  (empty) | list           list candidate projects (subdirs of PROJECTS_PARENT_DIR)
  current                  show the currently active project
  switch <name-or-path>    set the workspace to a project
  use|set <name-or-path>   aliases for switch

Discovery:
  Candidate projects are read from PROJECTS_PARENT_DIR (env), default
  ~/Desktop/Projects.  Matching is case-insensitive: exact name first,
  then unique substring, then absolute path.

Persistence:
  The active workspace is written to ~/.codeasachat/state.json and reloaded
  on every server start, so phone-side switches survive launchd restarts.
"""

import json
import os
from pathlib import Path

from server import config
from server.skills.base import Skill
from server.skills import register


STATE_DIR = Path.home() / ".codeasachat"
STATE_FILE = STATE_DIR / "state.json"


# ── persistence ───────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def apply_persisted_workspace() -> None:
    """
    Called once at server startup from config.py.
    If state.json holds a valid workspace_dir, override config.WORKSPACE_DIR.
    """
    state = _load_state()
    saved = state.get("workspace_dir")
    if not saved:
        return
    p = Path(saved).expanduser()
    if p.exists() and p.is_dir():
        config.WORKSPACE_DIR = p


# ── discovery ─────────────────────────────────────────────────────────────────

def _projects_parent() -> Path:
    env = os.getenv("PROJECTS_PARENT_DIR", "")
    return Path(env).expanduser() if env else Path.home() / "Desktop" / "Projects"


def _candidates() -> list[Path]:
    parent = _projects_parent()
    if not parent.exists() or not parent.is_dir():
        return []
    return sorted(
        (p for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )


def _resolve(target: str) -> Path | None:
    """Match `target` to a project directory."""
    target = target.strip().strip("'\"")
    if not target:
        return None

    # Explicit path
    if "/" in target or target.startswith("~"):
        p = Path(target).expanduser().resolve()
        return p if p.is_dir() else None

    cands = _candidates()
    low = target.lower()

    # Exact name
    for c in cands:
        if c.name.lower() == low:
            return c

    # Unique substring
    matches = [c for c in cands if low in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


# ── view helpers ──────────────────────────────────────────────────────────────

def _prettify(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


def _list_view() -> str:
    parent = _projects_parent()
    cands = _candidates()
    active = config.WORKSPACE_DIR

    if not cands:
        return (
            f"No projects found in {_prettify(parent)}\n\n"
            "Set PROJECTS_PARENT_DIR in .env to point at a different parent dir."
        )

    lines = [f"PROJECTS in {_prettify(parent)}:", ""]
    for c in cands:
        marker = "▶" if c.resolve() == active.resolve() else " "
        lines.append(f"{marker} {c.name}")

    lines += [
        "",
        "USAGE:",
        "• /projects switch <name>   change active",
        "• /projects current         show active",
    ]
    return "\n".join(lines)


def _current_view() -> str:
    return f"ACTIVE PROJECT:\n{_prettify(config.WORKSPACE_DIR)}"


def _switch_view(target: str) -> str:
    new_path = _resolve(target)
    if new_path is None:
        cands = _candidates()
        if not cands:
            return f"Could not match '{target}' and PROJECTS_PARENT_DIR is empty."
        names = ", ".join(c.name for c in cands[:8])
        more = f" (+{len(cands) - 8} more)" if len(cands) > 8 else ""
        return (
            f"No project matches '{target}'.\n\n"
            f"Candidates: {names}{more}\n"
            f"Try /projects to see the full list."
        )

    old = config.WORKSPACE_DIR
    if new_path.resolve() == old.resolve():
        return f"Already on {_prettify(new_path)} — nothing to change."

    config.WORKSPACE_DIR = new_path

    try:
        state = _load_state()
        state["workspace_dir"] = str(new_path)
        _save_state(state)
    except OSError as exc:
        return (f"Switched to {_prettify(new_path)} for this session, "
                f"but could not persist: {exc}")

    # Auto-create shared context files if this workspace has none yet.
    context_note = ""
    if getattr(config, "CONTEXT_AUTO_INIT", True):
        try:
            from server.skills.context import ensure_context
            if ensure_context(new_path):
                context_note = "\nCreated AGENTS.md/CLAUDE.md/GEMINI.md (template) — run /context refresh to fill in."
        except Exception:
            pass

    return (
        f"SWITCHED PROJECT:\n"
        f"From: {_prettify(old)}\n"
        f"To:   {_prettify(new_path)}\n\n"
        f"All subsequent /claude /codex /gemini /files calls work in the new dir."
        f"{context_note}"
    )


# ── skill ─────────────────────────────────────────────────────────────────────

class ProjectsSkill(Skill):
    name = "projects"
    description = "Switch active project dir: /projects | /projects switch <name>"
    final_output = True
    agent_doc = ("Switch the active project directory (workspace) for all subsequent skills. "
                 'args: "" (list) | "current" | "switch <name-or-path>"')

    async def run(self, prompt: str = "", **kwargs) -> str:
        args = prompt.strip().split()
        if not args:
            return _list_view()

        cmd = args[0].lower()

        if cmd in ("list", "ls"):
            return _list_view()
        if cmd in ("current", "active", "where"):
            return _current_view()
        if cmd in ("switch", "use", "set", "cd"):
            if len(args) < 2:
                return "Usage: /projects switch <name-or-path>"
            return _switch_view(" ".join(args[1:]))

        # Bare arg → treat as a switch target
        return _switch_view(prompt.strip())


register(ProjectsSkill())
