"""
firebase skill — drive `firebase` CLI from your phone.

Subcommands (passed via prompt):
  (empty) | status               show current firebase project + workspace
  list | projects                list all your firebase projects
  use <project-id>               set active project for THIS workspace
  deploy [target]                firebase deploy [--only target]
                                   targets: hosting, functions, firestore,
                                   storage, hosting:sitename, etc.
  preview [name]                 hosting:channel:deploy <name>
                                   auto-generates a name if not given
  whoami                         which Google account is signed in

Relies on `firebase login` being already done (it is — credentials live in
~/.config/configstore/firebase-tools.json). Runs from the active WORKSPACE_DIR,
so do /projects switch first to point at the right repo.
"""

import asyncio
import json
import re
import shutil
import time
from pathlib import Path

from server import workspace
from server.skills.base import Skill
from server.skills import register


DEFAULT_TIMEOUT = 600  # 10 min — deploys can be slow
QUICK_TIMEOUT = 60

HOSTING_URL_RE = re.compile(r"Hosting URL:\s*(https?://\S+)")
CHANNEL_URL_RE = re.compile(r"Channel URL\s*\([^)]+\):\s*(https?://\S+)")
ACTIVE_PROJECT_RE = re.compile(r"Active Project:\s*(\S+)")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ── subprocess helper ─────────────────────────────────────────────────────────

async def _run_firebase(args: list[str], timeout: int = DEFAULT_TIMEOUT
                        ) -> tuple[int, str, str]:
    if shutil.which("firebase") is None:
        return (127, "", "firebase CLI not installed. Install: npm i -g firebase-tools")

    cwd = str(workspace.active())
    cmd = ["firebase"] + args

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        return (-1, "", f"firebase command timed out after {timeout}s")

    stdout = ANSI_RE.sub("", stdout_b.decode(errors="replace"))
    stderr = ANSI_RE.sub("", stderr_b.decode(errors="replace"))
    return (proc.returncode, stdout, stderr)


# ── shared helpers ────────────────────────────────────────────────────────────

def _has_firebase_json() -> bool:
    return (workspace.active() / "firebase.json").exists()


def _short_workspace() -> str:
    home = str(Path.home())
    s = str(workspace.active())
    return "~" + s[len(home):] if s.startswith(home) else s


def _no_firebase_json_msg() -> str:
    return (
        f"No firebase.json in {_short_workspace()}.\n\n"
        "This workspace isn't a Firebase project. Either:\n"
        "• /projects switch <name>  to pick a Firebase project\n"
        "• run `firebase init` in this dir from a terminal"
    )


