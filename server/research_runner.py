"""Immediate durable research jobs, independent of the Night Shift window."""

from __future__ import annotations

import asyncio
import uuid

from server import config, night_exec
from server.db import assistant_tasks_store, store as memory


_workers: dict[str, asyncio.Task] = {}
_FINAL = {"completed", "unverified", "failed", "cancelled"}
_PUBLISHABLE = _FINAL | {"waiting_for_user"}


def submit(*, session_id: str, request_id: str | None, prompt: str,
           project: str | None = None, engine: str | None = None,
           timeout_seconds: int | None = None,
           attachment_refs: list[str] | None = None) -> tuple[dict, bool]:
    """Idempotently persist and dispatch a read-only, text-only job.

    Attachment references remain in the durable package, but require a later
    explicit approval before their contents can be handed to an external CLI.
    """
    from server import workspace
    request_id = request_id or uuid.uuid4().hex
    selected_engine = (engine or config.RESEARCH_ENGINE).lower()
    if selected_engine not in {"claude", "codex", "gemini"}:
        raise ValueError("engine must be claude, codex, or gemini")
    timeout = max(60, min(timeout_seconds or config.RESEARCH_TIMEOUT, 3600))
    work, created = assistant_tasks_store.accept(
        request_id=f"{request_id}:research", session_id=session_id,
        command="research", prompt=prompt, project=project,
    )
    if created or assistant_tasks_store.get_research(work["id"]) is None:
        path = workspace.for_turn(project, session_id)
        assistant_tasks_store.configure_research(
            work["id"], request_id=request_id, engine=selected_engine,
            workspace=str(path), timeout_seconds=timeout,
            attachment_refs=attachment_refs,
        )
    turn_ids = memory.current_turn_ids()
    memory.set_research_reply_source(work["id"], turn_ids.get("user_message_id"))
    if attachment_refs:
        work = assistant_tasks_store.update(
            work["id"], status="waiting_for_user",
            blocker="Approval is required before research can send attachment contents",
            next_action="Approve attachment use or retry without attachments",
            event="attachment_approval_required",
            payload={"attachment_refs": attachment_refs},
        )
        _append_terminal_reply(work)
    elif work["status"] in {"accepted", "working", "recovering"}:
        start(work["id"])
        work = assistant_tasks_store.get(work["id"])
    return work, created


def start(task_id: str) -> bool:
    """Start once in this process; durability and idempotency live in SQLite."""
    current = _workers.get(task_id)
    if current is not None and not current.done():
        return False
    task = asyncio.create_task(_run_guarded(task_id))
    _workers[task_id] = task
    def _remove_finished(finished: asyncio.Task) -> None:
        if _workers.get(task_id) is finished:
            _workers.pop(task_id, None)
    task.add_done_callback(_remove_finished)
    return True


def recover() -> int:
    # A process may have committed the terminal task row immediately before it
    # died while writing chat history. The receipt makes this repair idempotent.
    for metadata in assistant_tasks_store.research_records():
        work = assistant_tasks_store.get(metadata["task_id"])
        if work and work.get("status") in _PUBLISHABLE:
            _append_terminal_reply(work)
    count = 0
    for metadata in assistant_tasks_store.recoverable_research():
        count += int(start(metadata["task_id"]))
    return count


async def cancel(task_id: str) -> dict | None:
    work = assistant_tasks_store.get(task_id)
    if not work:
        return None
    if work.get("status") in _FINAL:
        _append_terminal_reply(work)
        return work
    updated = assistant_tasks_store.update(
        task_id, status="cancelled", blocker="Stopped by the user",
        next_action="", event="cancelled",
        payload={"outcome": "cancelled"},
    )
    task = _workers.get(task_id)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _append_terminal_reply(updated)
    await _notify(updated, "cancelled", _terminal_text(updated))
    return assistant_tasks_store.get(task_id)


def _research_prompt(work: dict, metadata: dict) -> tuple[str, str | None]:
    refs = metadata.get("attachment_refs") or []
    if refs:
        return "", ("Approval is required before research can send attachment "
                    "contents to the research provider")
    return work["original_prompt"], None


def _terminal_text(work: dict) -> str:
    status = work.get("status")
    result = (work.get("result") or "").strip()
    blocker = (work.get("blocker") or "").strip()
    if status == "completed":
        body = result or "Research completed without a report."
    elif status == "unverified":
        body = "Research was not fully verified."
        if blocker:
            body += f" {blocker}"
        if result:
            body += f"\n\n{result}"
    elif status == "waiting_for_user":
        body = f"Research needs your input: {blocker or result or 'More information is required.'}"
    elif status == "cancelled":
        body = "Research was cancelled."
    else:
        body = f"Research failed: {blocker or 'The research worker stopped unexpectedly.'}"
        if result:
            body += f"\n\nPartial output:\n{result}"
    return body


