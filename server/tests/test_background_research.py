"""Durable immediate research runs outside the request/stream lifetime."""

import asyncio

from fastapi.testclient import TestClient

from server import config, main, research_runner
from server.db import assistant_tasks_store, store


async def _quiet_notify(*_args, **_kwargs):
    return None


def test_background_research_completes_once_and_appends_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)
    calls = 0

    async def fake_run(engine, cwd, prompt, timeout):
        nonlocal calls
        calls += 1
        assert engine == "claude"
        assert cwd == str(tmp_path)
        assert timeout == 1800
        return ('{"status":"report_complete","report":"Finding https://example.com",'
                '"question":"","reason":""}', 12, 10, None)

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", fake_run)

    async def scenario():
        first, created = research_runner.submit(
            session_id="app:phone", request_id="request-1",
            prompt="Compare private LLM providers",
            engine="claude", timeout_seconds=1800,
        )
        duplicate, created_again = research_runner.submit(
            session_id="app:phone", request_id="request-1",
            prompt="Compare private LLM providers",
            engine="claude", timeout_seconds=1800,
        )
        assert created is True and created_again is False
        assert duplicate["id"] == first["id"]
        await research_runner._workers[first["id"]]
        return first["id"]

    task_id = asyncio.run(scenario())
    assert calls == 1
    work = assistant_tasks_store.get(task_id, include_events=True)
    assert work["status"] == "completed"
    assert work["result"] == "Finding https://example.com"
    assert work["request_id"].endswith("request-1:research")
    history = store.get_recent("app:phone", n=10)
    assert len(history) == 1  # the shell owns the original user/deferred turns
    assert history[0]["role"] == "assistant"
    assert history[0]["local_request_id"] == f"research-result:{task_id}"
    assert history[0]["content"] == "Finding https://example.com"


def test_timeout_has_explicit_durable_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)

    async def fake_timeout(*_args, **_kwargs):
        return "", 0, 0, "ran past the 1800s limit and was stopped"

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", fake_timeout)

    async def scenario():
        work, _ = research_runner.submit(
            session_id="app:x", request_id="timeout", prompt="Research this",
            project="general", timeout_seconds=1800,
        )
        await research_runner._workers[work["id"]]
        return assistant_tasks_store.get(work["id"], include_events=True)

    work = asyncio.run(scenario())
    assert work["status"] == "failed"
    assert work["blocker"] == "Research timed out after 1800 seconds."
    assert work["events"][-1]["kind"] == "timed_out"
    assert work["events"][-1]["payload"]["outcome"] == "timeout"
    reply = store.get_recent("app:x", n=5)[-1]
    assert reply["local_request_id"] == f"research-result:{work['id']}"
    assert "Research failed: Research timed out after 1800 seconds." in reply["content"]


def test_cancel_is_independent_of_chat_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", hanging)

    async def scenario():
        work, _ = research_runner.submit(
            session_id="app:x", request_id="cancel", prompt="Long research",
            project="general",
        )
        await started.wait()
        updated = await research_runner.cancel(work["id"])
        await asyncio.sleep(0)
        return updated

    updated = asyncio.run(scenario())
    assert cancelled.is_set()
    assert updated["status"] == "cancelled"
    assert assistant_tasks_store.get(updated["id"])["status"] == "cancelled"
    assert "Research was cancelled" in store.get_recent("app:x", n=5)[-1]["content"]


def test_restart_recovery_relaunches_preserved_work(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)

    async def fake_run(*_args, **_kwargs):
        return ('{"status":"report_complete","report":"Recovered https://example.com",'
                '"question":"","reason":""}', 1, 1, None)

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", fake_run)
    work, _ = assistant_tasks_store.accept(
        request_id="recover:research", session_id="app:x", command="research",
        prompt="Preserved prompt", project="general",
    )
    assistant_tasks_store.configure_research(
        work["id"], request_id="recover", engine="claude",
        workspace=str(tmp_path), timeout_seconds=1800,
    )
    assistant_tasks_store.update(work["id"], status="recovering")

    async def scenario():
        assert research_runner.recover() == 1
        await research_runner._workers[work["id"]]

    asyncio.run(scenario())
    assert assistant_tasks_store.get(work["id"])["status"] == "completed"


