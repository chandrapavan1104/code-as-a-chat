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
import inspect
import os
import re
import shutil
import time
from pathlib import Path

from server import config, night_exec
from server.db import (cli_runs_store, deployment_store, night_queue_store,
                       routing_recommendations_store)

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
        stdin=asyncio.subprocess.DEVNULL,
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
        current = night_queue_store.get(jid)
        if current and current.get("status") == "running":
            reason = ("Worker exited without reporting a result. The job was "
                      "marked failed instead of remaining stuck in Building.")
            night_queue_store.update(jid, status="failed", ended_at=time.time(),
                                     summary=reason, failure_kind="worker_lost",
                                     blocker_reason=reason,
                                     next_action=_failure_action(current, "worker_lost"))
            attempts = night_queue_store.attempts(jid, include_logs=False)
            if attempts and attempts[-1]["status"] == "running":
                night_queue_store.finish_attempt(
                    attempts[-1]["id"], status="failed", stage=attempts[-1]["stage"],
                    error=reason)
            night_queue_store.save_result(jid, kind="failure", text=reason,
                                          completeness="partial")


def _failure_kind(text: str, stage: str) -> str:
    lowered = (text or "").lower()
    if "not a git repo" in lowered or "not a git repository" in lowered:
        return "invalid_repo"
    if "project path is gone" in lowered or "no such file or directory" in lowered:
        return "project_missing"
    if "could not determine the repository base branch" in lowered:
        return "git_base_error"
    if "could not create the isolated job checkout" in lowered:
        return "worktree_setup"
    if "timed out" in lowered or "ran past" in lowered or "timeout" in lowered:
        return "timeout"
    if "auth" in lowered or "unauthorized" in lowered:
        return "authentication"
    if stage == "preflight":
        return "preflight_error"
    return "worker_error"


def _result_kind_for_status(status: str, work_type: str) -> str:
    if status == "failed": return "failure"
    if status in {"blocked", "awaiting_input", "needs_you"}: return "question"
    return "research" if work_type == "research" else "coding"


_FILE_MARKER = re.compile(r"\[file:\s*([^\]\r\n]+)\]")
MAX_WORKER_FILE_BYTES = 200 * 1024 * 1024


def _stage_explicit_file_markers(text: str, worktree: Path, job_id: int,
                                 attempt_no: int) -> tuple[str, list[dict]]:
    """Stage only worker-declared files from its isolated checkout for phone download."""
    from server.media import UPLOADS_DIR, ensure_uploads_dir, is_served_path
    artifacts: list[dict] = []
    replacements = {}
    root = worktree.resolve()
    target_dir = None
    for match in _FILE_MARKER.finditer(text or ""):
        raw = match.group(1).strip().strip("\"'")
        try:
            source = Path(raw).expanduser().resolve()
            if source.is_file() and is_served_path(source):
                target = source
            else:
                if not source.is_file() or (source != root and root not in source.parents):
                    replacements[match.group(0)] = f"[file unavailable: {source.name or 'unknown'}]"
                    artifacts.append({"kind": "unavailable_file", "name": source.name,
                                     "reason": "File was outside the job worktree or missing."})
                    continue
                size = source.stat().st_size
                if size > MAX_WORKER_FILE_BYTES:
                    replacements[match.group(0)] = f"[file unavailable: {source.name} (over 200 MB)]"
                    artifacts.append({"kind": "unavailable_file", "name": source.name,
                                     "reason": "File exceeds the 200 MB sharing limit."})
                    continue
                if target_dir is None:
                    target_dir = ensure_uploads_dir() / "queue" / str(job_id) / str(attempt_no)
                    target_dir.mkdir(parents=True, exist_ok=True)
                safe_name = source.name.replace("]", "_").replace("\n", "_").replace("\r", "_")
                target = target_dir / (safe_name or "worker-output")
                copied = 0
                with source.open("rb") as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > MAX_WORKER_FILE_BYTES:
                            target.unlink(missing_ok=True)
                            raise ValueError("File grew beyond the 200 MB sharing limit.")
                        dst.write(chunk)
                size = copied
            replacements[match.group(0)] = f"[file: {target}]"
            artifacts.append({"kind": "file", "path": str(target),
                              "name": target.name, "size": target.stat().st_size})
        except (OSError, ValueError) as exc:
            replacements[match.group(0)] = f"[file unavailable: {Path(raw).name}]"
            artifacts.append({"kind": "unavailable_file", "name": Path(raw).name,
                              "reason": str(exc)[:200]})
    for original, replacement in replacements.items():
        text = text.replace(original, replacement)
    return text, artifacts


