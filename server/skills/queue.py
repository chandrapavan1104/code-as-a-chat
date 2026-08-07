"""
queue skill — the Night Shift work queue you drive from the phone.

Queue build tasks during the day; the overnight runner (server/night_shift.py)
builds them across all three coding subscriptions on isolated branches, then you
review in the morning. Nothing merges to a base branch unattended.

Subcommands (passed via prompt):
  add [auto|mine] [engine] [<project>:] <task>   queue a job (defaults: auto, any
                                                 engine, active project)
  (empty) | list                                 pending + in-progress jobs
  review                                          last night's results + how to ship
  ship <id>                                       merge a staged/deployed job's branch
  drop <id>                                       remove a job
  tag <id> auto|mine                             flag for the runner / hold for you
  backlog [<project>:] <task>                     append to a project's backlog file
  status                                          runner window + enabled state
"""

import subprocess
import sys
import time
from pathlib import Path

from server import config
from server.db import night_queue_store
from server.skills.base import Skill
from server.skills import register

_ENGINES = ("claude", "codex", "gemini")
_TAGS = ("auto", "mine")
_BACKLOG_DIR = Path.home() / ".codeasachat" / "backlogs"


# ── parsing ───────────────────────────────────────────────────────────────────

def _resolve_project(name: str) -> str | None:
    from server.skills.projects import _resolve
    p = _resolve(name)
    return str(p) if p else None


def _parse_add(rest: str) -> tuple[str, str, str, str] | str:
    """'[auto|mine] [engine] [<project>:] <task>' → (project, task, tag, engine).
    Returns an error string on a bad project name."""
    tag, engine = "auto", "auto"
    body = rest.strip()

    # Leading flags (tag / engine), in any order, space-separated.
    while True:
        first = body.split(maxsplit=1)[0].lower() if body else ""
        if first in _TAGS:
            tag = first
            body = body[len(first):].strip()
        elif first in _ENGINES:
            engine = first
            body = body[len(first):].strip()
        else:
            break

    project = str(config.WORKSPACE_DIR)
    if ":" in body:
        proj_part, task = body.split(":", 1)
        proj_part, task = proj_part.strip(), task.strip()
        if proj_part:
            resolved = _resolve_project(proj_part)
            if resolved is None:
                return f"No project matches '{proj_part}'. Try /projects to list them."
            project = resolved
    else:
        task = body
    return project, task, tag, engine


# ── views ─────────────────────────────────────────────────────────────────────

def _pname(path: str) -> str:
    return Path(path).name


def _line(j: dict) -> str:
    eng = j.get("engine") if j.get("engine") != "auto" else (j.get("engine_used") or "any")
    return f"  #{j['id']} [{j['tag']}·{eng}] {_pname(j['project'])}: {j['task'][:56]}"


def _list_view() -> str:
    pending = night_queue_store.list_jobs(status="queued,running,held")
    if not pending:
        return ("🌙 Queue is empty. Add work with:\n"
                "  /queue add <project>: <task>\n"
                "e.g. /queue add auto codaur: add a CSV export command")
    running = [j for j in pending if j["status"] == "running"]
    queued = [j for j in pending if j["status"] == "queued"]
    held = [j for j in pending if j["status"] == "held"]
    out = ["🌙 NIGHT QUEUE"]
    if running:
        out += ["", "▶ building now:"] + [_line(j) for j in running]
    if queued:
        out += ["", "⏳ queued (auto):"] + [_line(j) for j in queued]
    if held:
        out += ["", "✋ held (mine — I won't run these):"] + [_line(j) for j in held]
    return "\n".join(out)


def _review_view() -> str:
    done = night_queue_store.list_jobs(status="deployed,staged,needs_you,failed")
    if not done:
        return "🌙 Nothing to review yet — no completed night jobs."
    buckets: dict[str, list[dict]] = {"deployed": [], "staged": [], "needs_you": [], "failed": []}
    for j in done:
        buckets[j["status"]].append(j)
    out = ["🌙 NIGHT SHIFT — REVIEW"]
    if buckets["deployed"]:
        out += ["", "📦 Deployed (app built — test on phone, then /queue ship <id>):"]
        out += [_line(j) for j in buckets["deployed"]]
    if buckets["staged"]:
        out += ["", "⏸ Staged (server/project — /queue ship <id> to merge):"]
        for j in buckets["staged"]:
            out.append(_line(j))
    if buckets["needs_you"]:
        out += ["", "🙋 Needs a decision from you:"]
        for j in buckets["needs_you"]:
            out.append(_line(j))
            if j.get("summary"):
                out.append(f"      ↳ {j['summary'][:160]}")
    if buckets["failed"]:
        out += ["", "⚠️ Failed:"] + [_line(j) for j in buckets["failed"]]
    out += ["", "Full detail: /queue show <id>"]
    return "\n".join(out)


