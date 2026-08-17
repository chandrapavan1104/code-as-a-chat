"""
Regression tests for the project-switching incident.

The turn that motivated all of this (conversations.db #803, "switch to the deaf
terminal project and check the status") spent its entire 7-step budget like so:

  1. projects(switch deaf-communication-terminal) → SWITCHED
  2. projects(switch general)                     → SWITCHED BACK
  3. projects({"switch":"deaf-communication..."}) → No project matches
  4. projects({"switch":"deaf-communication..."}) → No project matches
  5. claude({"command":"..."})
  6. projects(switch deaf-communication-terminal) → SWITCHED
  7. claude(Read the README...)
  (stopped at the step limit of 7)

…and answered none of what was asked. These tests replay that decision sequence
and assert the four independent defects behind it stay fixed.
"""

import asyncio

import pytest

import server.skills.projects  # noqa: F401 — importing registers the skill
from server import config, workspace
from server.skills import registry, shell
from server.skills.base import Skill
from server.skills.projects import _switch_view


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    """A stand-in ~/Projects with the two directories from the incident."""
    parent = tmp_path / "Projects"
    for name in ("general", "deaf-communication-terminal", "Code-as-a-chat"):
        (parent / name).mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_PARENT_DIR", str(parent))
    monkeypatch.setattr(config, "WORKSPACE_DIR", parent / "general")
    monkeypatch.setattr(config, "CONTEXT_AUTO_INIT", False, raising=False)
    return parent


# ── workspace resolution ──────────────────────────────────────────────────────

def test_resolve_matches_name_substring_and_slug(projects_dir):
    assert workspace.resolve("deaf-communication-terminal").name == "deaf-communication-terminal"
    assert workspace.resolve("deaf").name == "deaf-communication-terminal"
    # The app lowercases + slugifies a directory into the session id; that slug
    # has to find its way back to the real, differently-cased directory.
    assert workspace.resolve("code-as-a-chat").name == "Code-as-a-chat"
    assert workspace.resolve("no-such-project") is None


def test_for_session_recovers_the_thread_project(projects_dir):
    sid = "app:tseu3f9r::deaf-communication-terminal"
    assert workspace.for_session(sid).name == "deaf-communication-terminal"
    assert workspace.for_session("app:tseu3f9r") is None


def test_for_turn_prefers_explicit_then_session_then_default(projects_dir):
    sid = "app:x::deaf-communication-terminal"
    assert workspace.for_turn("Code-as-a-chat", sid).name == "Code-as-a-chat"
    assert workspace.for_turn(None, sid).name == "deaf-communication-terminal"
    assert workspace.for_turn(None, "app:x").name == "general"
    # An unresolvable explicit project must not silently win.
    assert workspace.for_turn("nonsense", sid).name == "deaf-communication-terminal"


def test_a_turn_may_switch_once_and_never_back(projects_dir):
    with workspace.bound(projects_dir / "general"):
        assert workspace.name() == "general"

        changed, reason = workspace.rebind(projects_dir / "deaf-communication-terminal")
        assert (changed, reason) == (True, "switched")
        assert workspace.name() == "deaf-communication-terminal"

        # Step 2 of the incident: switching back.
        changed, reason = workspace.rebind(projects_dir / "general")
        assert changed is False
        assert reason == "locked:deaf-communication-terminal"
        assert workspace.name() == "deaf-communication-terminal"

    # The binding is per turn — it must not leak into the next one.
    assert workspace.active().name == "general"


def test_binding_does_not_move_the_global_default(projects_dir):
    with workspace.bound(projects_dir / "deaf-communication-terminal"):
        assert workspace.name() == "deaf-communication-terminal"
        assert config.WORKSPACE_DIR.name == "general"


# ── the agent loop ────────────────────────────────────────────────────────────

class _Probe(Skill):
    """Stands in for claude/codex — records what it was actually handed."""
    name = "probe"
    description = "test probe"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def run(self, prompt="", **kwargs):
        self.calls.append((str(workspace.name()), prompt))
        return f"probe ran in {workspace.name()}"


def _replay(monkeypatch, decisions):
    """Feed the shell agent a fixed decision sequence, as the router would."""
    seq = iter(decisions)

    async def fake_haiku(*args, **kwargs):
        try:
            return next(seq)
        except StopIteration:
            return '{"action":"done","reply":"finished"}'

    monkeypatch.setattr(shell, "_haiku", fake_haiku)


def test_incident_replay_no_thrash_and_work_gets_done(projects_dir, monkeypatch):
    """The exact #803 decision sequence must now reach the real work."""
    probe = _Probe()
    monkeypatch.setitem(registry, "probe", probe)

    _replay(monkeypatch, [
        '{"action":"call","tool":"projects","args":"switch deaf-communication-terminal"}',
        '{"action":"call","tool":"projects","args":"switch general"}',
        '{"action":"call","tool":"projects","args":{"switch":"deaf-communication-terminal"}}',
        '{"action":"call","tool":"projects","args":{"switch":"deaf-communication-terminal"}}',
        '{"action":"call","tool":"probe","args":"check the status"}',
        '{"action":"done","reply":"Status checked."}',
    ])

    # Shaped exactly like /run: bind, await the turn, then read the project the
    # turn ENDED in — all inside one coroutine, so the ContextVar is the same one
    # the endpoint sees.
    async def turn():
        with workspace.bound(projects_dir / "general"):
            reply = await shell.ShellSkill().run("switch to deaf terminal and check status")
            return reply, workspace.name()

    result, ended_in = asyncio.run(turn())

    # It got where it was asked to go, and stayed.
    assert ended_in == "deaf-communication-terminal"
    # The real work ran, in the right project — the whole point of the turn.
    assert probe.calls == [("deaf-communication-terminal", "check the status")]
    assert result == "Status checked."