def _read_targets() -> list[str]:
    """Inspect firebase.json to list which products are configured."""
    fb_json = workspace.active() / "firebase.json"
    try:
        data = json.loads(fb_json.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for k in ("hosting", "functions", "firestore", "storage",
              "database", "remoteconfig", "emulators"):
        if k in data:
            out.append(k)
    return out


# ── views ─────────────────────────────────────────────────────────────────────

async def _status_view() -> str:
    if not _has_firebase_json():
        return _no_firebase_json_msg()

    rc, out, err = await _run_firebase(["use"], timeout=QUICK_TIMEOUT)
    project = "(unknown)"
    if rc == 0:
        m = ACTIVE_PROJECT_RE.search(out)
        if m:
            project = m.group(1)
        else:
            # Sometimes `firebase use` just prints the project id alone
            first_line = out.strip().splitlines()[0] if out.strip() else ""
            if first_line and not first_line.startswith("No active"):
                project = first_line.strip()

    targets = _read_targets()
    return (
        f"FIREBASE STATUS\n\n"
        f"Workspace: {_short_workspace()}\n"
        f"Project:   {project}\n"
        f"Targets:   {', '.join(targets) or '(none configured)'}"
    )


def _parse_projects_json(stdout: str) -> list[dict] | None:
    brace = stdout.find("{")
    if brace < 0:
        return None
    try:
        data = json.loads(stdout[brace:])
    except json.JSONDecodeError:
        return None
    result = data.get("result")
    return result if isinstance(result, list) else None


async def _list_projects_view() -> str:
    rc, out, err = await _run_firebase(["projects:list", "--json"], timeout=QUICK_TIMEOUT)
    if rc != 0:
        return f"[firebase error] {(err or out).strip()[:500]}"

    projects = _parse_projects_json(out)
    if projects is None:
        return f"Could not parse projects list.\n\n{out[:500]}"
    if not projects:
        return "No firebase projects under this account."

    lines = [f"FIREBASE PROJECTS ({len(projects)}):", ""]
    for p in projects[:25]:
        pid = p.get("projectId", "?")
        name = p.get("displayName", pid)
        lines.append(f"• {name}")
        lines.append(f"    id: {pid}")
    if len(projects) > 25:
        lines.append(f"\n… +{len(projects) - 25} more")
    lines += ["", "USAGE:", "• /firebase use <id>     set active for this workspace"]
    return "\n".join(lines)


async def _use_view(name: str) -> str:
    if not name:
        return "Usage: /firebase use <project-id>"
    if not _has_firebase_json():
        return _no_firebase_json_msg()
    rc, out, err = await _run_firebase(["use", name], timeout=QUICK_TIMEOUT)
    if rc != 0:
        return f"[firebase error] {(err or out).strip()[:400]}"
    return f"Switched firebase project to: {name}\n\n{out.strip()[:300]}"


async def _deploy_view(target: str) -> str:
    if not _has_firebase_json():
        return _no_firebase_json_msg()

    args = ["deploy", "--non-interactive"]
    if target:
        args += ["--only", target]

    rc, out, err = await _run_firebase(args, timeout=DEFAULT_TIMEOUT)
    combined = out + "\n" + err

    if rc != 0:
        tail = combined.strip().splitlines()[-15:]
        return "DEPLOY FAILED\n\n" + "\n".join(tail)

    hosting = HOSTING_URL_RE.search(combined)
    lines = ["DEPLOY OK"]
    if target:
        lines.append(f"Target: {target}")
    if hosting:
        lines.append(f"Hosting URL: {hosting.group(1)}")
    else:
        # Surface useful tail lines if no obvious URL
        for ln in combined.strip().splitlines()[-6:]:
            if ln.strip():
                lines.append(ln.strip())
    return "\n".join(lines)


async def _preview_view(name: str) -> str:
    if not _has_firebase_json():
        return _no_firebase_json_msg()

    if not name:
        name = f"preview-{int(time.time())}"
    # Channel IDs must be lowercase, alphanumeric + dashes
    name = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-") or f"preview-{int(time.time())}"

    rc, out, err = await _run_firebase(
        ["hosting:channel:deploy", name, "--non-interactive"],
        timeout=DEFAULT_TIMEOUT,
    )
    combined = out + "\n" + err

    if rc != 0:
        tail = combined.strip().splitlines()[-15:]
        return f"PREVIEW FAILED (channel: {name})\n\n" + "\n".join(tail)

    m = CHANNEL_URL_RE.search(combined)
    lines = ["PREVIEW DEPLOYED", f"Channel: {name}"]
    if m:
        lines.append(f"URL: {m.group(1)}")
    else:
        for ln in combined.strip().splitlines()[-5:]:
            if ln.strip():
                lines.append(ln.strip())
    return "\n".join(lines)


async def _whoami_view() -> str:
    rc, out, err = await _run_firebase(["login:list"], timeout=QUICK_TIMEOUT)
    return (out or err).strip()[:500] or f"(no output, code {rc})"


# ── skill ─────────────────────────────────────────────────────────────────────

class FirebaseSkill(Skill):
    name = "firebase"
    agent_doc = ("Deploy/preview the active workspace to Firebase. User must have /projects "
                 "switched into a Firebase project (one with firebase.json). "
                 'args: "" (status) | "list" | "use <id>" | "deploy" | "deploy hosting" | '
                 '"preview [name]"')
    description = ("Firebase from your phone: /firebase | list | use <id> | "
                   "deploy [target] | preview [name]")

    async def run(self, prompt: str = "", **kwargs) -> str:
        args = prompt.strip().split()
        if not args:
            return await _status_view()

        cmd = args[0].lower()
        rest = " ".join(args[1:]).strip()

        if cmd in ("status", "current"):
            return await _status_view()
        if cmd in ("list", "projects", "ls"):
            return await _list_projects_view()
        if cmd == "use":
            return await _use_view(rest)
        if cmd == "deploy":
            return await _deploy_view(rest)
        if cmd in ("preview", "channel"):
            return await _preview_view(rest)
        if cmd in ("whoami", "who"):
            return await _whoami_view()

        return ("Subcommands: status | list | use <id> | "
                "deploy [target] | preview [name] | whoami")


register(FirebaseSkill())
