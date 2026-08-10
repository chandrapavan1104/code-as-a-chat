"""Night Shift — the overnight autonomous build runner.

Started as its own asyncio task from main.py's lifespan (separate from the
reminder scheduler so neither blocks the other). While inside the night window it
keeps up to one job per engine in flight, so all three coding subscriptions build
in parallel. Engine selection is quota-aware (read live from the `usage`/codaur
snapshot): an engine at/over NIGHT_QUOTA_STOP_PCT of its rate-limit window sits
out until the window resets.

Each coding job runs in its own managed Git worktree on a throwaway
`night/<id>-<slug>` branch (never in the owner's live checkout):
  • app-only change to THIS repo (clients/gajala/**)  → build + deploy the APK,
    status `deployed` (branch still needs `/queue ship` to merge).
  • server / mixed change to THIS repo                → `staged` (gated).
  • any other project                                 → `staged` (gated).
`auto` jobs continue through the verified deployment coordinator; `mine` jobs
remain staged until `/queue ship <id>`.

Safety brakes (independent): the night window, NIGHT_MAX_JOBS per night, an
optional NIGHT_TOKEN_BUDGET, the per-job timeout, and quota benching. A per-repo
lock keeps parallel jobs that target the SAME repo from corrupting each other's
git state while jobs on different projects still run concurrently.
"""

import asyncio
import datetime as dt
import logging
import os
import re
import time
from pathlib import Path

from server import config, night_exec
from server.db import cli_runs_store, night_queue_store, routing_recommendations_store

log = logging.getLogger("night_shift")

# One lock per repo path: same-repo jobs serialize, cross-repo jobs run parallel.
_repo_locks: dict[str, asyncio.Lock] = {}
# Engine name → the in-flight worker task (so we keep at most one job per engine).
_workers: dict[str, asyncio.Task] = {}
# job id → {"proc": <subprocess or None>}: lets stop_job() kill a live build.
_running: dict[int, dict] = {}
# Jobs the app asked to stop; the running pipeline notices and cleans up.
_stop_requested: set[int] = set()
# Night-window bookkeeping so the morning report fires exactly once per night.
_state: dict = {"night_started_at": None, "reported": False}

_BACKLOG_DIR = Path.home() / ".codeasachat" / "backlogs"
_WORKTREE_DIR = Path.home() / ".codeasachat" / "night_worktrees"


# ── config helpers (read through prefs so the app can change them live) ────────

def _settings() -> dict:
    from server import prefs
    return prefs.night_settings()


def _enabled() -> bool:
    return bool(_settings().get("enabled"))


def _engines() -> list[str]:
    raw = _settings().get("engines") or "claude,codex,gemini"
    return [e.strip() for e in str(raw).split(",") if e.strip()]


def _parse_hhmm(s: str, default: dt.time) -> dt.time:
    try:
        h, m = s.split(":")
        return dt.time(int(h), int(m))
    except (ValueError, AttributeError):
        return default


