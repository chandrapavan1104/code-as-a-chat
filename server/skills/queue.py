"""
queue skill — the Night Shift work queue you drive from the phone.

Queue build tasks during the day; the overnight runner (server/night_shift.py)
builds them across all three coding subscriptions on isolated branches. `auto`
jobs deploy through the verified coordinator; `mine` jobs wait for review.

Subcommands (passed via prompt):
  add [auto|mine] [engine] [<project>:] <task>   queue a job (defaults: auto, any
                                                 engine, active project)
  (empty) | list                                 pending + in-progress jobs
  review                                          last night's results + how to ship
  ship <id>                                       merge a staged/deployed job's branch
  refine <id> confirm [instructions]              Claude-expands/reworks a job
  close <id> <reason>                             recoverably close a job
  reopen <id>                                     restore a closed job as held
  tag <id> auto|mine                             flag for the runner / hold for you
  engine <id> auto|claude|codex|gemini           choose that job's runner
  backlog [<project>:] <task>                     append to a project's backlog file
  status                                          runner window + enabled state
"""

from pathlib import Path

from server import config
from server.db import deployment_store, night_queue_store
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
    title = (j.get("spec_json") or {}).get("title") or j["task"]
    blocked = f" · blocked by {night_queue_store.blocked_by(j)}" if night_queue_store.blocked_by(j) else ""
    readiness = (j.get("spec_json") or {}).get("readiness", "draft")
    return (f"  #{j['id']} [{readiness}·{j['tag']}·{eng}] "
            f"{_pname(j['project'])}: {title[:56]}{blocked}")


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
    done = night_queue_store.list_jobs(status="completed,deployed,staged,needs_you,failed")
    if not done:
        return "🌙 Nothing to review yet — no completed night jobs."
    buckets: dict[str, list[dict]] = {
        "completed": [], "deployed": [], "staged": [], "needs_you": [], "failed": []
    }
    for j in done:
        buckets[j["status"]].append(j)
    out = ["🌙 NIGHT SHIFT — REVIEW"]
    if buckets["completed"]:
        out += ["", "✅ Completed reports:"] + [_line(j) for j in buckets["completed"]]
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
    spec = j.get("spec_json") or {}
    lines = [
        f"🌙 JOB #{j['id']}  [{j['status']}]",
        f"project: {_pname(j['project'])}  ({j['project']})",
        f"title:   {spec.get('title') or j['task']}",
        f"tag/engine: {j['tag']} · {j.get('engine_used') or j['engine']}",
        f"readiness: {(j.get('spec_json') or {}).get('readiness', 'draft')}",
    ]
    for label, key in (("outcome", "outcome"), ("context", "context"),
                       ("policy", "policy"), ("testing", "test_handoff"),
                       ("out of scope", "out_of_scope")):
        if spec.get(key):
            lines += ["", f"{label}:\n{spec[key]}"]
    if spec.get("plan"):
        lines += ["", "approved plan:"] + [f"  {i}. {step}" for i, step in enumerate(spec["plan"], 1)]
    if spec.get("acceptance"):
        lines += ["", "acceptance:"] + [f"  • {item}" for item in spec["acceptance"]]
    if j.get("depends_on"):
        lines.append(
            f"depends on: {j['depends_on']} "
            f"(waiting for ship/completion: {night_queue_store.blocked_by(j)})"
        )
    if j.get("close_reason"):
        lines.append(f"closed:  {j['close_reason']}")
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