def test_wasted_steps_do_not_exhaust_the_budget(projects_dir, monkeypatch):
    """Defects 1-4 together: a router that thrashes for longer than the budget
    must still reach the real work, because none of the thrash is charged.

    STEP_BUDGET+2 useless project calls, then the actual task.
    """
    probe = _Probe()
    monkeypatch.setitem(registry, "probe", probe)

    thrash = (
        ['{"action":"call","tool":"projects","args":"switch deaf-communication-terminal"}']
        + ['{"action":"call","tool":"projects","args":"switch general"}'] * 4
        + ['{"action":"call","tool":"projects","args":{"switch":"deaf-communication-terminal"}}'] * 4
        + ['{"action":"call","tool":"nonexistent-tool","args":"whatever"}'] * 3
    )
    _replay(monkeypatch, thrash + [
        '{"action":"call","tool":"probe","args":"check the status"}',
        '{"action":"done","reply":"Status checked."}',
    ])

    with workspace.bound(projects_dir / "general"):
        result = asyncio.run(shell.ShellSkill().run("switch to deaf terminal and check status"))

    assert probe.calls == [("deaf-communication-terminal", "check the status")]
    assert result == "Status checked."


def test_duplicate_call_is_replayed_not_repeated(projects_dir, monkeypatch):
    """Defect 4: an identical failing call used to be re-issued forever."""
    calls = {"n": 0}

    class Counter(Skill):
        name = "probe"
        description = "test probe"

        async def run(self, prompt="", **kwargs):
            calls["n"] += 1
            return "ERROR: nope"

    monkeypatch.setitem(registry, "probe", Counter())
    _replay(monkeypatch, [
        '{"action":"call","tool":"probe","args":"same"}',
        '{"action":"call","tool":"probe","args":"same"}',
        '{"action":"call","tool":"probe","args":"same"}',
        '{"action":"done","reply":"gave up"}',
    ])

    with workspace.bound(projects_dir / "general"):
        asyncio.run(shell.ShellSkill().run("do the thing"))

    assert calls["n"] == 1, "the identical call must run once, then be replayed"


def test_noop_switch_is_not_charged(projects_dir):
    """Switching to where you already are satisfies the precondition; it is not
    a step the user should pay for."""
    with workspace.bound(projects_dir / "deaf-communication-terminal"):
        out = _switch_view("deaf-communication-terminal")
        assert shell._is_noop_step("projects", out) is True

    with workspace.bound(projects_dir / "general"):
        out = _switch_view("deaf-communication-terminal")
        assert shell._is_noop_step("projects", out) is False
        assert "[[switch:deaf-communication-terminal]]" in out


def test_failed_switch_suggests_instead_of_shrugging(projects_dir):
    with workspace.bound(projects_dir / "general"):
        out = _switch_view("deff-terminal")
    assert "No project matches" in out
    assert "Did you mean" in out


def test_run_endpoints_bind_the_turn_and_report_where_it_ended(projects_dir, monkeypatch):
    """End to end over HTTP: the thread's project (recovered from the session id)
    binds the turn, an in-turn switch is visible to later tool calls, and the
    response tells the app which project the turn finished in — so its header and
    thread key follow. Also guards the ContextVar propagating across the
    endpoint's task boundary, which /run/stream crosses via create_task."""
    import json as _json
    from fastapi.testclient import TestClient
    from server import main

    seen: list[str] = []

    async def fake_route(command, prompt, **kwargs):
        seen.append(workspace.name())
        workspace.rebind(projects_dir / "Code-as-a-chat")
        return "done"

    monkeypatch.setattr(main.orchestrator, "route", fake_route)
    client = TestClient(main.app)
    headers = {"X-API-Token": config.API_TOKEN}
    body = {"command": "shell", "prompt": "hi",
            "session_id": "app:abc::deaf-communication-terminal"}

    r = client.post("/run", headers=headers, json=body)
    assert r.status_code == 200
    assert seen[-1] == "deaf-communication-terminal", "session id must bind the turn"
    assert r.json()["workspace"] == "Code-as-a-chat", "must report where it ended"

    r = client.post("/run/stream", headers=headers, json=body)
    final = [_json.loads(ln) for ln in r.text.splitlines()][-1]
    assert final["type"] == "final"
    assert final["workspace"] == "Code-as-a-chat"

    # An explicit project beats the session id.
    r = client.post("/run", headers=headers, json={**body, "project": "general"})
    assert seen[-1] == "general"

    # None of it moved the global default.
    assert config.WORKSPACE_DIR.name == "general"


def test_charged_steps_ignores_free_ones():
    assert shell._charged_steps([]) == 0
    assert shell._charged_steps([
        {"charged": True}, {"charged": False}, {},   # {} defaults to charged
    ]) == 2
