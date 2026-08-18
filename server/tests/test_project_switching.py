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
import os

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


def test_a_turn_leaves_a_readable_trace(projects_dir, monkeypatch):
    """The whole point of the trace: after the fact, you can see which tools ran,
    in which project, which ones were wasted, and why the turn stopped."""
    from server.db import agent_runs_store

    monkeypatch.setitem(registry, "probe", _Probe())
    _replay(monkeypatch, [
        '{"action":"call","tool":"projects","args":"switch deaf-communication-terminal"}',
        '{"action":"call","tool":"projects","args":"switch general"}',
        '{"action":"call","tool":"probe","args":"check the status"}',
        '{"action":"done","reply":"Status checked."}',
    ])

    async def turn():
        with workspace.bound(projects_dir / "general"):
            return await shell.ShellSkill().run("check status", session_id="s1")

    asyncio.run(turn())

    runs = agent_runs_store.list_runs(session_id="s1")
    assert len(runs) == 1
    trace = agent_runs_store.get(runs[0]["id"])

    assert trace["stop_reason"] == "done"
    assert trace["reply"] == "Status checked."
    tools = [s["tool"] for s in trace["steps"]]
    assert tools == ["projects", "projects", "probe"]

    # The refused switch-back is recorded but not charged; the real work is.
    by_tool = {(s["tool"], s["idx"]): s for s in trace["steps"]}
    assert by_tool[("projects", 1)]["charged"] == 1      # the switch that worked
    assert by_tool[("projects", 2)]["charged"] == 0      # the refused switch-back
    assert by_tool[("probe", 3)]["charged"] == 1
    assert trace["charged_steps"] == 2

    # And it records WHERE each step ran — the question the incident came down to.
    assert by_tool[("probe", 3)]["workspace"].endswith("deaf-communication-terminal")

    # The reply is linked to the trace, so the app can find it from chat history.
    from server.db import store as memory
    turns = memory.get_recent("s1", n=5)
    assert turns[-1]["run_id"] == trace["id"]


# ── configuration reaches the deployed runtime ────────────────────────────────

def test_env_is_found_from_a_deployment_worktree(tmp_path, monkeypatch):
    """The server runs from ~/.codeasachat/deploy_worktrees/deploy-N, a git
    worktree — and .env is gitignored, so it is not there. A bare load_dotenv()
    found nothing and the process came up with NO configuration: that is how
    production lost its OPENAI_API_KEY, silently killing the backup brain and
    leaving a 3B local model as the only alternative to Claude.
    """
    import importlib
    from server import config as config_module

    canonical = tmp_path / "dot-codeasachat" / ".env"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("OPENAI_API_KEY=sk-from-canonical\n")

    worktree = tmp_path / "deploy_worktrees" / "deploy-99"
    (worktree / "server").mkdir(parents=True)
    assert not (worktree / ".env").exists()

    monkeypatch.setattr(config_module.Path, "home",
                        staticmethod(lambda: tmp_path / "dot-codeasachat" / ".."),
                        raising=False)
    monkeypatch.setenv("CODEASACHAT_ENV_FILE", str(canonical))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    found = config_module._load_env()
    assert found == str(canonical)
    assert os.environ.get("OPENAI_API_KEY") == "sk-from-canonical"

    importlib.reload(config_module)   # leave the module as the rest of the suite expects


# ── the routing brain ─────────────────────────────────────────────────────────

def test_qwen_is_a_backup_never_the_primary(monkeypatch):
    """A 3B local model as the PRIMARY router produced off-schema decisions,
    fabricated paths and garbled replies. It stays in the chain as a last resort
    — when Claude and OpenAI are both gone, a degraded answer beats none."""
    monkeypatch.setattr(config, "QWEN_TASKS", "")
    monkeypatch.setattr(config, "SHELL_LLM_PROVIDER", "auto")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")

    for task in ("shell", "notes", "diary", "reminders"):
        chain = shell._provider_chain(task)
        assert chain[-1] == "qwen", f"{task}: qwen must be the last resort"
        assert chain[0] != "qwen", f"{task}: qwen must never lead"

    # Still explicitly opt-in-able for the simpler formatting tasks.
    monkeypatch.setattr(config, "QWEN_TASKS", "notes,diary")
    assert shell._provider_chain("notes")[0] == "qwen"
    assert shell._provider_chain("shell")[0] != "qwen"

    # And a one-line override still conserves Claude entirely.
    monkeypatch.setattr(config, "QWEN_TASKS", "")
    monkeypatch.setattr(config, "SHELL_LLM_PROVIDER", "openai")
    assert shell._provider_chain("shell")[0] == "openai"