def test_attachments_are_preserved_but_not_handed_to_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)
    called = False

    async def must_not_run(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", must_not_run)

    async def scenario():
        work, _ = research_runner.submit(
            session_id="app:x", request_id="with-file", prompt="Research it",
            project="general", attachment_refs=["/private/upload.txt"],
        )
        await asyncio.sleep(0)
        return work

    work = asyncio.run(scenario())
    assert called is False
    assert work["status"] == "waiting_for_user"
    assert assistant_tasks_store.get_research(work["id"])["attachment_refs"] == [
        "/private/upload.txt"]
    reply = store.get_recent("app:x", n=5)[-1]
    assert "needs your input" in reply["content"]
    assert reply["local_request_id"] == (
        f"research-result:{work['id']}:waiting:{work['revision']}")
    prompt, blocker = research_runner._research_prompt(
        work, assistant_tasks_store.get_research(work["id"]))
    assert prompt == ""
    assert "/private/upload.txt" not in blocker

    cancelled = asyncio.run(research_runner.cancel(work["id"]))
    assert cancelled["status"] == "cancelled"
    replies = store.get_recent("app:x", n=5)
    assert len(replies) == 2
    assert replies[-1]["local_request_id"] == f"research-result:{work['id']}"
    assert "cancelled" in replies[-1]["content"].lower()


def test_unexpected_runner_exception_is_failed_and_replied(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)

    async def explode(*_args, **_kwargs):
        raise RuntimeError("provider process vanished")

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", explode)

    async def scenario():
        work, _ = research_runner.submit(
            session_id="app:crash", request_id="crash", prompt="Research safely")
        await research_runner._workers[work["id"]]
        return assistant_tasks_store.get(work["id"])

    work = asyncio.run(scenario())
    assert work["status"] == "failed"
    assert "RuntimeError: provider process vanished" in work["blocker"]
    assert "Research failed:" in store.get_recent("app:crash", n=5)[-1]["content"]


def test_cancelled_status_wins_when_runner_returns_during_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(research_runner, "_notify", _quiet_notify)
    started = asyncio.Event()

    async def stubborn(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return ('{"status":"report_complete","report":"Late https://example.com",'
                    '"question":"","reason":""}', 1, 1, None)

    monkeypatch.setattr(research_runner.night_exec, "run_research_job", stubborn)

    async def scenario():
        work, _ = research_runner.submit(
            session_id="app:race", request_id="race", prompt="Long report")
        await started.wait()
        return await research_runner.cancel(work["id"])

    work = asyncio.run(scenario())
    assert work["status"] == "cancelled"
    reply = store.get_recent("app:race", n=5)[-1]["content"]
    assert "cancelled" in reply.lower()
    assert "Late" not in reply


def test_recovery_repairs_completed_job_missing_chat_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    work, _ = assistant_tasks_store.accept(
        request_id="gap:research", session_id="app:gap", command="research",
        prompt="Original question", project="general",
    )
    assistant_tasks_store.configure_research(
        work["id"], request_id="gap", engine="gemini", workspace=str(tmp_path),
        timeout_seconds=1800,
    )
    assistant_tasks_store.update(
        work["id"], status="completed", result="Report https://example.com")
    assert store.get_recent("app:gap", n=5) == []

    assert research_runner.recover() == 0
    assert research_runner.recover() == 0

    replies = store.get_recent("app:gap", n=5)
    assert len(replies) == 1
    assert replies[0]["local_request_id"] == f"research-result:{work['id']}"


def test_direct_research_command_returns_immediately_and_retry_deduplicates(monkeypatch):
    work = {"id": "research-123", "status": "working"}

    def fake_submit(**kwargs):
        assert kwargs["request_id"] == "stable-request"
        assert kwargs["session_id"] == "app:direct"
        return work, True

    monkeypatch.setattr(research_runner, "submit", fake_submit)
    main.orchestrator.init()
    http = TestClient(main.app)
    headers = {"X-API-Token": config.API_TOKEN}
    body = {"command": "research", "prompt": "Compare current providers",
            "session_id": "app:direct", "request_id": "stable-request"}

    first = http.post("/run", headers=headers, json=body)
    second = http.post("/run", headers=headers, json=body)

    assert first.status_code == 200 and second.status_code == 200
    assert "continuing in the background" in first.json()["result"]
    turns = store.get_recent("app:direct", n=10)
    assert len(turns) == 2
    assert {turn["local_request_id"] for turn in turns} == {
        "command-result:stable-request"}


def test_research_reply_body_uses_structured_quote_instead_of_repeating_question():
    text = research_runner._terminal_text({"original_prompt": "My very long question", "status": "completed", "result": "The actual report"})
    assert text == "The actual report"
    assert "Research reply to:" not in text


def test_existing_research_receipt_is_not_rewritten_by_quote_format_change():
    work = {"id": "old-task", "session_id": "app:phone", "status": "completed", "result": "Report", "original_prompt": "Question"}
    store.append_local_turn("app:phone", "research-result:old-task", [{"role": "assistant", "content": "Research reply to: Question\n\nReport"}])
    assert research_runner._append_terminal_reply(work) is False
    assert store.count("app:phone") == 1