def _attempt_output_callback(attempt_id: int):
    def record(*, stdout="", stderr="", output="", error=None, exit_code=None):
        night_queue_store.update_attempt(
            attempt_id, stdout=stdout, stderr=stderr, output=output,
            error=error, exit_code=exit_code,
        )
    return record


async def _run_with_attempt_capture(runner, engine: str, cwd: str, task: str,
                                    timeout: int, *, on_spawn, attempt: dict):
    """Use the richer runner hook when available; keep old monkeypatches/callers valid."""
    kwargs = {"on_spawn": on_spawn}
    try:
        params = inspect.signature(runner).parameters.values()
        if any(p.name == "on_result" or p.kind == inspect.Parameter.VAR_KEYWORD
               for p in params):
            kwargs["on_result"] = _attempt_output_callback(attempt["id"])
    except (TypeError, ValueError):
        pass
    result = await runner(engine, cwd, task, timeout, **kwargs)
    if "on_result" not in kwargs:
        output = result[0] if result else ""
        night_queue_store.update_attempt(attempt["id"], output=output)
    return result


def _failure_action(job: dict, kind: str) -> str:
    if kind == "invalid_repo":
        return "Choose a valid Git repository for this coding task, then run it again."
    if kind == "project_missing":
        return "Restore the project folder or choose another project, then run it again."
    if kind == "git_base_error":
        return "Check the repository branch, then run this task again."
    if job.get("tag") == "mine":
        return "Review the failure, correct the project or work order, then run it again."
    return "The supervisor will classify this failure and decide whether a safe retry is possible."


async def _fail_job(job: dict, attempt: dict, reason: str, *, stage: str,
                    kind: str | None = None, output: str = "",
                    stdout: str | None = None, stderr: str | None = None, exit_code: int | None = None,
                    status: str = "failed") -> None:
    kind = kind or _failure_kind(reason, stage)
    completeness = "partial" if output else "none"
    night_queue_store.save_result(
        job["id"], kind="failure" if status == "failed" else "question",
        text=output or reason, completeness=completeness)
    night_queue_store.update(
        job["id"], status=status, engine_used=attempt["engine"], ended_at=time.time(),
        summary=(f"{reason}\n\n{output}".strip() if output and output not in reason else reason),
        failure_kind=kind if status == "failed" else None,
        blocker_reason=reason if status != "failed" else reason,
        next_retry_at=None,
        next_action=_failure_action(job, kind) if status == "failed" else
                    "Answer the question or provide the missing input, then resume this task.",
    )
    night_queue_store.finish_attempt(
        attempt["id"], status="failed" if status == "failed" else "waiting_for_user",
        stage=stage, error=reason, exit_code=exit_code,
        stdout=stdout, stderr=stderr, output=output)


