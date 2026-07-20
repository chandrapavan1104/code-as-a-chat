"""
fix skill — the self-healing repair agent.

You describe a problem in the Gajala app (text + screenshots); this runs a coding
agent (codex, to conserve Claude) on THIS repo to diagnose and make a MINIMAL fix
on a throwaway git branch, then gates by what changed:

  • nothing changed  → reports the diagnosis + what it needs from you (no thrash).
  • app-only changes → rebuilds the APK and hands back the install link to test.
  • server changes   → shows the diff and WAITS ("/fix ship") — it never restarts
                       the server with unverified code, so a bad fix can't lock
                       you out from your phone.

"/fix ship" merges the pending fix. Server merges are health-checked and
auto-rolled-back if the server doesn't come back up.

Guardrails: one bounded codex run (FIX_TIMEOUT wall-clock), no agent loop at this
layer, and a triage prompt that tells the model to stop + ask rather than flail.
"""

import asyncio
import json
import time
from pathlib import Path

from server import config
from server.db import errors_store
from server.skills.base import Skill
from server.skills import register
from server.skills.build_app import build_and_deploy
from server.skills.errors import format_errors

_STATE = Path.home() / ".codeasachat" / "state.json"


def _state_get(key: str, default=None):
    try:
        return json.loads(_STATE.read_text()).get(key, default)
    except (OSError, json.JSONDecodeError):
        return default


def _state_set(key: str, value) -> None:
    try:
        state = json.loads(_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        state = {}
    state[key] = value
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(state, indent=2))


async def _sh(*args: str, cwd: str | None = None, timeout: int = 60) -> tuple[int, str]:
    """Run a command, return (rc, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 124, f"(timed out after {timeout}s)"
    return proc.returncode, out.decode(errors="replace")


async def _git(repo: str, *args: str, timeout: int = 60) -> tuple[int, str]:
    return await _sh("git", "-C", repo, *args, timeout=timeout)


_REPAIR_SYSTEM = """You are the repair agent for THIS project, "Code-as-a-Chat":
a FastAPI server in server/ and a Flutter Android app "Gajala" in clients/gajala/.
The user hit a problem and wants a fix. Work ONLY inside this repo.

Do this:
1. Diagnose the ROOT cause from their description (and any screenshot paths given —
   read the image file). TRACE THE EXACT CODE PATH of the symptom they describe
   before changing anything — find the specific line that produces the wrong
   behaviour. Do not fix an adjacent/plausible-looking thing; fix the actual cause.
2. Make the SMALLEST correct fix. Touch as few files as possible. Do NOT refactor,
   reformat, rename, or "improve" unrelated code. No new dependencies unless
   strictly required.
3. For server changes, verify with the project's venv tools (they are NOT on the
   global PATH):
     .venv/bin/ruff check server/ --ignore E501
     .venv/bin/python -m pytest server/tests/ -q
   and make sure they pass.
4. Do NOT commit, push, restart the server, or run flutter build — the harness
   handles building, review, and deploy.

STOP and explain instead of changing code if ANY of these are true:
- It is not a code bug (network / Tailscale off / phone offline / a CLI or model
  version issue / an environment or account problem).
- It is a design/product decision that needs the user to choose.
- You are unsure of the root cause, or a fix would be speculative/large.
In those cases make NO changes and clearly state: the diagnosis, and exactly what
you need from the user (a decision, info, or an action on their side).

BE HONEST ABOUT CONFIDENCE. You usually cannot run the app or reproduce a UI /
behavioural bug, so you often CANNOT confirm your change actually fixes it. If so,
say plainly that the fix is UNVERIFIED — a hypothesis — name what would confirm it
(a specific thing for the user to test), and never imply it's definitely fixed. If
there is no captured error and you can't pin the exact cause from the code, prefer
asking the user for one concrete detail (exact steps, what they see vs expect)
over guessing.

End with a 2-4 line summary: what was wrong, what you changed, and — if you
couldn't verify it — say so and how to confirm. Be concise."""


async def _run_codex(repo: str, task: str, timeout: int) -> tuple[str, str | None]:
    """One bounded codex run in the repo. Returns (final_message, error)."""
    from server import prefs
    model = prefs.get_coding_model("codex") or config.CODEX_MODEL
    prompt = f"{_REPAIR_SYSTEM}\n\n=== USER PROBLEM ===\n{task}"
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check",
           "--dangerously-bypass-approvals-and-sandbox", "-C", repo,
           "--model", model, prompt]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return "", f"the fix attempt ran past the {timeout}s limit and was stopped"

    final, error = "", None
    for line in out_b.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        if etype == "item.completed" and ev.get("item", {}).get("type") == "agent_message":
            final = ev["item"].get("text", "")
        elif etype in ("error", "turn.failed"):
            error = ev.get("message") or (ev.get("error") or {}).get("message")
    return final, error


def _changed_files(status_porcelain: str) -> list[str]:
    files = []
    for line in status_porcelain.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return files


