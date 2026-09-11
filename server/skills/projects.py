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

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

from server import workspace
from server.skills.base import Skill, SkillResult
from server.skills import register


# Replies that mean "nothing happened, the precondition was already met". The
# shell agent reads these to keep a redundant switch from consuming a step of
# the user's budget — see _is_noop_step in server/skills/shell.py.
NOOP_REPLIES = ("Already on ", "Project already switched to ")


async def _git(*args: str, timeout: int = 180) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()
        return -1, "", f"timed out after {timeout}s"
    return (proc.returncode, out.decode(errors="replace").strip(),
            err.decode(errors="replace").strip())


def _clone_args(text: str) -> tuple[str, str | None, bool] | None:
    """Parse a deliberately small clone grammar; never execute free-form shell."""
    match = re.match(
        r"^clone\s+(?P<url>\S+?)(?:\s+as\s+(?P<name>[A-Za-z0-9._-]+))?"
        r"(?:\s+and\s+(?:switch|open|use))?$", text.strip(), re.IGNORECASE)
    if not match:
        return None
    switch = bool(re.search(r"\s+and\s+(?:switch|open|use)\s*$", text,
                            re.IGNORECASE))
    return match.group("url"), match.group("name"), switch


def _remote_identity(value: str) -> str:
    value = value.strip().rstrip("/")
    scp = re.fullmatch(r"git@(?P<host>[^:]+):(?P<path>.+)", value)
    if scp:
        return f"{scp.group('host').lower()}/{scp.group('path').removesuffix('.git')}"
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return f"file:{Path(parsed.path).resolve()}"
    return f"{parsed.hostname or ''}/{parsed.path.lstrip('/').removesuffix('.git')}".lower()


async def _clone_view(text: str, *, persist: bool = False) -> SkillResult:
    parsed = _clone_args(text)
    if parsed is None:
        return SkillResult(
            "failed", "Usage: /projects clone <git-url> [as <name>] [and switch]")
    url, requested_name, should_switch = parsed
    scp_style = re.fullmatch(r"git@[^\s:]+:(?P<path>[^\s]+)", url)
    parsed_url = urlparse(url)
    if parsed_url.scheme not in ("https", "ssh", "git", "file") and scp_style is None:
        return SkillResult("failed", "Clone needs an https, ssh, git, or file URL.")
    remote_path = scp_style.group("path") if scp_style else parsed_url.path
    inferred = Path(remote_path.rstrip("/")).name
    if inferred.endswith(".git"):
        inferred = inferred[:-4]
    name = requested_name or inferred
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return SkillResult("failed", "Could not derive a safe project directory name.")

    parent = workspace.parent_dir().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    destination = (parent / name).resolve()
    if destination.parent != parent:
        return SkillResult("failed", "Clone destination must be directly inside the projects folder.")

    existed = destination.exists()
    if existed and not (destination / ".git").is_dir():
        return SkillResult(
            "failed", f"Destination already exists and is not a Git repository: {destination}",
            data={"path": str(destination), "url": url})

    if not existed:
        rc, out, err = await _git("clone", "--", url, str(destination))
        if rc != 0:
            detail = err or out or "Git returned no error details"
            return SkillResult(
                "failed", f"Clone failed: {detail}",
                data={"path": str(destination), "url": url})

    rc, origin, err = await _git("-C", str(destination), "remote", "get-url", "origin")
    if rc != 0 or not origin:
        return SkillResult(
            "failed", f"Repository exists but its origin could not be verified: {err or origin}",
            data={"path": str(destination), "url": url})
    if _remote_identity(origin) != _remote_identity(url):
        return SkillResult(
            "failed", "Destination exists, but its origin is a different repository.\n"
                      f"Requested: {url}\nObserved: {origin}",
            data={"path": str(destination), "url": url, "origin": origin})
    rc, head, err = await _git("-C", str(destination), "rev-parse", "HEAD")
    if rc != 0 or not head:
        return SkillResult(
            "failed", f"Repository exists but HEAD could not be verified: {err or head}",
            data={"path": str(destination), "origin": origin})

    switched = False
    marker = ""
    if should_switch:
        switched, reason = workspace.rebind(destination)
        if not switched and reason not in ("already",):
            return SkillResult(
                "failed", f"Repository verified, but project activation failed: {reason}",
                data={"path": str(destination), "origin": origin, "head": head})
        if persist:
            workspace.persist_default(destination)
        marker = f"\n[[switch:{destination.name}]]"

    action = "Cloned and verified" if not existed else "Found and verified"
    if should_switch:
        action += "; project is active"
    message = (
        f"{action}: {destination.name}\n"
        f"Path: {destination}\n"
        f"Origin: {origin}\n"
        f"Commit: {head[:12]}{marker}"
    )
    return SkillResult(
        "succeeded", message, changed=(not existed or switched),
        data={"path": str(destination), "name": destination.name,
              "origin": origin, "head": head, "active": should_switch},
        evidence=[str(destination / ".git"), f"origin={origin}", f"HEAD={head}"])


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
    agent_doc = ("List, clone, or switch projects. Clone is a real verified Git operation. "
                 'args: "clone <git-url> [as <name>] [and switch]". '
                 "Switch the project directory this turn runs in, for all subsequent "
                 "skills. You may switch AT MOST ONCE per turn and cannot switch back. "
                 'args: "" (list) | "current" | "switch <name-or-path>"')

    async def run(self, prompt: str = "", **kwargs) -> str:
        # Explicit user actions (the Projects screen, a typed /projects switch)
        # also move the default for new threads; an agent's routing decision
        # does not.
        persist = bool(kwargs.get("persist"))
        if prompt.strip().lower().startswith("clone "):
            return await _clone_view(prompt.strip(), persist=persist)
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