def test_off_schema_decisions_are_rejected_so_they_escalate():
    """The validator is what makes a weak brain fall through to a stronger one.
    It used to accept ANY non-empty string action, so invented verbs sailed
    through and surfaced to the user as raw JSON."""
    ok = shell._is_usable_decision
    # Valid shapes.
    assert ok('{"action":"done","reply":"hi"}')
    assert ok('{"action":"call","tool":"projects","args":"current"}')
    assert ok('{"tool":"projects","args":"current"}')          # bare tool
    assert ok('{"action":"projects","args":"current"}')        # tool-as-action
    # Invalid: an invented verb, which the old rule let through.
    assert not ok('{"action":"switch","project":"deaf-communication-terminal"}')
    assert not ok('{"action":"call"}')                          # call with no tool
    assert not ok('{"project_name":"x","description":"y"}')     # off-schema blob
    assert not ok("not json at all")


def test_a_weak_brain_escalates_and_the_trace_says_so(projects_dir, monkeypatch):
    """The real escalation path, end to end: a local model emits an off-schema
    decision, it is rejected, the next brain answers — and the trace records
    exactly that, because "which model produced this" is the first useful
    question when a reply looks wrong."""
    from server.db import agent_runs_store

    # Exercise the REAL _haiku (the provider chain lives there); stub only the
    # per-provider transport.
    monkeypatch.setattr(shell, "_provider_chain", lambda task: ["qwen", "claude"])
    seen: list[str] = []

    async def fake_call(provider, system_prompt, user_message, timeout, model,
                        json_mode=False):
        seen.append(provider)
        if provider == "qwen":
            # Exactly the shape that used to sail through validation.
            return '{"action":"switch","project":"deaf-communication-terminal"}'
        return '{"action":"done","reply":"On deaf-communication-terminal."}'

    monkeypatch.setattr(shell, "_call_llm", fake_call)

    async def turn():
        with workspace.bound(projects_dir / "general"):
            return await shell.ShellSkill().run("where am I", session_id="s-brain")

    reply = asyncio.run(turn())

    assert seen == ["qwen", "claude"], "qwen's bad output must escalate, not stand"
    assert reply == "On deaf-communication-terminal."
    runs = agent_runs_store.list_runs(session_id="s-brain")
    assert runs[0]["brains"] == "qwen:rejected -> claude"


def test_charged_steps_ignores_free_ones():
    assert shell._charged_steps([]) == 0
    assert shell._charged_steps([
        {"charged": True}, {"charged": False}, {},   # {} defaults to charged
    ]) == 2


# ── LLM call hygiene ──────────────────────────────────────────────────────────

def test_one_shot_llm_calls_carry_no_ambient_context(monkeypatch, tmp_path):
    """A one-shot LLM call must receive the caller's prompt and nothing else.

    Two leaks, both real and both found in production. The Claude CLI loads the
    CLAUDE.md of whatever directory it starts in, and the server's cwd is inside
    THIS repo — so every routing and refinement call was handed Code-as-a-Chat's
    project memory. And the subprocess inherited the parent's stdin, so whatever
    sat on it was appended to the prompt. Together they refined a work order for
    another project entirely against this repo's context.
    """
    import asyncio as _asyncio
    captured = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (b'{"result": "ok"}', b"")

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(shell.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(_asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(shell._claude_cli("sys", "hello", timeout=5))

    # No project's CLAUDE.md: the call starts from an empty scratch directory.
    cwd = captured["kwargs"].get("cwd")
    assert cwd, "the CLI must be given an explicit cwd, not inherit the server's"
    assert "Projects" not in str(cwd), f"cwd must not sit inside a project: {cwd}"

    # Nothing on stdin can reach the prompt.
    assert captured["kwargs"].get("stdin") is _asyncio.subprocess.DEVNULL


def test_refiner_tells_the_model_which_project_the_job_targets(monkeypatch, tmp_path):
    """Job #21 targeted TFI-banisa and came back a complete work order for
    Code-as-a-Chat, because the refiner never said which repo the job was for."""
    import server.work_order_refiner as wor
    from server.db import night_queue_store

    monkeypatch.setattr(
        night_queue_store, "get",
        lambda _id: {"id": 21, "status": "held",
                     "project": "/Users/someone/Projects/TFI-banisa",
                     "task": "Create GitHub repository and push local code",
                     "spec_json": {"source_text": "Create GitHub repository and push local code"}},
    )
    monkeypatch.setattr(wor, "assess", lambda *a, **k: {}, raising=False)

    seen = {}

    async def fake_cli(system, prompt, timeout=90, model=None):
        seen["prompt"] = prompt
        raise RuntimeError("stop after capturing the prompt")

    monkeypatch.setattr(wor, "_claude_cli", fake_cli)
    with pytest.raises(RuntimeError):
        asyncio.run(wor.refine_job(21, allow_cloud=True))

    assert "TFI-banisa" in seen["prompt"], "the target project must be stated"
    # Still no repo contents or absolute paths in the cloud call.
    assert "/Users/" not in seen["prompt"]