class FixSkill(Skill):
    name = "fix"
    description = "Self-healing agent: describe a bug (+ screenshot), it diagnoses & fixes it."
    final_output = True
    agent_doc = ('Repair THIS app. Use when the user reports a bug/problem with '
                 'Gajala itself and wants it fixed. Pass their full description '
                 '(and any "[User sent an image, saved at: <path>]" markers) as '
                 'args. "/fix ship" applies the pending fix.')

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        prompt = prompt.strip()
        if prompt.lower() in ("ship", "apply", "/fix ship"):
            return await self._ship()
        if not prompt:
            return ("🔧 Describe the problem (attach a screenshot if you have one) "
                    "and I'll diagnose + fix it. I auto-build app fixes and gate "
                    "server changes for your OK.")
        return await self._attempt(prompt)

    async def _attempt(self, task: str) -> str:
        repo = str(config.REPO_DIR)

        rc, status = await _git(repo, "status", "--porcelain")
        if rc != 0:
            return f"🔧 Can't reach the repo git ({status.strip()[:200]})."
        if status.strip():
            return ("🔧 The repo has uncommitted changes, so I won't touch it — "
                    "commit or stash them first, then retry.")

        rc, base = await _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        base = base.strip() or "main"
        branch = f"fix/agent-{int(time.time())}"
        await _git(repo, "checkout", "-b", branch)

        try:
            # Give the agent the actual captured errors, not just the description.
            errs = errors_store.recent(8)
            task_ctx = task
            if errs:
                task_ctx = (f"{task}\n\n=== RECENTLY CAPTURED ERRORS (server + app, "
                            f"may be related) ===\n{format_errors(errs, detail=True)}")
            summary, error = await _run_codex(repo, task_ctx, config.FIX_TIMEOUT)

            _, status2 = await _git(repo, "status", "--porcelain")
            changed = _changed_files(status2)

            if not changed:
                await _git(repo, "checkout", base)
                await _git(repo, "branch", "-D", branch)
                head = f"🔧 I didn't change anything — {'it needs a look' if not error else 'the run hit an error'}.\n\n"
                if error:
                    head += f"Error: {error}\n\n"
                return head + (summary or "No diagnosis returned.")

            # Snapshot the change on the branch (keeps main clean).
            await _git(repo, "add", "-A")
            await _git(repo, "commit", "-m", f"fix(agent): {task[:60]}", timeout=30)
            _state_set("pending_fix", {"branch": branch, "base": base, "files": changed})

            app_only = all(f.startswith("clients/gajala/") for f in changed)
            filelist = "\n".join(f"  • {f}" for f in changed[:12])
            await _git(repo, "checkout", base)

            if app_only:
                ok, msg = await build_and_deploy(timeout=config.FIX_TIMEOUT)
                if not ok:
                    return (f"🔧 Made an app fix but the build failed:\n{msg}\n\n"
                            f"Diagnosis: {summary}\nBranch: {branch}")
                return (f"🔧 Fixed (app-side) — auto-built.\n\n{summary}\n\n"
                        f"Changed:\n{filelist}\n\n{msg}\n\n"
                        f"Test it on your phone. Reply **/fix ship** to keep it "
                        f"(merge to {base}), or tell me what's still off.")
            else:
                rc, diff = await _git(repo, "diff", f"{base}..{branch}", "--stat")
                return (f"🔧 Proposed fix (server-side — needs your OK, not applied).\n\n"
                        f"{summary}\n\nChanged:\n{filelist}\n\n{diff.strip()[:800]}\n\n"
                        f"Reply **/fix ship** to merge + restart (I health-check and "
                        f"auto-roll-back if it doesn't come up), or tell me what to change.")
        except Exception as exc:
            await _git(repo, "checkout", base)
            await _git(repo, "branch", "-D", branch)
            return f"🔧 The fix attempt failed: {exc}"

    async def _ship(self) -> str:
        repo = str(config.REPO_DIR)
        pending = _state_get("pending_fix")
        if not pending:
            return "🔧 Nothing pending to ship."
        branch, base, files = pending["branch"], pending["base"], pending["files"]
        server_touched = any(not f.startswith("clients/gajala/") for f in files)

        rc, before = await _git(repo, "rev-parse", base)
        before = before.strip()
        rc, out = await _git(repo, "checkout", base)
        rc, out = await _git(repo, "merge", "--no-ff", "-m", f"ship {branch}", branch)
        if rc != 0:
            await _git(repo, "merge", "--abort")
            return f"🔧 Merge failed (conflict?):\n{out.strip()[:300]}"

        _state_set("pending_fix", None)
        if not server_touched:
            return f"🔧 Merged to {base} ✅ (app-only — already built)."

        # Server change: hand the restart to a DETACHED guard, because the server
        # can't restart itself (it would kill this very request). The guard
        # restarts, health-checks, rolls back to `before` if it doesn't boot, and
        # pushes the outcome to the phone. We return immediately.
        import subprocess
        import sys
        subprocess.Popen(
            [sys.executable, "-m", "server.restart_guard", repo, before],
            cwd=repo, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return ("🔧 Merged to {b} — restarting on the new code now. I health-check "
                "and auto-roll-back if it doesn't come up; you'll get a push either "
                "way.".format(b=base))


register(FixSkill())