def _in_window(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now()
    s = _settings()
    start = _parse_hhmm(s.get("start", "23:00"), dt.time(23, 0))
    end = _parse_hhmm(s.get("end", "07:00"), dt.time(7, 0))
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end   # window wraps past midnight


def _repo_lock(repo: str) -> asyncio.Lock:
    return _repo_locks.setdefault(repo, asyncio.Lock())


# ── quota (which engines have headroom tonight) ───────────────────────────────

async def _engine_usage_pct() -> dict[str, float]:
    """Best-effort per-engine 'percent of its current window used', from the same
    codaur read the /usage skill uses. Unknown/unreadable → 0 (treated as free)."""
    try:
        from server.skills.usage import _billable, _fetch
        payload = await _fetch("all")
    except Exception:
        return {}
    if not payload:
        return {}

    pct: dict[str, float] = {}
    for rep in payload.get("reports", []) or []:
        prov = rep.get("provider")
        if prov == "codex":
            snap = rep.get("latestRateLimitSnapshot") or {}
            vals = [w.get("used_percent", 0) for w in
                    (snap.get("primary"), snap.get("secondary")) if w]
            pct["codex"] = float(max(vals)) if vals else 0.0
        elif prov in ("claude", "gemini"):
            budget = (config.USAGE_BUDGET_CLAUDE if prov == "claude"
                      else config.USAGE_BUDGET_GEMINI)
            today = _billable((rep.get("totals") or {}).get("todayUsage"))
            pct[prov] = (today / budget * 100.0) if budget else 0.0
    return pct


def _available_engines(usage_pct: dict[str, float]) -> list[str]:
    stop = _settings().get("quota_stop_pct", 85)
    free = []
    for eng in _engines():
        if eng in _workers and not _workers[eng].done():
            continue                       # already building something
        if usage_pct.get(eng, 0.0) >= stop:
            continue                       # benched until its window resets
        free.append(eng)
    return free


# ── backlog (queue-empty fallback) ────────────────────────────────────────────

def _project_name(path: str) -> str:
    return Path(path).name


def _backlog_path_for(project_dir: str) -> Path:
    return _BACKLOG_DIR / f"{_project_name(project_dir)}.md"


def _next_backlog_job() -> dict | None:
    """Find the first project backlog with an undone line, turn it into a claimed
    job. A line is 'done' if blank, a comment (#), or already checked ('- [x]')."""
    if not _BACKLOG_DIR.is_dir():
        return None
    from server.skills.projects import _resolve
    for f in sorted(_BACKLOG_DIR.glob("*.md")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or line.lower().startswith("- [x]"):
                continue
            task = re.sub(r"^-\s*\[\s*\]\s*", "", line).lstrip("-* ").strip()
            if not task:
                continue
            repo = _resolve(f.stem)
            if repo is None:
                break   # backlog file with no matching project — skip this file
            jid = night_queue_store.add(
                project=str(repo), task=task, tag="auto", engine="auto",
                origin="backlog",
            )
            return _claim_specific(jid)
    return None


def _claim_specific(job_id: int) -> dict | None:
    """Mark a just-created backlog job running (it's ours, no contention)."""
    night_queue_store.update(job_id, status="running", started_at=time.time())
    return night_queue_store.get(job_id)


def _mark_backlog_done(project_dir: str, task: str) -> None:
    f = _backlog_path_for(project_dir)
    try:
        lines = f.read_text().splitlines()
    except OSError:
        return
    out, changed = [], False
    for raw in lines:
        stripped = re.sub(r"^-\s*\[\s*\]\s*", "", raw.strip()).lstrip("-* ").strip()
        if not changed and stripped == task:
            out.append(f"- [x] {task}")
            changed = True
        else:
            out.append(raw)
    if changed:
        try:
            f.write_text("\n".join(out) + "\n")
        except OSError:
            pass


# ── git helpers ───────────────────────────────────────────────────────────────

async def _git(repo: str, *args: str, timeout: int = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", repo, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 124, f"(git {args[0]} timed out)"
    return proc.returncode, out.decode(errors="replace")


def _slug(text: str, n: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n] or "job"


def _changed_files(porcelain: str) -> list[str]:
    return [ln[3:].strip().strip('"') for ln in porcelain.splitlines() if len(ln) > 3]


def _is_this_repo(repo: str) -> bool:
    try:
        return Path(repo).resolve() == Path(config.REPO_DIR).resolve()
    except OSError:
        return False


async def _discard_worktree(repo: str, worktree: Path, branch: str,
                            *, delete_branch: bool) -> None:
    """Remove only Night Shift's managed checkout, optionally its job branch."""
    await _git(repo, "worktree", "remove", "--force", str(worktree))
    await _git(repo, "worktree", "prune")
    if delete_branch:
        await _git(repo, "branch", "-D", branch)


async def _prepare_worktree(repo: str, jid: int, branch: str,
                            base: str) -> tuple[Path | None, str]:
    """Create a clean checkout without switching or cleaning the live repo."""
    _WORKTREE_DIR.mkdir(parents=True, exist_ok=True)
    worktree = _WORKTREE_DIR / f"job-{jid}"
    # A stopped/crashed prior attempt can leave either artifact behind. Both are
    # Night Shift-owned and safe to replace for the same durable queue id.
    await _discard_worktree(repo, worktree, branch, delete_branch=True)
    rc, output = await _git(
        repo, "worktree", "add", "-b", branch, str(worktree), base, timeout=60)
    if rc != 0:
        return None, output.strip()[:500]
    return worktree, ""


# ── the per-job pipeline ──────────────────────────────────────────────────────

async def _process_job(job: dict, engine: str) -> None:
    repo = job["project"]
    jid = job["id"]
    _running[jid] = {"proc": None}
    try:
        async with _repo_lock(str(Path(repo).resolve())):
            await _process_job_locked(job, engine, repo, jid)
    finally:
        _running.pop(jid, None)
        _stop_requested.discard(jid)


async def _process_job_locked(job: dict, engine: str, repo: str, jid: int) -> None:
    if not Path(repo).is_dir():
        night_queue_store.update(jid, status="failed", ended_at=time.time(),
                                 summary=f"project path is gone: {repo}")
        await _notify_status(jid, job, "failed", f"project path is gone: {repo}")
        return

    if (job.get("spec_json") or {}).get("work_type") == "research":
        await _process_research_job(job, engine, repo, jid)
        return

    rc, status = await _git(repo, "rev-parse", "--git-dir")
    if rc != 0:
        night_queue_store.update(jid, status="failed", ended_at=time.time(),
                                 summary=f"not a git repo ({status.strip()[:160]})")
        await _notify_status(jid, job, "failed", "not a git repo")
        return

    rc, base = await _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    base = base.strip() or "main"
    if rc != 0:
        night_queue_store.update(jid, status="failed", ended_at=time.time(),
                                 summary="Could not determine the repository base branch.")
        await _notify_status(jid, job, "failed", "could not determine base branch")
        return
    branch = f"night/{jid}-{_slug(job['task'])}"
    worktree, worktree_error = await _prepare_worktree(repo, jid, branch, base)
    if worktree is None:
        msg = f"Could not create the isolated job checkout: {worktree_error}"
        night_queue_store.update(jid, status="failed", ended_at=time.time(),
                                 summary=msg)
        await _notify_status(jid, job, "failed", msg)
        return

    try:
        started = time.time()

        def _hold(proc):
            if jid in _running:
                _running[jid]["proc"] = proc

        summary, total, billable, error = await night_exec.run_job(
            engine, str(worktree), job["task"],
            getattr(config, "NIGHT_JOB_TIMEOUT", 1800),
            on_spawn=_hold)
        _record_run(repo, engine, started, error, total, billable)

        # Stop requested mid-run: the subprocess was killed. Clean up and park it.
        if jid in _stop_requested:
            await _discard_worktree(repo, worktree, branch, delete_branch=True)
            night_queue_store.update(jid, status="stopped", ended_at=time.time(),
                                     summary="Stopped by you.")
            return

        _, status2 = await _git(str(worktree), "status", "--porcelain")
        changed = _changed_files(status2)

        # No change: the agent either errored, or deliberately stopped to ask you
        # a question (a design/product decision). The latter becomes an interactive
        # 'awaiting_input' item — answer it and the job re-runs with your answer.
        if not changed:
            await _discard_worktree(repo, worktree, branch, delete_branch=True)
            if error:
                night_queue_store.update(
                    jid, status="failed", tokens_total=total,
                    tokens_billable=billable, engine_used=engine,
                    ended_at=time.time(),
                    summary=f"Run error: {error}\n\n{summary}".strip())
                await _notify_status(jid, job, "failed", error)
            else:
                question = summary or "I need a decision before I can build this."
                night_queue_store.update(
                    jid, status="awaiting_input", tokens_total=total,
                    tokens_billable=billable, engine_used=engine,
                    ended_at=time.time(), summary=question)
                await _notify_input(jid, job, question)
            return

        await _git(str(worktree), "add", "-A")
        commit_rc, commit_output = await _git(
            str(worktree), "commit", "-m", f"night(agent): {job['task'][:60]}",
            timeout=30)
        if commit_rc != 0:
            raise RuntimeError(f"could not commit job changes: {commit_output.strip()[:300]}")

        this_repo = _is_this_repo(repo)
        app_only = this_repo and all(f.startswith("clients/gajala/") for f in changed)

        final_status = "staged"
        deploy_note = ""
        deployed_ok = False
        if app_only:
            # Build from the isolated checkout so the APK contains this branch,
            # while the owner's active checkout remains completely untouched.
            from server.skills.build_app import build_and_deploy
            deployed_ok, msg = await build_and_deploy(
                timeout=getattr(config, "NIGHT_JOB_TIMEOUT", 1800),
                source_repo=worktree)
            deploy_note = msg
            final_status = "deployed" if deployed_ok else "staged"

        await _discard_worktree(repo, worktree, branch, delete_branch=False)

        rc, diffstat = await _git(repo, "diff", f"{base}..{branch}", "--stat")
        night_queue_store.update(
            jid, status=final_status, branch=branch, base=base,
            files_changed=changed, engine_used=engine,
            tokens_total=total, tokens_billable=billable, ended_at=time.time(),
            summary=("\n\n".join(p for p in (summary, deploy_note, diffstat.strip()[:600])
                                 if p)).strip())

        if job.get("origin") == "backlog":
            _mark_backlog_done(repo, job["task"])

        await _notify_status(jid, job, final_status, summary or "", deployed=deployed_ok)

        # `auto` is end-to-end authorization: after an agent produces a committed
        # branch, merge/deploy it through the serialized verifier. A busy deploy
        # leaves this staged; _cycle retries it durably on the next tick.
        if job.get("tag") == "auto":
            from server.skills.queue import _ship
            try:
                _ship(jid)
            except Exception as exc:  # branch is safely staged; next tick retries
                log.warning("automatic deploy of #%s deferred: %s", jid, exc)
    except Exception as exc:  # noqa: BLE001 — never let one job kill the runner
        log.error("night job %s crashed: %s", jid, exc)
        await _discard_worktree(repo, worktree, branch, delete_branch=True)
        night_queue_store.update(jid, status="failed", ended_at=time.time(),
                                 summary=f"night job crashed: {exc}")
        await _notify_status(jid, job, "failed", str(exc))


async def _process_research_job(
        job: dict, engine: str, cwd: str, jid: int) -> None:
    """Research produces a report, so it needs neither Git nor changed files."""
    started = time.time()

    def _hold(proc):
        if jid in _running:
            _running[jid]["proc"] = proc

    summary, total, billable, error = await night_exec.run_research_job(
        engine, cwd, job["task"], getattr(config, "NIGHT_JOB_TIMEOUT", 1800),
        on_spawn=_hold,
    )
    _record_run(cwd, engine, started, error, total, billable)
    if jid in _stop_requested:
        night_queue_store.update(
            jid, status="stopped", ended_at=time.time(), summary="Stopped by you."
        )
        return
    if error:
        reason = f"Research agent error: {error}"
        night_queue_store.update(
            jid, status="failed", engine_used=engine, tokens_total=total,
            tokens_billable=billable, ended_at=time.time(),
            summary=f"{reason}\n\n{summary}".strip(),
        )
        await _notify_status(jid, job, "failed", reason)
        return
    if not summary.strip():
        reason = "Research agent returned no report."
        night_queue_store.update(
            jid, status="failed", engine_used=engine, ended_at=time.time(),
            summary=reason,
        )
        await _notify_status(jid, job, "failed", reason)
        return
    night_queue_store.update(
        jid, status="completed", engine_used=engine, tokens_total=total,
        tokens_billable=billable, ended_at=time.time(), summary=summary.strip(),
    )
    await _notify_status(jid, job, "completed", "Research report is ready.")


# ── inbox notifications for job outcomes ──────────────────────────────────────

async def _notify_input(jid: int, job: dict, question: str) -> None:
    from server.notifier import notify_app
    await notify_app(
        "queue_input",
        title=f"Task #{jid} needs your input",
        body=f"{_project_name(job['project'])}: {job['task'][:80]}\n\n{question}",
        needs_response=True, ref_kind="queue_job", ref_id=jid, telegram=True)


async def _notify_status(jid: int, job: dict, status: str, detail: str = "",
                         deployed: bool = False) -> None:
    from server.notifier import notify_app
    icon = {"deployed": "📦", "staged": "⏸", "completed": "✅", "failed": "⚠️",
            "needs_you": "🙋"}.get(status, "•")
    verb = {"deployed": "deployed — test on phone", "completed": "completed — report ready",
            "staged": "staged — ship when ready",
            "failed": "failed", "needs_you": "needs you"}.get(status, status)
    title = f"{icon} Task #{jid} {verb}"
    body = f"{_project_name(job['project'])}: {job['task'][:80]}"
    if detail:
        prefix = "Reason: " if status == "failed" else ""
        body += f"\n\n{prefix}{detail[:200]}"
    await notify_app("queue_status", title=title, body=body,
                     ref_kind="queue_job", ref_id=jid)
    # An app-only deploy means a fresh installable build is waiting.
    if deployed and _is_this_repo(job["project"]):
        await notify_app(
            "gajala_update", title="⬆️ New Gajala build ready",
            body="A night task built and deployed an app change. Tap to update.")


def _record_run(repo: str, engine: str, started: float, error: str | None,
                total: int, billable: int) -> None:
    try:
        cli_runs_store.add(
            workspace=repo, engine=engine, cli_session_id=None, source="night",
            started_at=started, status=("error" if error else "success"),
            total_tokens=total, billable_tokens=billable)
    except Exception:
        pass


# ── on-demand control (used by the app / API) ─────────────────────────────────

# Holds strong refs to run-now tasks so the loop doesn't GC them mid-build.
_adhoc_tasks: set = set()


def respond_to_job(job_id: int, answer: str) -> dict | None:
    """Feed your answer to a job that stopped to ask a question, and re-queue it so
    it proceeds with your decision. Returns the updated job (or None if unknown)."""
    job = night_queue_store.get(job_id)
    if job is None:
        return None
    answer = (answer or "").strip()
    new_task = job["task"]
    if answer:
        new_task = f"{job['task']}\n\nOwner's answer to your question: {answer}"
    night_queue_store.update(job_id, task=new_task, status="queued",
                             summary=f"You answered: {answer[:200]}")
    return night_queue_store.get(job_id)


def _record_shadow_recommendation(job: dict, usage_pct: dict[str, float]) -> None:
    """SHADOW MODE: compute and record a routing recommendation without changing
    the live assignment. This lets us evaluate the dispatcher before activation."""
    try:
        from server import routing_dispatcher

        job_id = job["id"]
        spec_dict = job.get("spec_json") or {}

        decision = routing_dispatcher.route_work_order(
            spec_dict=spec_dict,
            job_id=job_id,
            configured_engines=_engines(),
            usage_pct=usage_pct,
            pinned_engine=(job.get("engine") or "auto").lower(),
            quota_stop_pct=_settings().get("quota_stop_pct", 85),
        )

        routing_recommendations_store.record(
            job_id=job_id,
            recommended_engine=decision.recommended_engine,
            alternatives=decision.alternatives,
            scores=decision.scores,
            quota_snapshot=usage_pct,
            confidence=decision.confidence,
            rationale=decision.rationale,
            features_summary=decision.features_summary,
        )
    except Exception as e:
        log.debug("shadow recommendation for job #%d failed: %s", job["id"], e)


def _pick_engine(job: dict, usage_pct: dict[str, float] | None = None) -> str:
    """Which engine should run this job now? Its pinned engine if configured, else
    the first configured engine with quota headroom, else the first configured.

    SHADOW MODE: also records a routing recommendation for offline evaluation."""
    configured = _engines() or ["claude"]
    pinned = (job.get("engine") or "auto").lower()

    # Record shadow recommendation (does not affect live assignment)
    if usage_pct:
        _record_shadow_recommendation(job, usage_pct)

    if pinned in configured:
        return pinned
    stop = _settings().get("quota_stop_pct", 85)
    for eng in configured:
        if (usage_pct or {}).get(eng, 0.0) < stop:
            return eng
    return configured[0]


async def run_now(job_id: int) -> dict | None:
    """Dispatch a specific job immediately — ignores the night window and works
    even when Night Shift is disabled, so the queue is usable any time."""
    job = night_queue_store.get(job_id)
    if job is None:
        return None
    if job["status"] == "closed":
        return job
    if not night_queue_store.is_refined(job):
        return job
    if night_queue_store.blocked_by(job):
        return job
    if job_id in _running:
        return job                      # already building
    usage_pct = await _engine_usage_pct()
    engine = _pick_engine(job, usage_pct)
    night_queue_store.update(job_id, status="running", started_at=time.time(),
                             engine_used=engine)
    fresh = night_queue_store.get(job_id)
    task = asyncio.create_task(_process_job(fresh, engine))
    _adhoc_tasks.add(task)
    task.add_done_callback(_adhoc_tasks.discard)
    return fresh


def stop_job(job_id: int) -> bool:
    """Stop a job: kill a live build (it cleans up + parks as 'stopped'), or just
    unqueue one that hasn't started. Idempotent / safe when nothing is running."""
    job = night_queue_store.get(job_id)
    if job is None:
        return False
    if job_id in _running:
        _stop_requested.add(job_id)
        proc = _running[job_id].get("proc")
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        return True
    if job["status"] in ("queued", "running"):
        night_queue_store.update(job_id, status="stopped", ended_at=time.time(),
                                 summary="Stopped by you.")
        return True
    return False


# ── dispatch loop ─────────────────────────────────────────────────────────────

def _jobs_tonight() -> list[dict]:
    if _state["night_started_at"] is None:
        return []
    return night_queue_store.list_since(_state["night_started_at"])


def _budget_ok(tonight: list[dict]) -> bool:
    s = _settings()
    cap = s.get("max_jobs", 12)
    if len(tonight) >= cap:
        return False
    token_budget = s.get("token_budget", 0)
    if token_budget:
        spent = sum(j.get("tokens_total", 0) or 0 for j in tonight)
        if spent >= token_budget:
            return False
    return True


async def _engine_worker(engine: str) -> None:
    try:
        job = night_queue_store.claim_next(engine)
        if job is None:
            job = _next_backlog_job()      # queue empty → keep quota busy
        if job is None:
            return
        log.info("night: %s → job #%s (%s) in %s",
                 engine, job["id"], job.get("origin"), job["project"])
        await _process_job(job, engine)
    except Exception as exc:  # a stray worker failure must not go unretrieved
        log.error("night worker (%s) failed: %s", engine, exc)


async def _cycle() -> None:
    # Finish previously staged automatic work before spending quota on more jobs.
    from server.skills.queue import _ship
    for ready in night_queue_store.list_jobs(status="staged,deployed"):
        if ready.get("tag") == "auto":
            result = _ship(ready["id"])
            if "already" in result or "queued behind" in result:
                break
    tonight = _jobs_tonight()
    if not _budget_ok(tonight):
        return
    usage_pct = await _engine_usage_pct()
    for engine in _available_engines(usage_pct):
        if not _budget_ok(_jobs_tonight()):
            break
        _workers[engine] = asyncio.create_task(_engine_worker(engine))


# ── morning report ────────────────────────────────────────────────────────────

async def _morning_report(tonight: list[dict]) -> None:
    if not tonight:
        return
    by: dict[str, list[dict]] = {}
    for j in tonight:
        by.setdefault(j["status"], []).append(j)

    def _n(s: str) -> int:
        return len(by.get(s, []))

    tok: dict[str, int] = {}
    for j in tonight:
        tok[j.get("engine_used") or "?"] = tok.get(j.get("engine_used") or "?", 0) \
            + (j.get("tokens_total", 0) or 0)
    tok_line = " · ".join(f"{e} {t/1e6:.1f}M" for e, t in tok.items() if e != "?") \
        or "no tokens recorded"

    lines = ["🌙 Night Shift report", ""]
    if by.get("deployed"):
        lines.append(f"📦 Deployed (app, test on phone): {_n('deployed')}")
    if by.get("staged"):
        lines.append(f"⏸ Staged — /queue ship <id>: {_n('staged')}")
        for j in by["staged"][:6]:
            lines.append(f"   #{j['id']} {_project_name(j['project'])}: {j['task'][:48]}")
    if by.get("needs_you"):
        lines.append(f"🙋 Needs you (a decision): {_n('needs_you')}")
    if by.get("failed"):
        lines.append(f"⚠️ Failed: {_n('failed')}")
    lines += ["", f"tokens: {tok_line}", "Open Tasks to review."]
    body = "\n".join(lines)

    # One inbox row + push + Telegram, all in sync, via the shared notifier.
    from server.notifier import notify_app
    try:
        await notify_app("night_report", title="🌙 Night Shift report",
                         body=body, telegram=True)
    except Exception:
        pass
    log.info("night: morning report sent (%d jobs)", len(tonight))


# ── the loop ──────────────────────────────────────────────────────────────────

async def night_shift_loop() -> None:
    tick = getattr(config, "NIGHT_TICK", 120)
    log.info("Night Shift loop started (enabled=%s, window=%s-%s, tick=%ss)",
             _enabled(), getattr(config, "NIGHT_START", "23:00"),
             getattr(config, "NIGHT_END", "07:00"), tick)
    while True:
        try:
            if _enabled() and _in_window():
                if _state["night_started_at"] is None:
                    _state["night_started_at"] = time.time()
                    _state["reported"] = False
                    log.info("night: window opened")
                await _cycle()
            else:
                # Window just closed (or disabled mid-night): report once.
                if _state["night_started_at"] is not None and not _state["reported"]:
                    await _morning_report(_jobs_tonight())
                    _state["reported"] = True
                    _state["night_started_at"] = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one tick kill the loop
            log.error("night tick error: %s", exc)
        await asyncio.sleep(tick)


# Escape hatch for tests / manual runs: force the backlog dir.
def _set_backlog_dir(path: os.PathLike) -> None:
    global _BACKLOG_DIR
    _BACKLOG_DIR = Path(path)
