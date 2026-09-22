"""Outcome continuity and truthful repository execution regressions."""

import asyncio
import json
import subprocess

from fastapi.testclient import TestClient

from server import config, main, workspace
from server.db import assistant_tasks_store
from server.skills.base import SkillResult
from server.skills.projects import _clone_view


def _make_remote(path):
    source = path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=source, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=source, check=True,
                   capture_output=True)
    remote = path / "sample.git"
    subprocess.run(["git", "clone", "--bare", str(source), str(remote)], check=True,
                   capture_output=True)
    return remote


def test_clone_is_executed_verified_and_can_activate(tmp_path, monkeypatch):
    remote = _make_remote(tmp_path)
    projects = tmp_path / "Projects"
    general = projects / "general"
    general.mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_PARENT_DIR", str(projects))
    monkeypatch.setattr(config, "WORKSPACE_DIR", general)

    url = remote.as_uri()

    async def run_clone():
        with workspace.bound(general):
            result = await _clone_view(f"clone {url} and switch")
            return result, workspace.name()

    result, active = asyncio.run(run_clone())
    assert active == "sample", result.message

    assert isinstance(result, SkillResult)
    assert result.status == "succeeded"
    assert result.data["active"] is True
    assert (projects / "sample" / ".git").is_dir()
    origin = subprocess.run(
        ["git", "-C", str(projects / "sample"), "remote", "get-url", "origin"],
        check=True, text=True, capture_output=True).stdout.strip()
    assert origin.endswith("/sample.git")
    assert "[[switch:sample]]" in result.message
def test_clone_failure_never_claims_success(tmp_path, monkeypatch):
    projects = tmp_path / "Projects"
    general = projects / "general"
    general.mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_PARENT_DIR", str(projects))
    monkeypatch.setattr(config, "WORKSPACE_DIR", general)
    with workspace.bound(general):
        result = asyncio.run(_clone_view(
            "clone https://127.0.0.1:1/does-not-exist.git"))
    assert result.status == "failed"
    assert "Clone failed" in result.message
    assert "Cloned and verified" not in result.message


def test_request_id_deduplicates_and_correction_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant_tasks_store, "DB_PATH", tmp_path / "work.db")
    first, created = assistant_tasks_store.accept(
        request_id="request-1", session_id="app:test", command="shell",
        prompt="clone this repository", project="general")
    again, duplicate_created = assistant_tasks_store.accept(
        request_id="request-1", session_id="app:test", command="shell",
        prompt="clone this repository", project="general")
    corrected, correction_created = assistant_tasks_store.accept(
        request_id="request-2", session_id="app:test", command="shell",
        prompt="and open it", project="general", continue_task_id=first["id"])

    assert created is True and duplicate_created is False
    assert again["id"] == first["id"]
    assert correction_created is True
    assert corrected["id"] == first["id"]
    assert corrected["original_prompt"] == "clone this repository"
    assert corrected["latest_prompt"] == "and open it"
    assert corrected["revision"] == 2
    assert [e["kind"] for e in assistant_tasks_store.get(
        first["id"], include_events=True)["events"]] == ["accepted", "accepted"]
    package = assistant_tasks_store.context_for(first["id"])
    assert "ORIGINAL REQUEST: clone this repository" in package
    assert "and open it" in package


def test_stream_exposes_durable_work_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(assistant_tasks_store, "DB_PATH", tmp_path / "work.db")

    calls = {"count": 0}

    async def fake_route(command, prompt, **kwargs):
        calls["count"] += 1
        on_event = kwargs.get("on_event")
        if on_event:
            await on_event({"type": "run", "run_id": "run-1", "project": "general"})
            await on_event({"type": "step", "n": 1, "tool": "projects",
                            "args": "clone x", "label": "Cloning repository"})
            await on_event({"type": "step_result", "n": 1, "tool": "projects",
                            "ok": True, "status": "succeeded", "summary": "verified"})
            await on_event({"type": "completion", "status": "completed",
                            "reason": "done"})
        return "Cloned and verified"

    monkeypatch.setattr(main.orchestrator, "route", fake_route)
    client = TestClient(main.app)
    body = {"command": "shell", "prompt": "clone it", "session_id": "app:test",
            "project": "general", "request_id": "stable-1"}
    headers = {"X-API-Token": config.API_TOKEN}

    first = client.post("/run/stream", json=body, headers=headers)
    frames = [json.loads(line) for line in first.text.splitlines()]
    work_frame = next(frame for frame in frames if frame["type"] == "work")
    final = next(frame for frame in frames if frame["type"] == "final")
    assert work_frame["work"]["status"] == "accepted"
    assert final["work"]["status"] == "completed"

    second = client.post("/run/stream", json=body, headers=headers)
    second_frames = [json.loads(line) for line in second.text.splitlines()]
    assert second_frames[0]["deduplicated"] is True
    assert calls["count"] == 1
