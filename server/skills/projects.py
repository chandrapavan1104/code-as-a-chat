"""
projects skill — see and change the project a turn runs in.

Subcommands (passed via prompt):
  (empty) | list           list candidate projects (subdirs of PROJECTS_PARENT_DIR)
  current                  show the currently active project
  switch <name-or-path>    point this turn at a project
  use|set|cd <name-or-path>  aliases for switch

Discovery and resolution live in `server/workspace.py`, which is the single
authority — this skill is the chat-facing view over it.

THE INVARIANT: a switch made *by the agent* rebinds only the current turn, and
only once. It does not touch the global default and it cannot be undone later in
the same turn. That is what stops the switch → switch-back → switch loop that
used to eat a whole turn's step budget. A switch made by the *user* (the
Projects screen, or `/projects switch` typed directly) also persists the default
for new threads, because that is an explicit choice rather than a routing guess.

The reply carries a `[[switch:<name>]]` marker when the binding changed, so the
app can move the conversation thread to that project — the same mechanism as the
existing `[[move:general]]` confirm-to-move marker.
"""

from pathlib import Path

from server import workspace
from server.skills.base import Skill
from server.skills import register


# Replies that mean "nothing happened, the precondition was already met". The
# shell agent reads these to keep a redundant switch from consuming a step of
# the user's budget — see _is_noop_step in server/skills/shell.py.
NOOP_REPLIES = ("Already on ", "Project already switched to ")


# ── back-compat shims ─────────────────────────────────────────────────────────
# Older call sites (api_v2, shell's directory hint) import these names directly.

def _projects_parent() -> Path:
    return workspace.parent_dir()


def _candidates() -> list[Path]:
    return workspace.candidates()


def _resolve(target: str) -> Path | None:
    return workspace.resolve(target)


def _prettify(path: Path) -> str:
    return workspace.prettify(path)


def apply_persisted_workspace() -> None:
    """Called once at server startup from config.py: if state.json holds a valid
    workspace_dir, make it the default."""
    import json
    if not workspace.STATE_FILE.exists():
        return
    try:
        saved = json.loads(workspace.STATE_FILE.read_text()).get("workspace_dir")
    except (OSError, json.JSONDecodeError):
        return
    if not saved:
        return
    p = Path(saved).expanduser()
    if p.is_dir():
        from server import config
        config.WORKSPACE_DIR = p


# ── views ─────────────────────────────────────────────────────────────────────

def _list_view() -> str:
    """Names AND paths. A bare name is ambiguous when ~/Projects holds thirty
    directories, several of which are not repos at all."""
    rows = workspace.describe_all()
    if not rows:
        return (
            f"No projects found in {workspace.prettify(workspace.parent_dir())}\n\n"
            "Set PROJECTS_PARENT_DIR in .env to point at a different parent dir."
        )

    lines = [f"PROJECTS in {workspace.prettify(workspace.parent_dir())}:", ""]
    for r in rows:
        marker = "▶" if r["active"] else " "
        lines.append(f"{marker} {r['name']}")
        detail = r["display_path"]
        if r["is_git"] and r["branch"]:
            detail += f"  · git {r['branch']}"
        elif not r["is_git"]:
            detail += "  · not a git repo"
        lines.append(f"    {detail}")

    lines += [
        "",
        "USAGE:",
        "• /projects switch <name>   change active",
        "• /projects current         show active",
    ]
    return "\n".join(lines)


def _current_view() -> str:
    cur = workspace.active()
    info = workspace.describe(cur, is_active=True)
    lines = ["ACTIVE PROJECT:", info["display_path"]]
    if info["is_git"]:
        git = f"git {info['branch'] or '?'}"
        if info["remote"]:
            git += f" · {info['remote']}"
        lines.append(git)
    else:
        lines.append("not a git repo")
    return "\n".join(lines)


def _switch_view(target: str, *, persist: bool = False) -> str:
    """Point the current turn (and, when the user asked for it, the default) at
    another project."""
    new_path = workspace.resolve(target)
    if new_path is None:
        names = workspace.suggestions(target)
        if not names:
            return (f"Could not match '{target}' and "
                    f"{workspace.prettify(workspace.parent_dir())} is empty.")
        return (
            f"No project matches '{target}'.\n\n"
            f"Did you mean: {', '.join(names)}\n"
            f"Try /projects to see the full list."
        )

    old = workspace.active()
    changed, reason = workspace.rebind(new_path)

    if not changed and reason == "already":
        # Not an error, and deliberately not a wasted step: the agent asking to
        # go where it already is means the precondition is satisfied.
        return (f"Already on {workspace.prettify(new_path)} — nothing to change.\n"
                "NOTE: this project is already active; continue with the actual task.")

    if not changed and reason.startswith("locked:"):
        locked = reason.split(":", 1)[1]
        return (
            f"Project already switched to {locked} earlier in this turn — "
            f"staying there.\nNOTE: one project change per turn. Do NOT switch "
            f"again; continue the task in {locked}, or ask the user to open the "
            f"{new_path.name} chat."
        )

    if persist:
        try:
            workspace.persist_default(new_path)
        except OSError as exc:
            return (f"Switched to {workspace.prettify(new_path)} for this turn, "
                    f"but could not persist: {exc}")

    context_note = ""
    from server import config
    if getattr(config, "CONTEXT_AUTO_INIT", True):
        try:
            from server.skills.context import ensure_context
            if ensure_context(new_path):
                context_note = ("\nCreated AGENTS.md/CLAUDE.md/GEMINI.md (template)"
                                " — run /context refresh to fill in.")
        except Exception:
            pass

    return (
        f"SWITCHED PROJECT:\n"
        f"From: {workspace.prettify(old)}\n"
        f"To:   {workspace.prettify(new_path)}\n\n"
        f"All subsequent /claude /codex /gemini /files calls in this turn work "
        f"in the new dir."
        f"{context_note}\n"
        f"[[switch:{new_path.name}]]"
    )


# ── skill ─────────────────────────────────────────────────────────────────────

class ProjectsSkill(Skill):
    name = "projects"
    description = "Switch active project dir: /projects | /projects switch <name>"
    final_output = True
    agent_doc = ("Switch the project directory this turn runs in, for all subsequent "
                 "skills. You may switch AT MOST ONCE per turn and cannot switch back. "
                 'args: "" (list) | "current" | "switch <name-or-path>"')

    async def run(self, prompt: str = "", **kwargs) -> str:
        # Explicit user actions (the Projects screen, a typed /projects switch)
        # also move the default for new threads; an agent's routing decision
        # does not.
        persist = bool(kwargs.get("persist"))
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
            return _switch_view(" ".join(args[1:]), persist=persist)

        # Bare arg → treat as a switch target
        return _switch_view(prompt.strip(), persist=persist)


register(ProjectsSkill())