def _ship(job_id: int, source_base_sha: str | None = None) -> str:
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

    # A rejected push leaves the exact job delta in the deployment ledger. On
    # retry, replay that delta onto the published base instead of merging the
    # rejected deployment's stale ancestry back into main.
    latest = deployment_store.latest(source="queue", ref_id=job_id)
    if (not source_base_sha and latest
            and latest.get("state") == "push_failed"):
        source_base_sha = latest.get("source_base_sha") or latest.get("before_sha")

    from server.deployment import deploy_branch
    return "🌙 " + deploy_branch(
        repo=repo, branch=branch, base=base, changed_files=files,
        source="queue", ref_id=job_id, server_touched=server_touched,
        source_base_sha=source_base_sha)


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
                   "review | refine <id> confirm [instructions] | engine <id> auto|claude|codex|gemini | "
                   "ship <id> | close <id> <reason> | reopen <id>")
    final_output = True
    aliases = ["night"]
    agent_doc = (
        "Overnight autonomous build queue ('Night Shift'). The user queues coding "
        "tasks to be built overnight across claude/codex/gemini on isolated "
        "branches. Route here when they want to QUEUE work for later/overnight, "
        "review what was built, or ship/merge a night job. args: "
        "'add [auto|mine] [engine] [<project>:] <task>' | '' or 'list' | 'review' | "
        "'refine <id> confirm [instructions]' | 'ship <id>' | "
        "'engine <id> auto|claude|codex|gemini' | "
        "'close <id> <reason>' | 'reopen <id>' | "
        "'tag <id> auto|mine' | 'backlog <project>: <task>' | "
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
            saved = night_queue_store.get(jid)
            draft = not night_queue_store.is_refined(saved)
            where = "saved as a draft — use /queue refine " + str(jid) if draft else (
                "held for you" if saved["tag"] == "mine" else "queued for tonight")
            return (f"🌙 Job #{jid} {where} — {_pname(project)}: {task[:60]}\n"
                    f"tag {saved['tag']} · engine {engine}")

        if cmd == "refine":
            bits = rest.strip().split()
            try:
                jid = int(bits[0])
            except (ValueError, TypeError, IndexError):
                return "Usage: /queue refine <id> confirm"
            if len(bits) < 2 or bits[1].lower() != "confirm":
                return (f"🌙 Refining #{jid} sends only its rough task text to "
                        "Claude Sonnet (no repo files or paths). If approved, use: "
                        f"/queue refine {jid} confirm")
            from server.work_order_refiner import refine_job
            try:
                instructions = " ".join(bits[2:])
                job = await refine_job(
                    jid, allow_cloud=True, instructions=instructions)
            except (PermissionError, LookupError, ValueError, RuntimeError) as exc:
                return f"🌙 Could not refine #{jid}: {exc}"
            return (f"🌙 Refined #{jid}: {(job.get('spec_json') or {}).get('title')}\n"
                    "Held for your review — inspect it, then make it auto when ready.")

        if cmd in ("ship", "apply"):
            try:
                return _ship(int(rest.strip()))
            except (ValueError, TypeError):
                return "Usage: /queue ship <id>"

        if cmd == "close":
            bits = rest.strip().split(maxsplit=1)
            if len(bits) != 2:
                return "Usage: /queue close <id> <reason>"
            try:
                ok = night_queue_store.close(int(bits[0]), bits[1])
            except (ValueError, TypeError):
                return "Usage: /queue close <id> <reason> (stop running jobs first)"
            return f"🌙 Closed #{bits[0]} recoverably." if ok else f"🌙 No job #{bits[0]}."

        if cmd == "reopen":
            try:
                ok = night_queue_store.reopen(int(rest.strip()))
            except (ValueError, TypeError):
                return "Usage: /queue reopen <id>"
            return (f"🌙 Reopened #{rest.strip()} as held."
                    if ok else f"🌙 Job #{rest.strip()} is not closed.")

        if cmd in ("drop", "rm", "delete"):
            return "Permanent deletion is disabled. Use /queue close <id> <reason>."

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
            if new_tag == "auto" and not night_queue_store.is_refined(j):
                return f"🌙 Job #{jid} is a draft. Refine it before making it auto."
            fields = {"tag": new_tag}
            if j["status"] in ("queued", "held"):
                fields["status"] = "held" if new_tag == "mine" else "queued"
            night_queue_store.update(jid, **fields)
            return f"🌙 Job #{jid} is now '{new_tag}'."

        if cmd == "engine":
            bits = rest.split()
            if len(bits) != 2 or bits[1].lower() not in ("auto", *_ENGINES):
                return "Usage: /queue engine <id> auto|claude|codex|gemini"
            try:
                jid = int(bits[0])
            except ValueError:
                return "Usage: /queue engine <id> auto|claude|codex|gemini"
            job = night_queue_store.get(jid)
            if not job:
                return f"🌙 No job #{jid}."
            if job["status"] in ("running", "closed"):
                return f"🌙 Cannot change engine on a {job['status']} job."
            engine = bits[1].lower()
            night_queue_store.update(jid, engine=engine, engine_used=None)
            note = "Gajala will choose using quota/availability" if engine == "auto" else "pinned"
            return f"🌙 Job #{jid} engine: {engine} ({note})."

        if cmd == "show":
            try:
                return _show_view(int(rest.strip()))
            except (ValueError, TypeError):
                return "Usage: /queue show <id>"

        return self.description


register(QueueSkill())