async def _process_job_locked(job: dict, engine: str, repo: str, jid: int) -> None:
    attempt = night_queue_store.begin_attempt(jid, engine)
    worktree: Path | None = None
    branch = f"night/{jid}-{_slug(job['task'])}"
    try:
        if not Path(repo).is_dir():
            reason = f"project path is gone: {repo}"
            await _fail_job(job, attempt, reason, stage="preflight",
                            kind="project_missing")
            await _notify_status(jid, job, "failed", reason)
            return

        worker_task, attachment_error = night_exec.task_with_attachments(job)
        if attachment_error:
            question = f"{attachment_error}. Reattach the file in the original chat and answer this task again."
            night_queue_store.save_result(jid, kind="question", text=question)
            night_queue_store.update(jid, status="awaiting_input", ended_at=time.time(),
                                     blocker_reason=attachment_error,
                                     next_action="Reattach the missing file, then answer the task.",
                                     failure_kind=None, next_retry_at=None, summary=question)
            night_queue_store.finish_attempt(attempt["id"], status="waiting_for_user",
                                             stage="input_validation", error=attachment_error)
            await _notify_input(jid, job, question)
            return

        if (job.get("spec_json") or {}).get("work_type") == "research":
            await _process_research_job(job, engine, repo, jid, attempt=attempt,
                                        worker_task=worker_task)
            return

        night_queue_store.update_attempt(attempt["id"], stage="repository_preflight")
        rc, status = await _git(repo, "rev-parse", "--git-dir")
        if rc != 0:
            reason = f"not a git repo ({status.strip()[:160]})"
            await _fail_job(job, attempt, reason, stage="repository_preflight",
                            kind="invalid_repo", stderr=status)
            await _notify_status(jid, job, "failed", reason)
            return

        rc, base = await _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        base = base.strip() or "main"
        if rc != 0:
            reason = "Could not determine the repository base branch."
            await _fail_job(job, attempt, reason, stage="repository_preflight",
                            kind="git_base_error", stderr=base)
            await _notify_status(jid, job, "failed", reason)
            return

        night_queue_store.update_attempt(attempt["id"], stage="worktree_setup")
        live = deployment_store.latest_live(repo, base)
        base_commit = (live or {}).get("deployed_sha") or base
        worktree, worktree_error = await _prepare_worktree(repo, jid, branch, base_commit)
        if worktree is None:
            reason = f"Could not create the isolated job checkout: {worktree_error}"
            await _fail_job(job, attempt, reason, stage="worktree_setup",
                            kind="worktree_setup", stderr=worktree_error)
            await _notify_status(jid, job, "failed", reason)
            return

        started = time.time()

        def _hold(proc):
            if jid in _running:
                _running[jid]["proc"] = proc

        night_queue_store.update_attempt(attempt["id"], stage="agent_run")
        summary, total, billable, error = await _run_with_attempt_capture(
            night_exec.run_job, engine, str(worktree), worker_task,
            getattr(config, "NIGHT_JOB_TIMEOUT", 1800), on_spawn=_hold, attempt=attempt)
        _record_run(repo, engine, started, error, total, billable)
        summary, file_artifacts = _stage_explicit_file_markers(
            summary, worktree, jid, attempt["attempt_no"])
        night_queue_store.update_attempt(attempt["id"], output=summary)

        if jid in _stop_requested:
            await _discard_worktree(repo, worktree, branch, delete_branch=True)
            night_queue_store.save_result(jid, kind="coding", text=summary,
                                          completeness="partial" if summary else "none")
            night_queue_store.update(jid, status="stopped", ended_at=time.time(),
                                     summary="Stopped by you.", next_retry_at=None,
                                     failure_kind=None, blocker_reason=None,
                                     next_action="Run again when you are ready.")
            night_queue_store.finish_attempt(attempt["id"], status="cancelled",
                                             stage="agent_run", error="Stopped by you.",
                                             output=summary)
            return

        night_queue_store.update_attempt(attempt["id"], stage="inspect_changes")
        _, status2 = await _git(str(worktree), "status", "--porcelain")
        changed = _changed_files(status2)
        if not changed:
            await _discard_worktree(repo, worktree, branch, delete_branch=True)
            if error:
                reason = f"Run error: {error}"
                await _fail_job(job, attempt, reason, stage="agent_run", kind=_failure_kind(error, "agent_run"),
                                output=summary, stdout="", stderr=error)
                night_queue_store.update(jid, tokens_total=total, tokens_billable=billable,
                                         engine_used=engine)
                await _notify_status(jid, job, "failed", error)
            else:
                question = summary or "I need a decision before I can build this."
                night_queue_store.save_result(jid, kind="question", text=question)
                night_queue_store.update(jid, status="awaiting_input", tokens_total=total,
                                         tokens_billable=billable, engine_used=engine,
                                         ended_at=time.time(), summary=question,
                                         blocker_reason="The worker made no changes and needs a decision or more detail.",
                                         next_action="Answer the question, then resume this task.",
                                         failure_kind=None, next_retry_at=None)
                night_queue_store.finish_attempt(attempt["id"], status="waiting_for_user",
                                                 stage="inspect_changes", output=summary)
                await _notify_input(jid, job, question)
            return

        night_queue_store.update_attempt(attempt["id"], stage="commit_changes")
        await _git(str(worktree), "add", "-A")
        commit_rc, commit_output = await _git(
            str(worktree), "commit", "-m", f"night(agent): {job['task'][:60]}", timeout=30)
        if commit_rc != 0:
            raise RuntimeError(f"could not commit job changes: {commit_output.strip()[:300]}")

        this_repo = _is_this_repo(repo)
        app_only = this_repo and all(f.startswith("clients/gajala/") for f in changed)
        final_status = "staged"
        deploy_note = ""
        deployed_ok = False
        if app_only:
            night_queue_store.update_attempt(attempt["id"], stage="build_apk")
            from server.skills.build_app import build_and_deploy
            deployed_ok, msg = await build_and_deploy(
                timeout=getattr(config, "NIGHT_JOB_TIMEOUT", 1800), source_repo=worktree)
            deploy_note = msg
            final_status = "deployed" if deployed_ok else "staged"

        await _discard_worktree(repo, worktree, branch, delete_branch=False)
        worktree = None
        _, diffstat = await _git(repo, "diff", f"{base_commit}..{branch}", "--stat")
        full_result = "\n\n".join(p for p in (summary, deploy_note, diffstat.strip()) if p).strip()
        artifacts = [{"kind": "git_branch", "ref": branch, "files_changed": changed},
                     *file_artifacts]
        night_queue_store.save_result(jid, kind="coding", text=full_result,
                                      completeness="complete" if full_result else "none",
                                      artifacts=artifacts)
        night_queue_store.update(
            jid, status=final_status, branch=branch, base=base,
            files_changed=changed, engine_used=engine,
            tokens_total=total, tokens_billable=billable, ended_at=time.time(),
            summary=full_result, failure_kind=None, blocker_reason=None,
            next_retry_at=None,
            next_action="Implementation is ready for deployment review." if final_status == "staged"
                        else "APK deployed; install and verify it on the phone.")
        night_queue_store.finish_attempt(attempt["id"], status="succeeded",
                                         stage="complete", output=summary)
        if job.get("origin") == "backlog":
            _mark_backlog_done(repo, job["task"])
        await _notify_status(jid, job, final_status, summary or "", deployed=deployed_ok)
        if job.get("tag") == "auto":
            from server.skills.queue import _ship
            try:
                _ship(jid, source_base_sha=base_commit)
            except Exception as exc:
                log.warning("automatic deploy of #%s deferred: %s", jid, exc)
    except asyncio.CancelledError:
        if worktree is not None:
            await _discard_worktree(repo, worktree, branch, delete_branch=True)
        night_queue_store.finish_attempt(attempt["id"], status="cancelled",
                                         stage="cancelled", error="Worker task cancelled.")
        raise
    except Exception as exc:  # noqa: BLE001 — one job must not kill the runner
        log.error("night job %s crashed: %s", jid, exc)
        if worktree is not None:
            try:
                await _discard_worktree(repo, worktree, branch, delete_branch=True)
            except Exception:
                pass
        reason = f"night job crashed: {exc}"
        await _fail_job(job, attempt, reason, stage="runtime",
                        kind=_failure_kind(reason, "runtime"))
        await _notify_status(jid, job, "failed", str(exc))


