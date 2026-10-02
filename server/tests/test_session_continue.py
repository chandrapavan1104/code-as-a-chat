"""
Regression tests for the "access my Claude session from Gajala" incident
(conversations.db #1050–#1065, 2026-10-01).

Claude had hit its usage limit, so a fallback model answered. Three things went
wrong: the quota error surfaced as `[claude error code 1]` and was treated as a
sign-in problem; there was no way to continue a *specific* session, so the
request ran in the wrong folder's session; and the fallback claimed "Switched to
the UltraSync session" after only reading it.
"""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import server.skills.projects  # noqa: F401 — registers the skill
from server import brain_health, config, usage_limits, workspace
from server.db import cli_runs_store, cli_sessions_store, native_sessions
from server.outcomes import false_switch_claim
from server.skills import registry, sessions, shell
from server.skills.base import Skill, SkillResult
from server.skills.cli_base import CLISubprocessSkill

LIMIT_JSON = json.dumps({
    "is_error": True, "api_error_status": 429, "session_id": "64fc3626",
    "result": "You've hit your session limit · resets 5:50pm (America/Los_Angeles)"})


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    parent = tmp_path / "Projects"
    for name in ("general", "gogon-home-automation"):
        (parent / name).mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_PARENT_DIR", str(parent))
    monkeypatch.setattr(config, "WORKSPACE_DIR", parent / "general")
    monkeypatch.setattr(config, "CONTEXT_AUTO_INIT", False, raising=False)
    monkeypatch.setattr(config, "CONTEXT_AUTO_SYNC", False, raising=False)
    monkeypatch.setattr(cli_sessions_store, "DB_PATH", tmp_path / "cli_sessions.db")
    monkeypatch.setattr(cli_runs_store, "DB_PATH", tmp_path / "cli_runs.db")
    cli_sessions_store._init()
    cli_runs_store.init()
    return parent


# ── 1. usage limits are reported as usage limits ──────────────────────────────

def test_limit_notice_gives_local_reset_time_and_rules_out_sign_in():
    # The incident: 3:15 pm on the owner's India-time Mac = 2:45 am in Los Angeles.
    now = datetime(2026, 10, 1, 2, 45, tzinfo=ZoneInfo("America/Los_Angeles"))
    text = usage_limits.notice("Claude", LIMIT_JSON, now=now,
                               local_tz=ZoneInfo("Asia/Kolkata"))
    assert text.startswith("Claude's usage limit is reached. It resets around 6:20 AM tomorrow.")
    assert "not a sign-in problem" in text