def _show_view(job_id: int) -> str:
    j = night_queue_store.get(job_id)
    if not j:
        return f"🌙 No job #{job_id}."
    lines = [
        f"🌙 JOB #{j['id']}  [{j['status']}]",
        f"project: {_pname(j['project'])}  ({j['project']})",
        f"task:    {j['task']}",
        f"tag/engine: {j['tag']} · {j.get('engine_used') or j['engine']}",
    ]
    if j.get("branch"):
        lines.append(f"branch:  {j['branch']} (base {j.get('base')})")
    if j.get("files_changed"):
        lines.append("files:   " + ", ".join(j["files_changed"][:10]))
    if j.get("tokens_total"):
        lines.append(f"tokens:  {j['tokens_total']:,} ({j['tokens_billable']:,} billable)")
    if j.get("summary"):
        lines += ["", j["summary"][:1200]]
    return "\n".join(lines)


# ── ship ──────────────────────────────────────────────────────────────────────

def _git(repo: str, *args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "(git timed out)"


def _ship(job_id: int) -> str:
    j = night_queue_store.get(job_id)
    if not j:
        return f"🌙 No job #{job_id}."
    if j["status"] not in ("staged", "deployed"):
        return (f"🌙 Job #{job_id} is '{j['status']}', not ready to ship "
                "(only staged/deployed jobs merge).")
    repo, branch, base = j["project"], j.get("branch"), j.get("base") or "main"
    if not branch:
        return f"🌙 Job #{job_id} has no branch to merge."

    files = j.get("files_changed") or []
    from server.night_shift import _is_this_repo
    server_touched = _is_this_repo(repo) and any(
        not f.startswith("clients/gajala/") for f in files)

    rc, before = _git(repo, "rev-parse", base)
    before = before.strip()
    _git(repo, "checkout", base)
    rc, out = _git(repo, "merge", "--no-ff", "-m", f"ship night #{job_id}", branch)
    if rc != 0:
        _git(repo, "merge", "--abort")
        return f"🌙 Merge of #{job_id} failed (conflict?):\n{out.strip()[:300]}"

    night_queue_store.update(job_id, status="shipped", ended_at=time.time())

    if not server_touched:
        push_rc, _ = _git(repo, "push", "origin", base, timeout=60)
        pushed = " · pushed" if push_rc == 0 else " · (push skipped/failed)"
        return f"🌙 Shipped #{job_id} → {base} ✅{pushed}."

    # Server change to THIS repo: hand the restart to the detached guard (the
    # server can't restart itself), which health-checks + auto-rolls-back.
    subprocess.Popen(
        [sys.executable, "-m", "server.restart_guard", repo, before],
        cwd=repo, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return (f"🌙 Shipped #{job_id} → {base} — restarting on the new code now. "
            "I health-check, push on success, and auto-roll-back otherwise.")


# ── backlog append ────────────────────────────────────────────────────────────

def _backlog_add(rest: str) -> str:
    body = rest.strip()
    project = str(config.WORKSPACE_DIR)
    if ":" in body:
        proj_part, task = body.split(":", 1)
        proj_part, task = proj_part.strip(), task.strip()
        if proj_part:
            resolved = _resolve_project(proj_part)
            if resolved is None:
                return f"No project matches '{proj_part}'."
            project = resolved
    else:
        task = body
    if not task:
        return "Usage: /queue backlog [<project>:] <task>"
    _BACKLOG_DIR.mkdir(parents=True, exist_ok=True)
    f = _BACKLOG_DIR / f"{_pname(project)}.md"
    with f.open("a") as fh:
        fh.write(f"- [ ] {task}\n")
    return (f"🌙 Added to {_pname(project)} backlog. The night runner pulls from "
            "here when your queue empties and quota remains.")


def _status_view() -> str:
    from server.night_shift import _in_window
    enabled = getattr(config, "NIGHT_SHIFT_ENABLED", False)
    win = f"{getattr(config, 'NIGHT_START', '23:00')}–{getattr(config, 'NIGHT_END', '07:00')}"
    active = enabled and _in_window()
    n_q = len(night_queue_store.list_jobs(status="queued"))
    return ("🌙 NIGHT SHIFT\n"
            f"enabled: {enabled}\n"
            f"window:  {win}  (in window now: {_in_window()})\n"
            f"state:   {'BUILDING' if active else 'idle'}\n"
            f"engines: {getattr(config, 'NIGHT_ENGINES', 'claude,codex,gemini')}\n"
            f"queued:  {n_q} job(s)")


# ── skill ─────────────────────────────────────────────────────────────────────

class QueueSkill(Skill):
    name = "queue"
    description = ("Night Shift queue: /queue add <project>: <task> | list | "
                   "review | ship <id> | drop <id> | tag <id> auto|mine")
    final_output = True
    aliases = ["night"]
    agent_doc = (
        "Overnight autonomous build queue ('Night Shift'). The user queues coding "
        "tasks to be built overnight across claude/codex/gemini on isolated "
        "branches. Route here when they want to QUEUE work for later/overnight, "
        "review what was built, or ship/merge a night job. args: "
        "'add [auto|mine] [engine] [<project>:] <task>' | '' or 'list' | 'review' | "
        "'ship <id>' | 'drop <id>' | 'tag <id> auto|mine' | 'backlog <project>: <task>' | "
        "'show <id>' | 'status'. Default tag auto, default project = active workspace.")

    async def run(self, prompt: str = "", **kwargs) -> str:
        args = prompt.strip().split(maxsplit=1)
        if not args:
            return _list_view()
        cmd = args[0].lower()
        rest = args[1] if len(args) > 1 else ""

        if cmd in ("list", "ls"):
            return _list_view()
        if cmd == "review":
            return _review_view()
        if cmd == "status":
            return _status_view()
        if cmd == "backlog":
            return _backlog_add(rest)

        if cmd == "add":
            parsed = _parse_add(rest)
            if isinstance(parsed, str):
                return parsed
            project, task, tag, engine = parsed
            if not task:
                return ("Usage: /queue add [auto|mine] [engine] [<project>:] <task>\n"
                        "e.g. /queue add auto codaur: add a CSV export command")
            jid = night_queue_store.add(project=project, task=task, tag=tag, engine=engine)
            where = "held for you" if tag == "mine" else "queued for tonight"
            return (f"🌙 Job #{jid} {where} — {_pname(project)}: {task[:60]}\n"
                    f"tag {tag} · engine {engine}")

        if cmd in ("ship", "apply"):
            try:
                return _ship(int(rest.strip()))
            except (ValueError, TypeError):
                return "Usage: /queue ship <id>"

        if cmd in ("drop", "rm", "delete"):
            try:
                ok = night_queue_store.drop(int(rest.strip()))
            except (ValueError, TypeError):
                return "Usage: /queue drop <id>"
            return f"🌙 Dropped #{rest.strip()}." if ok else f"🌙 No job #{rest.strip()}."

        if cmd == "tag":
            bits = rest.split()
            if len(bits) != 2 or bits[1].lower() not in _TAGS:
                return "Usage: /queue tag <id> auto|mine"
            try:
                jid = int(bits[0])
            except ValueError:
                return "Usage: /queue tag <id> auto|mine"
            new_tag = bits[1].lower()
            # Flip the queue/held status to match the new tag (only if not started).
            j = night_queue_store.get(jid)
            if not j:
                return f"🌙 No job #{jid}."
            fields = {"tag": new_tag}
            if j["status"] in ("queued", "held"):
                fields["status"] = "held" if new_tag == "mine" else "queued"
            night_queue_store.update(jid, **fields)
            return f"🌙 Job #{jid} is now '{new_tag}'."

        if cmd == "show":
            try:
                return _show_view(int(rest.strip()))
            except (ValueError, TypeError):
                return "Usage: /queue show <id>"

        return self.description


register(QueueSkill())