async def _process_research_job(
        job: dict, engine: str, cwd: str, jid: int, *, attempt: dict | None = None,
        worker_task: str | None = None) -> None:
    """Research produces a durable report and needs no Git worktree."""
    if attempt is None:
        attempt = night_queue_store.begin_attempt(jid, engine)
    started = time.time()

    def _hold(proc):
        if jid in _running:
            _running[jid]["proc"] = proc

    if worker_task is None:
        worker_task, attachment_error = night_exec.task_with_attachments(job)
        if attachment_error:
            await _fail_job(job, attempt, attachment_error, stage="input_validation",
                            kind="attachment_missing", status="awaiting_input")
            return
    night_queue_store.update_attempt(attempt["id"], stage="research_run")
    summary, total, billable, error = await _run_with_attempt_capture(
        night_exec.run_research_job, engine, cwd, worker_task,
        getattr(config, "NIGHT_JOB_TIMEOUT", 1800), on_spawn=_hold, attempt=attempt)
    _record_run(cwd, engine, started, error, total, billable)
    if jid in _stop_requested:
        night_queue_store.save_result(jid, kind="research", text=summary,
                                      completeness="partial" if summary else "none")
        night_queue_store.update(jid, status="stopped", ended_at=time.time(), summary="Stopped by you.",
                                 failure_kind=None, blocker_reason=None, next_retry_at=None,
                                 next_action="Run again when you are ready.")
        night_queue_store.finish_attempt(attempt["id"], status="cancelled",
                                         stage="research_run", error="Stopped by you.", output=summary)
        return
    if error:
        reason = f"Research agent error: {error}"
        await _fail_job(job, attempt, reason, stage="research_run",
                        kind=_failure_kind(error, "research_run"), output=summary,
                        stderr=error)
        night_queue_store.update(jid, engine_used=engine, tokens_total=total,
                                 tokens_billable=billable)
        await _notify_status(jid, job, "failed", reason)
        return
    outcome = night_exec.parse_research_outcome(summary)
    if outcome.status == "waiting_input":
        question = outcome.question or outcome.report or "Research needs your input."
        night_queue_store.save_result(jid, kind="question", text=question)
        night_queue_store.update(jid, status="awaiting_input", engine_used=engine,
            tokens_total=total, tokens_billable=billable, ended_at=time.time(),
            summary=question, blocker_reason="Research is waiting for your input.",
            next_action="Answer the research question, then retry this task.",
            failure_kind=None, next_retry_at=None)
        night_queue_store.finish_attempt(attempt["id"], status="waiting_for_user",
                                         stage="research_review", output=summary)
        await _notify_input(jid, job, question)
        return
    if outcome.status == "blocked":
        reason = outcome.reason or "Research was blocked before producing a report."
        text = outcome.report or summary or reason
        night_queue_store.save_result(jid, kind="research", text=text, completeness="partial")
        night_queue_store.update(jid, status="blocked", engine_used=engine,
            tokens_total=total, tokens_billable=billable, ended_at=time.time(),
            summary=text, blocker_reason=reason,
            next_action="Resolve the blocker, then retry the task.",
            failure_kind="research_blocked", next_retry_at=None)
        night_queue_store.finish_attempt(attempt["id"], status="blocked",
                                         stage="research_review", error=reason, output=summary)
        await _notify_status(jid, job, "blocked", reason)
        return
    if outcome.status == "unverified":
        reason = outcome.reason or "Research findings were not sufficiently verified."
        text = outcome.report or summary or reason
        night_queue_store.save_result(jid, kind="research", text=text, completeness="partial")
        night_queue_store.update(jid, status="unverified", engine_used=engine,
            tokens_total=total, tokens_billable=billable, ended_at=time.time(),
            summary=text, blocker_reason=reason,
            next_action="Review sources or rerun with a narrower task.",
            failure_kind="research_unverified", next_retry_at=None)
        night_queue_store.finish_attempt(attempt["id"], status="unverified",
                                         stage="research_review", error=reason, output=summary)
        await _notify_status(jid, job, "unverified", reason)
        return
    report = outcome.report.strip()
    artifact = [{"kind": "research_report", "ref": f"queue:{jid}:result"}]
    night_queue_store.save_result(jid, kind="research", text=report,
                                  completeness="complete", artifacts=artifact)
    night_queue_store.update(
        jid, status="completed", engine_used=engine, tokens_total=total,
        tokens_billable=billable, ended_at=time.time(), summary=report,
        failure_kind=None, blocker_reason=None, next_retry_at=None,
        next_action="Research report is ready in task details.",
    )
    night_queue_store.finish_attempt(attempt["id"], status="succeeded",
                                     stage="complete", output=summary)
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
            "needs_you": "🙋", "blocked": "⛔", "unverified": "⚠️"}.get(status, "•")
    verb = {"deployed": "deployed — test on phone", "completed": "completed — report ready",
            "staged": "staged — ship when ready",
            "failed": "failed", "needs_you": "needs you", "blocked": "blocked",
            "unverified": "unverified — review sources"}.get(status, status)
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
    night_queue_store.start_attempt(job_id, engine)
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
    # No worker survives a server process restart. Reconcile persisted claims
    # immediately so the app never shows an orphan as Building until next night.
    recovered = night_queue_store.fail_orphaned_running(grace_seconds=0)
    for job in recovered:
        log.warning("startup recovered orphaned Night Shift job #%s", job["id"])
        try:
            await _notify_status(job["id"], job, "failed", job["summary"])
        except Exception:
            pass
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