def test_limit_notice_same_day_and_unparseable_reset():
    now = datetime(2026, 10, 1, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    same_zone = usage_limits.local_reset("resets 5pm (America/Los_Angeles)", now=now,
                                         local_tz=ZoneInfo("America/Los_Angeles"))
    assert same_zone == "5:00 PM"
    assert usage_limits.local_reset("resets soon") is None
    assert usage_limits.notice("Codex", "429 Too Many Requests").startswith(
        "Codex's usage limit is reached. This is a usage quota")


def test_other_failures_are_not_called_usage_limits():
    assert usage_limits.notice("Claude", "OAuth token expired, please log in") is None
    assert usage_limits.notice("Claude", "fatal: not a git repository") is None


class _FakeCli(CLISubprocessSkill):
    name = "fakecli"
    description = "test CLI"
    cli_name = "fakecli"
    install_hint = ""
    supports_sessions = True

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.commands: list[list[str]] = []

    def build_command(self, prompt, resume_id=None, new_id=None, model=""):
        return ["fakecli", prompt, f"resume={resume_id}"]

    def extract_session_id(self, stdout):
        return "after-run"

    async def _spawn(self, cmd, cwd):
        self.commands.append(cmd)
        return self.outputs.pop(0)


def _run_cli(skill, monkeypatch, folder):
    monkeypatch.setattr("server.skills.cli_base.shutil.which", lambda _: "/bin/fakecli")

    async def go():
        with workspace.bound(folder):
            return await skill.run("do the thing")
    return asyncio.run(go())


def test_cli_quota_error_is_a_clear_failed_result(projects_dir, monkeypatch):
    out = _run_cli(_FakeCli([(1, LIMIT_JSON, "")]), monkeypatch, projects_dir / "general")
    assert isinstance(out, SkillResult) and out.status == "failed"
    assert out.data["kind"] == "usage_limit"
    assert "usage limit is reached" in out.message and "error code" not in out.message


def test_cli_other_error_keeps_detail_and_is_marked_failed(projects_dir, monkeypatch):
    out = _run_cli(_FakeCli([(2, "", "boom")]), monkeypatch, projects_dir / "general")
    assert out.status == "failed" and str(out) == "[fakecli error code 2]\nboom"


def test_shell_fallback_note_names_the_quota(monkeypatch):
    monkeypatch.setattr(brain_health, "_failures", {})
    brain_health.failed("claude", RuntimeError(
        "You've hit your session limit · resets 5:50pm (America/Los_Angeles)"))
    note = brain_health.fallback_note()
    assert note.startswith("Claude's usage limit is reached.")
    assert note.endswith("A backup model answered this.")
    assert brain_health.snapshot()["claude"]["reason"] == "quota or rate limit reached"


# ── 2. continue one specific session ──────────────────────────────────────────

def test_pin_beats_newest_native_session_until_it_has_run(projects_dir, monkeypatch):
    folder = projects_dir / "gogon-home-automation"
    monkeypatch.setattr(native_sessions, "latest", lambda cwd, engine: ("newest-native", 0.0))
    skill = _FakeCli([(0, "{}", "")])
    assert skill._resume_id(str(folder)) == "newest-native"

    cli_sessions_store.pin(str(folder), "fakecli", "4cbce6b5-chosen")
    assert skill._resume_id(str(folder)) == "4cbce6b5-chosen"

    _run_cli(skill, monkeypatch, folder)
    assert skill.commands == [["fakecli", "do the thing", "resume=4cbce6b5-chosen"]]
    # It has now run, so it is the folder's newest and the pin is spent.
    assert cli_sessions_store.pinned(str(folder), "fakecli") is None


def _fake_sessions(monkeypatch, folder):
    monkeypatch.setattr(sessions, "_all_sessions", lambda: [{
        "engine": "claude", "id": "4cbce6b5-aaaa-bbbb", "mtime": 0.0,
        "cwd": str(folder), "preview": "ultra sync+ home automation", "path": ""}])


class _Engine(Skill):
    name = "claude"
    description = "stand-in for the claude CLI skill"

    def __init__(self, reply="registered the email"):
        self.reply = reply
        self.calls: list[tuple[str, str, str | None]] = []

    async def run(self, prompt="", **kwargs):
        folder = str(workspace.active())
        self.calls.append((workspace.name(), prompt,
                           cli_sessions_store.pinned(folder, "claude")))
        return self.reply


def test_continue_moves_the_turn_and_pins_the_session(projects_dir, monkeypatch):
    target = projects_dir / "gogon-home-automation"
    _fake_sessions(monkeypatch, target)

    async def go():
        with workspace.bound(projects_dir / "general"):
            out = await sessions.SessionsSkill().run("continue 4cbce6b5")
            return out, workspace.name()

    out, ended_in = asyncio.run(go())
    assert out.status == "succeeded" and out.changed
    assert ended_in == "gogon-home-automation"
    assert cli_sessions_store.pinned(str(target), "claude") == "4cbce6b5-aaaa-bbbb"
    assert "[[switch:gogon-home-automation]]" in out.message


def test_continue_with_message_sends_it_to_that_session(projects_dir, monkeypatch):
    target = projects_dir / "gogon-home-automation"
    _fake_sessions(monkeypatch, target)
    engine = _Engine()
    monkeypatch.setitem(registry, "claude", engine)

    async def go():
        with workspace.bound(projects_dir / "general"):
            return await sessions.SessionsSkill().run(
                "continue 4cbce6b5 register gowtham and give website access")

    out = asyncio.run(go())
    assert engine.calls == [("gogon-home-automation",
                             "register gowtham and give website access",
                             "4cbce6b5-aaaa-bbbb")]
    assert out.status == "succeeded" and "registered the email" in out.message


def test_continue_reports_a_quota_failure_instead_of_success(projects_dir, monkeypatch):
    _fake_sessions(monkeypatch, projects_dir / "gogon-home-automation")
    monkeypatch.setitem(registry, "claude", _Engine(
        SkillResult("failed", "Claude's usage limit is reached.")))

    async def go():
        with workspace.bound(projects_dir / "general"):
            return await sessions.SessionsSkill().run("continue 4cbce6b5 do it")

    out = asyncio.run(go())
    assert out.status == "failed" and "usage limit is reached" in out.message


def test_continue_unknown_id_and_missing_folder(projects_dir, monkeypatch):
    _fake_sessions(monkeypatch, projects_dir / "deleted-project")

    async def go(args):
        with workspace.bound(projects_dir / "general"):
            return await sessions.SessionsSkill().run(args), workspace.name()

    out, ended_in = asyncio.run(go("continue ffff"))
    assert out.status == "not_found" and ended_in == "general"
    out, ended_in = asyncio.run(go("continue 4cbce6b5"))
    assert out.status == "not_found" and "not a folder" in out.message
    assert ended_in == "general"


# ── 3. no claiming a switch that did not happen ───────────────────────────────

def test_false_switch_claim_rules():
    names = ["general", "gogon-home-automation"]
    claim = "Switched to the UltraSync session. Last update: both phone and browser working."
    assert false_switch_claim(claim, switched=False, project_names=names, steps=[])
    assert false_switch_claim("I moved you to gogon-home-automation.", switched=False,
                              project_names=names, steps=[])
    # A real switch, a model change, and an "already there" no-op are all fine.
    assert not false_switch_claim(claim, switched=True, project_names=names, steps=[])
    assert not false_switch_claim("Switched to opus.", switched=False,
                                  project_names=names, steps=[])
    assert not false_switch_claim(
        "Switched to the general project.", switched=False, project_names=names,
        steps=[{"result": "Already on ~/Projects/general — nothing to change."}])


def _replay(monkeypatch, decisions):
    seq = iter(decisions)

    async def fake_haiku(*args, **kwargs):
        return next(seq, '{"action":"done","reply":"finished"}')

    monkeypatch.setattr(shell, "_haiku", fake_haiku)


def _turn(folder, prompt):
    async def go():
        with workspace.bound(folder):
            return await shell.ShellSkill().run(prompt), workspace.name()
    return asyncio.run(go())


def test_incident_replay_reading_a_session_is_not_switching(projects_dir, monkeypatch):
    _fake_sessions(monkeypatch, projects_dir / "gogon-home-automation")
    monkeypatch.setattr(sessions, "_show_view", lambda prefix: "SESSION 4cbce6b5 … 43 turns")
    _replay(monkeypatch, [
        '{"action":"call","tool":"sessions","args":"show 4cbce6b5"}',
        '{"action":"done","reply":"Switched to the UltraSync session. Last update: layout live."}',
    ])
    reply, ended_in = _turn(projects_dir / "general", "I want to switch to that session here")
    assert ended_in == "general"
    assert reply.startswith("Correction: I did not switch anything. This chat is still in general.")


def test_real_continue_is_reported_without_correction(projects_dir, monkeypatch):
    _fake_sessions(monkeypatch, projects_dir / "gogon-home-automation")
    _replay(monkeypatch, [
        '{"action":"call","tool":"sessions","args":"continue 4cbce6b5"}',
        '{"action":"done","reply":"Switched to the UltraSync session in gogon-home-automation."}',
    ])
    reply, ended_in = _turn(projects_dir / "general", "I want to switch to that session here")
    assert ended_in == "gogon-home-automation"
    assert "Correction" not in reply