def _append_terminal_reply(work: dict) -> bool:
    status = work.get("status")
    if status not in _PUBLISHABLE:
        return False
    receipt = f"research-result:{work['id']}"
    if status == "waiting_for_user":
        receipt += f":waiting:{work.get('revision', 1)}"
    # A formatting update must never replace an already delivered receipt.
    if memory.get_local_turn(work["session_id"], receipt):
        return False
    return memory.append_local_turn(
        work["session_id"], receipt,
        [{"role": "assistant", "content": _terminal_text(work)}],
        reply_to_message_id=memory.get_research_reply_source(work["id"]),
    )


async def _notify(work: dict, status: str, text: str) -> None:
    from server.notifier import notify_app
    await notify_app(
        "chat_reply" if status in {"completed", "unverified"} else "research_update",
        "Research ready" if status == "completed" else "Research needs attention",
        text, data={"session_id": work["session_id"], "task_id": work["id"]},
    )


async def _run_guarded(task_id: str) -> None:
    try:
        await _run(task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        current = assistant_tasks_store.get(task_id)
        if not current:
            return
        # Never turn a result already committed by _run into a failure merely
        # because a later notification transport raised.
        if current.get("status") not in _PUBLISHABLE:
            current = assistant_tasks_store.update(
                task_id, status="failed", result="",
                blocker=f"Research worker crashed: {type(exc).__name__}: {exc}",
                next_action="Retry the preserved research request", event="failed",
                payload={"outcome": "unexpected_error"},
                unless_cancelled=True,
            )
        _append_terminal_reply(current)
        try:
            await _notify(current, current["status"], _terminal_text(current))
        except Exception:
            pass


async def _run(task_id: str) -> None:
    work = assistant_tasks_store.get(task_id)
    metadata = assistant_tasks_store.get_research(task_id)
    if not work or not metadata or work.get("status") == "cancelled":
        return
    started = assistant_tasks_store.update(
        task_id, status="working", blocker="",
        next_action="Researching and checking sources", event="research_started",
        payload={"engine": metadata["engine"],
                 "timeout_seconds": metadata["timeout_seconds"]},
        unless_cancelled=True,
    )
    if started.get("status") == "cancelled":
        return
    prompt, attachment_error = _research_prompt(work, metadata)
    if attachment_error:
        updated = assistant_tasks_store.update(
            task_id, status="waiting_for_user", blocker=attachment_error,
            result=attachment_error,
            next_action="Reattach the missing file and retry", event="blocked",
            payload={"outcome": "missing_attachment"},
            unless_cancelled=True,
        )
        _append_terminal_reply(updated)
        await _notify(updated, "waiting_for_user", attachment_error)
        return
    try:
        raw, total, billable, error = await night_exec.run_research_job(
            metadata["engine"], metadata["workspace"], prompt,
            metadata["timeout_seconds"],
        )
    except asyncio.CancelledError:
        # cancel() records the durable terminal state; do not overwrite it.
        raise
    if error:
        timed_out = "ran past the" in error and "limit" in error
        message = (f"Research timed out after {metadata['timeout_seconds']} seconds."
                   if timed_out else f"Research agent failed: {error}")
        updated = assistant_tasks_store.update(
            task_id, status="failed", result=raw, blocker=message,
            next_action="Retry with a narrower question or another engine",
            event="timed_out" if timed_out else "failed",
            payload={"outcome": "timeout" if timed_out else "runner_error",
                     "engine": metadata["engine"], "tokens_total": total,
                     "tokens_billable": billable},
            unless_cancelled=True,
        )
        if updated.get("status") == "cancelled":
            return
        _append_terminal_reply(updated)
        await _notify(updated, "failed", message)
        return

    outcome = night_exec.parse_research_outcome(raw)
    mapping = {
        "report_complete": ("completed", outcome.report, "", ""),
        "waiting_input": ("waiting_for_user",
                          outcome.question or outcome.report,
                          outcome.question or "Research needs your input",
                          "Answer the research question"),
        "blocked": ("failed", outcome.report or outcome.reason,
                    outcome.reason or "Research was blocked",
                    "Resolve the blocker and retry"),
        "unverified": ("unverified", outcome.report,
                       outcome.reason or "The report could not be verified",
                       "Review the evidence or retry with a narrower question"),
    }
    status, result, blocker, next_action = mapping[outcome.status]
    if assistant_tasks_store.get(task_id).get("status") == "cancelled":
        return
    updated = assistant_tasks_store.update(
        task_id, status=status, result=result, blocker=blocker,
        next_action=next_action, event=status,
        payload={"outcome": outcome.status, "engine": metadata["engine"],
                 "tokens_total": total, "tokens_billable": billable},
        unless_cancelled=True,
    )
    if updated.get("status") == "cancelled":
        return
    _append_terminal_reply(updated)
    await _notify(updated, status, _terminal_text(updated))
