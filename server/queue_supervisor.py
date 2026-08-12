"""Always-on queue watchdog: explain, recover, retry, and escalate work."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time

from server.db import capability_store, deployment_store, night_queue_store

log = logging.getLogger("queue_supervisor")
_state = {"last_check": None, "last_actions": [], "error": None,
          "awareness_error": None}
_TRANSIENT = (
    "worker disappeared", "worker exited", "timed out", "ran past", "timeout",
    "failed to launch", "cli is not installed", "rate limit", "quota", "auth",
    "connection", "http", "run error", "night job crashed",
)
_HUMAN = ("owner's answer", "need a decision", "need your input", "ambiguous")


def _configured_engines() -> list[str]:
    from server import prefs
    raw = prefs.night_settings().get("engines") or "claude,codex,gemini"
    engines = [value.strip() for value in str(raw).split(",") if value.strip()]
    installed = [engine for engine in engines if shutil.which(
        "gemini" if engine == "gemini" else engine)]
    return installed or engines or ["claude"]


def _next_engine(job: dict) -> str:
    engines = _configured_engines()
    attempted = list(job.get("attempted_engines") or [])
    for engine in engines:
        if engine not in attempted:
            return engine
    # All have been tried: prefer the least-used engine deterministically.
    return min(engines, key=attempted.count)


def _failure_kind(text: str) -> str:
    lowered = (text or "").lower()
    if "worker disappeared" in lowered or "worker exited" in lowered:
        return "worker_lost"
    if "timed out" in lowered or "ran past" in lowered or "timeout" in lowered:
        return "timeout"
    if "merge conflict" in lowered:
        return "merge_conflict"
    if "rolled back" in lowered or "health verification" in lowered:
        return "deployment_health"
    if "path" in lowered and ("gone" in lowered or "not found" in lowered):
        return "project_missing"
    if "auth" in lowered:
        return "authentication"
    return "worker_error"


def _retryable(job: dict) -> bool:
    text = (job.get("summary") or "").lower()
    if any(marker in text for marker in _HUMAN):
        return False
    if "project path is gone" in text or "not a git repo" in text:
        return False
    return any(marker in text for marker in _TRANSIENT) or job.get("tag") == "auto"


def _next_window_text() -> str:
    from server import prefs
    start = prefs.night_settings().get("start", "23:00")
    return f"Night Shift will pick this up in its next window at {start}."


def job_explanation(job: dict) -> dict:
    """Plain-language operational state for the app; never mutates the row."""
    status = job.get("status")
    blocked = night_queue_store.blocked_by(job)
    attempts = job.get("attempt_count") or 0
    maximum = job.get("max_attempts") or 3
    blocker = job.get("blocker_reason")
    action = job.get("next_action")
    if blocked:
        blocker = f"Waiting for prerequisite task(s) #{', #'.join(map(str, blocked))}."
        action = "The supervisor is monitoring those prerequisites and will continue automatically."
    elif status == "queued" and not action:
        action = _next_window_text()
    elif status == "held":
        blocker = "This task is marked Mine, so automation is intentionally paused."
        action = "Change it to Auto when you want the supervisor to own it."
    elif status == "running":
        action = "A worker is implementing and testing it now."
    elif status == "deploying":
        action = "The coordinator is merging, restarting, and health-checking it."
    elif status == "staged" and job.get("tag") == "mine":
        blocker = "Implementation is ready, but this Mine task requires manual Ship."
        action = "Tap Ship, or change it to Auto for supervised deployment."
    elif status in ("shipped", "completed"):
        action = "No action needed; this task is complete."
    return {
        "blocker": blocker,
        "next_action": action,
        "attempts": attempts,
        "max_attempts": maximum,
        "failure_kind": job.get("failure_kind"),
        "next_retry_at": job.get("next_retry_at"),
        "last_supervised_at": job.get("last_supervised_at"),
    }


async def _notify_exhausted(job: dict, reason: str) -> None:
    try:
        from server.notifier import notify_app
        await notify_app(
            "queue_supervisor", f"Task #{job['id']} needs a decision",
            reason, ref_kind="queue_job", ref_id=job["id"])
    except Exception:
        pass


async def supervise_once(now: float | None = None) -> list[str]:
    """One idempotent audit. Recoverable work advances; only hard stops escalate."""
    now = now or time.time()
    actions: list[str] = []
    from server.capability_registry import REGISTRY_VERSION, assess, refresh
    try:
        refresh()
        _state["awareness_error"] = None
    except Exception as exc:
        _state["awareness_error"] = str(exc)
        log.warning("capability refresh failed; queue supervision continues: %s", exc)
    from server import night_shift
    recovered = night_queue_store.fail_orphaned_running(
        active_job_ids=set(night_shift._running), grace_seconds=60)
    for job in recovered:
        actions.append(f"#{job['id']} recovered orphaned worker")
    jobs = night_queue_store.list_jobs(limit=300)
    for job in jobs:
        if job["status"] == "closed":
            continue
        jid = job["id"]
        spec = job.get("spec_json") or {}
        changed_at = spec.get("refined_at") or job.get("created_at") or 0
        prior_awareness = job.get("awareness_json") or {}
        needs_recheck = (prior_awareness.get("registry_version") != REGISTRY_VERSION
                         or (job.get("awareness_checked_at") or 0) < changed_at
                         or (prior_awareness.get("classification") == "conflict"
                             and not night_queue_store.blocked_by(job)))
        if (job["status"] not in ("running", "deploying", "staged", "deployed",
                                  "shipped", "completed")
                and needs_recheck):
            try:
                result = assess(jid)
                actions.append(f"#{jid} classified {result['classification']}")
                job = night_queue_store.get(jid)
                if not job or job["status"] == "closed":
                    continue
            except Exception as exc:
                log.warning("awareness check failed for #%s: %s", jid, exc)
        blocked = night_queue_store.blocked_by(job)
        if blocked:
            night_queue_store.update(
                jid, blocker_reason=f"Blocked by prerequisite(s): {blocked}",
                next_action="Supervisor is monitoring and recovering the prerequisite tasks.",
                last_supervised_at=now)
            continue
        if job.get("blocker_reason") and job["status"] in ("queued", "running"):
            night_queue_store.update(jid, blocker_reason=None)

        status = job["status"]
        attempts = job.get("attempt_count") or 0
        maximum = job.get("max_attempts") or 3
        if status == "failed" and job.get("tag") == "auto":
            kind = _failure_kind(job.get("summary") or "")
            if _retryable(job) and attempts < maximum:
                retry_at = job.get("next_retry_at")
                if retry_at is None:
                    retry_at = now + 60
                    night_queue_store.update(
                        jid, failure_kind=kind, blocker_reason=None,
                        next_retry_at=retry_at, last_supervised_at=now,
                        next_action=(f"Supervisor will retry with {_next_engine(job).title()} "
                                     "in about one minute."))
                    actions.append(f"#{jid} scheduled retry")
                elif retry_at <= now:
                    engine = _next_engine(job)
                    night_queue_store.update(
                        jid, status="queued", engine=engine, engine_used=None,
                        ended_at=None, next_retry_at=None, last_supervised_at=now,
                        blocker_reason=None,
                        next_action=f"Recovered automatically; queued for {engine.title()}.")
                    actions.append(f"#{jid} requeued on {engine}")
                continue
            reason = (f"Automatic recovery stopped after {attempts}/{maximum} attempts. "
                      f"Last failure: {(job.get('summary') or 'unknown')[:300]}")
            night_queue_store.update(
                jid, status="needs_you", failure_kind=kind, blocker_reason=reason,
                next_action="Review the work order or provide one decision; completed work is preserved.",
                last_supervised_at=now)
            actions.append(f"#{jid} escalated after retry limit")
            await _notify_exhausted(job, reason)
            continue

        if status in ("staged", "deployed") and job.get("tag") == "auto":
            latest = deployment_store.latest(source="queue", ref_id=jid)
            deployment_is_current = bool(
                latest and (latest.get("updated_at") or 0) >= (job.get("started_at") or 0))
            if (deployment_is_current
                    and latest.get("state") in ("failed", "rolled_back")):
                if attempts < maximum:
                    engine = _next_engine(job)
                    night_queue_store.update(
                        jid, status="queued", branch=None, engine=engine,
                        engine_used=None, failure_kind="deployment_recovery",
                        blocker_reason=None, next_retry_at=None,
                        last_supervised_at=now,
                        next_action=("Previous deployment was safely rolled back. "
                                     f"Rebuilding against the live base with {engine.title()}."))
                    actions.append(f"#{jid} rebuilding after deployment failure")
                else:
                    reason = (f"Deployment could not be verified after {attempts} build attempts. "
                              f"Last result: {latest.get('detail') or 'unknown'}")
                    night_queue_store.update(
                        jid, status="needs_you", failure_kind="deployment_health",
                        blocker_reason=reason, next_action="Review the deployment report.",
                        last_supervised_at=now)
                    await _notify_exhausted(job, reason)
                continue
            from server.skills.queue import _ship
            result = _ship(jid)
            night_queue_store.update(jid, last_supervised_at=now,
                                     next_action=result[:500])
            actions.append(f"#{jid} deployment advanced")
            continue

        if status == "queued":
            night_queue_store.update(
                jid, last_supervised_at=now,
                next_action=job.get("next_action") or _next_window_text())
        elif status in ("running", "deploying"):
            night_queue_store.update(jid, last_supervised_at=now)

    _state.update(last_check=now, last_actions=actions[-20:], error=None)
    return actions


def health_snapshot() -> dict:
    jobs = night_queue_store.list_jobs(limit=300)
    active = [j for j in jobs if j["status"] != "closed"]
    blocked = [j for j in active if night_queue_store.blocked_by(j)]
    recovering = [j for j in active if
                  j["status"] == "failed" and j.get("tag") == "auto"
                  and (j.get("attempt_count") or 0) < (j.get("max_attempts") or 3)]
    attention = [j for j in active if j["status"] == "needs_you" or
                 (j["status"] == "failed" and j not in recovering)]
    held = [j for j in active if j.get("tag") == "mine" and
            j["status"] in ("held", "staged", "deployed")]
    working = [j for j in active if
               (j["status"] in ("queued", "running", "deploying") or
                j in recovering or
                (j["status"] in ("staged", "deployed") and j.get("tag") == "auto"))]
    if attention:
        state, headline = "attention", f"{len(attention)} task(s) need a decision after automatic recovery."
    elif working:
        state, headline = "working", f"Supervisor is monitoring {len(working)} active task(s)."
    elif held:
        state, headline = "healthy", f"{len(held)} task(s) are intentionally held by you."
    else:
        state, headline = "healthy", "Queue is clear."
    try:
        capabilities_known = capability_store.count()
    except Exception:
        capabilities_known = 0
    return {
        "state": state, "headline": headline, "active": len(active),
        "working": len(working), "blocked": len(blocked),
        "held": len(held), "recovering": len(recovering),
        "needs_attention": len(attention), "last_check": _state["last_check"],
        "capabilities_known": capabilities_known,
        "awareness_error": _state["awareness_error"],
        "last_actions": _state["last_actions"], "error": _state["error"],
    }


async def supervisor_loop() -> None:
    log.info("queue supervisor started")
    while True:
        try:
            await supervise_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _state.update(last_check=time.time(), error=str(exc))
            log.exception("queue supervision failed")
        await asyncio.sleep(60)
