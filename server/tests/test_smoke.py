"""Smoke tests — cheap sanity checks that the server wiring imports and holds
together, with no external CLIs or network. Keeps CI meaningful and green."""

import asyncio
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _isolated_capability_registry(tmp_path, monkeypatch):
    """Awareness tests must never read or rewrite the live Mac Mini inventory."""
    from server import capability_registry
    from server.db import capability_store
    monkeypatch.setattr(capability_store, "DB_PATH", tmp_path / "capabilities.db")
    monkeypatch.setattr(capability_registry, "_last_refresh", 0.0)


def _ready_work_order(title="Ready task"):
    return {
        "version": 2, "readiness": "refined", "title": title,
        "outcome": "The requested behavior works", "plan": ["Implement it"],
        "policy": "Preserve existing behavior and user changes",
        "acceptance": ["The behavior is verified"],
        "test_handoff": "Return exact phone test steps",
    }


def test_config_imports_and_has_token():
    from server import config
    assert isinstance(config.API_TOKEN, str) and config.API_TOKEN
    assert isinstance(config.BATTERY_ALERTS, bool)


def test_api_v2_router_mounts():
    from server.api_v2 import router
    paths = {r.path for r in router.routes}
    assert "/api/system" in paths
    assert "/api/devices" in paths
    assert "/api/queue/{job_id}/refine" in paths
    assert "/api/capabilities" in paths


def test_fcm_available_is_bool_without_key():
    # No service-account key on CI → available() must return False, not raise.
    from server import fcm
    assert isinstance(fcm.available(), bool)


def test_skills_register():
    from server.skills import discover, registry
    discover()  # import every skill module so each self-registers
    assert {"shell", "mac", "notes", "reminders"} <= set(registry)


def test_shell_accepts_tool_name_as_action(monkeypatch):
    from server.skills import registry
    from server.skills.base import Skill
    from server.skills import shell

    class ProbeSkill(Skill):
        name = "probe"
        description = "test probe"
        final_output = True

        async def run(self, prompt="", **kwargs):
            return f"probe ran: {prompt}"

    async def fake_haiku(*args, **kwargs):
        return '{"action":"probe","args":"status","final":true}'

    monkeypatch.setitem(registry, "probe", ProbeSkill())
    monkeypatch.setattr(shell, "_haiku", fake_haiku)

    result = asyncio.run(shell.ShellSkill().run("check status"))

    assert result == "probe ran: status"


def test_shell_coerces_structured_tool_args(monkeypatch):
    from server.skills import registry
    from server.skills.base import Skill
    from server.skills import shell

    class ProbeSkill(Skill):
        name = "probe"
        description = "test probe"
        final_output = True

        async def run(self, prompt="", **kwargs):
            return f"probe ran: {prompt}"

    async def fake_haiku(*args, **kwargs):
        return (
            '{"action":"call","tool":"probe",'
            '"args":{"path":"notes.md","mode":"read"},"final":true}'
        )

    monkeypatch.setitem(registry, "probe", ProbeSkill())
    monkeypatch.setattr(shell, "_haiku", fake_haiku)

    result = asyncio.run(shell.ShellSkill().run("check status"))

    assert result == 'probe ran: {"path":"notes.md","mode":"read"}'


def test_usage_ignores_expired_rate_limits(monkeypatch):
    from server import api_v2

    monkeypatch.setattr(api_v2.time, "time", lambda: 2_000)
    report = {"limitUsage": [
        {"window": "5h", "usedPercent": 80, "resetsAt": 1_999},
        {"window": "7d", "usedPercent": 25, "resetsAt": 2_001},
    ]}

    assert api_v2._rate_pcts(report) == (None, 25)
    assert api_v2._limits(report) == [
        {"label": "weekly", "pct": 25.0, "detail": None},
    ]


def test_all_coding_agents_allow_ten_minute_turns():
    from server.skills.claude_code import ClaudeCodeSkill
    from server.skills.antigravity import AntigravitySkill
    from server.skills.codex import CodexSkill

    assert ClaudeCodeSkill.timeout == 600
    assert AntigravitySkill.timeout == 600
    assert CodexSkill.timeout == 600


def test_filemanager_resolves_relative_to_active_workspace(tmp_path, monkeypatch):
    from server import config
    from server.skills.filemanager import FileManagerSkill

    workspace = tmp_path / "active-project"
    workspace.mkdir()
    (workspace / "README.md").write_text("active readme")
    monkeypatch.setattr(config, "WORKSPACE_DIR", workspace)

    result = asyncio.run(FileManagerSkill().run("read README.md"))

    assert "active readme" in result
    assert str(workspace / "README.md") in result


def test_filemanager_recovers_bare_known_project(tmp_path, monkeypatch):
    from server import config
    from server.skills.filemanager import FileManagerSkill

    current = tmp_path / "current"
    sibling = tmp_path / "Sibling-Project"
    current.mkdir()
    sibling.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", current)
    monkeypatch.setenv("PROJECTS_PARENT_DIR", str(tmp_path))

    result = asyncio.run(FileManagerSkill().run("list Sibling-Project"))

    assert result.startswith(f"{sibling}/")


def test_shell_repeats_last_update_without_regeneration():
    from server.skills.shell import ShellSkill

    recent = [
        {"role": "user", "content": "start the build"},
        {"role": "assistant", "content": "Build is 60% complete."},
    ]

    assert (ShellSkill._repeat_last_reply("Give me the update again", recent)
            == "Build is 60% complete.")
    assert ShellSkill._repeat_last_reply("try the build again", recent) is None


def test_shell_project_handoff_reads_context_then_uses_pinned_agent(monkeypatch):
    from server import prefs
    from server.skills.shell import ShellSkill

    prompt = "Do you have the project idea passed to you? if yes, build it"
    monkeypatch.setattr(prefs, "get_coding_engine", lambda: "claude")

    first = ShellSkill._project_intent_action(prompt, [])
    second = ShellSkill._project_intent_action(prompt, [
        {"tool": "context", "args": "show", "result": "project context"},
    ])

    assert first == {"action": "call", "tool": "context", "args": "show"}
    assert second["tool"] == "claude"
    assert "AGENTS.md" in second["args"]


def test_shell_short_build_command_uses_pinned_agent(monkeypatch):
    from server import prefs
    from server.skills.shell import ShellSkill

    monkeypatch.setattr(prefs, "get_coding_engine", lambda: "codex")

    action = ShellSkill._project_intent_action("implement the project", [])

    assert action == {
        "action": "call", "tool": "codex", "args": "implement the project",
    }


def test_shell_does_not_repeat_identical_timed_out_call(monkeypatch):
    from server.skills import registry
    from server.skills.base import Skill
    from server.skills import shell

    calls = 0
    decisions = iter([
        '{"action":"call","tool":"slow_probe","args":"build everything"}',
        '{"action":"call","tool":"slow_probe","args":"build everything"}',
    ])

    class SlowProbe(Skill):
        name = "slow_probe"
        description = "test timeout"

        async def run(self, prompt="", **kwargs):
            nonlocal calls
            calls += 1
            return "[slow_probe] Timed out after 600s"

    async def fake_haiku(*args, **kwargs):
        return next(decisions)

    monkeypatch.setitem(registry, "slow_probe", SlowProbe())
    monkeypatch.setattr(shell, "_haiku", fake_haiku)

    result = asyncio.run(shell.ShellSkill().run("run the slow probe"))

    assert calls == 1
    assert "not retried automatically" in result


def test_coding_engine_is_pinned_per_project(tmp_path, monkeypatch):
    from server import prefs

    monkeypatch.setattr(prefs, "STATE_FILE", tmp_path / "state.json")
    one, two = tmp_path / "one", tmp_path / "two"
    prefs.set_coding_engine("claude", one)
    prefs.set_coding_engine("codex", two)

    assert prefs.get_coding_engine(one) == "claude"
    assert prefs.get_coding_engine(two) == "codex"


def test_legacy_global_engine_is_frozen_when_project_is_first_seen(tmp_path, monkeypatch):
    import json
    from server import prefs

    state_file = tmp_path / "state.json"
    state_file.write_text('{"coding_engine":"claude"}')
    monkeypatch.setattr(prefs, "STATE_FILE", state_file)
    project = tmp_path / "project"

    assert prefs.get_coding_engine(project) == "claude"
    prefs.set_coding_engine("codex", tmp_path / "other")
    assert prefs.get_coding_engine(project) == "claude"
    assert json.loads(state_file.read_text())["coding_engines"][str(project)] == "claude"


def test_backup_model_keeps_the_same_cli_session(tmp_path, monkeypatch):
    from server import config, prefs
    from server.db import cli_runs_store, cli_sessions_store
    from server.skills.claude_code import ClaudeCodeSkill

    calls = []
    replies = iter([
        (1, "", "primary failed"),
        (0, '{"session_id":"same-session","result":"ok"}', ""),
    ])

    async def fake_spawn(_self, cmd, cwd):
        calls.append(cmd)
        return next(replies)

    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(prefs, "get_coding_model", lambda _engine: "opus")
    monkeypatch.setattr(prefs, "get_backup_model", lambda _engine: "sonnet")
    monkeypatch.setattr(ClaudeCodeSkill, "_resume_id", lambda _self, _cwd: "same-session")
    monkeypatch.setattr(ClaudeCodeSkill, "_spawn", fake_spawn)
    monkeypatch.setattr(cli_runs_store, "add", lambda **_kwargs: None)
    monkeypatch.setattr(cli_sessions_store, "set", lambda *_args: None)
    monkeypatch.setattr("server.skills.cli_base.shutil.which", lambda _name: "/bin/cli")

    result = asyncio.run(ClaudeCodeSkill().run("build", session_id="app:test"))

    assert len(calls) == 2
    assert all(cmd[cmd.index("--resume") + 1] == "same-session" for cmd in calls)
    assert "retried on backup sonnet" in result


def test_cli_usage_parsers():
    from server.skills.antigravity import AntigravitySkill
    from server.skills.claude_code import ClaudeCodeSkill
    from server.skills.codex import CodexSkill

    claude = '{"usage":{"input_tokens":10,"output_tokens":4,' \
             '"cache_creation_input_tokens":3,"cache_read_input_tokens":20}}'
    codex = '{"type":"turn.completed","usage":{"total_tokens":40,' \
            '"cached_input_tokens":25}}'
    gemini = '{"stats":{"models":{"m":{"tokens":{"input":8,' \
             '"candidates":2,"cached":30,"thoughts":5}}}}}'

    assert ClaudeCodeSkill().extract_usage(claude) == (37, 17)
    assert CodexSkill().extract_usage(codex) == (40, 15)
    assert AntigravitySkill().extract_usage(gemini) == (45, 15)


def test_local_qwen_usage_log_stores_metrics_not_content(tmp_path, monkeypatch):
    import json
    from server.db import local_llm_usage_store

    log = tmp_path / "qwen_usage.jsonl"
    monkeypatch.setattr(local_llm_usage_store, "DB_PATH", log)
    response = {
        "message": {"content": "private answer"},
        "prompt_eval_count": 12,
        "eval_count": 8,
        "total_duration": 900,
    }
    local_llm_usage_store.record(
        response=response, model="qwen2.5:3b", cwd="/Projects/demo",
        source="gajala", session_id="app:test",
    )

    row = json.loads(log.read_text())
    assert row["inputTokens"] == 12
    assert row["outputTokens"] == 8
    assert row["totalTokens"] == 20
    assert row["sessionId"] == "app:test"
    assert "private answer" not in log.read_text()


def test_night_queue_store_roundtrip_and_atomic_claim(tmp_path, monkeypatch):
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")

    jid = q.add(project="/tmp/proj", task="add CSV export", tag="auto",
                engine="codex", spec=_ready_work_order("Add CSV export"))
    held = q.add(project="/tmp/proj", task="redesign nav", tag="mine")

    # A held (mine) job is never queued/claimable.
    assert q.get(held)["status"] == "held"
    # An engine-pinned job is only claimed by its engine.
    assert q.claim_next("claude") is None
    claimed = q.claim_next("codex")
    assert claimed["id"] == jid and claimed["status"] == "running"
    # It's gone from the runnable set now (no double-claim).
    assert q.claim_next("codex") is None
    assert q.claim_next("gemini") is None

    q.update(jid, status="staged", files_changed=["server/x.py"], tokens_total=1234)
    row = q.get(jid)
    assert row["status"] == "staged"
    assert row["files_changed"] == ["server/x.py"]
    assert [j["id"] for j in q.list_jobs(status="staged")] == [jid]


def test_queue_dependencies_block_until_shipped(tmp_path, monkeypatch):
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    foundation = q.add(project="/tmp/proj", task="foundation", tag="mine")
    dependent = q.add(
        project="/tmp/proj", task="dependent", depends_on=[foundation],
        spec=_ready_work_order("Dependent task"),
    )

    assert q.blocked_by(q.get(dependent)) == [foundation]
    assert q.claim_next("codex") is None
    q.update(foundation, status="deployed")
    assert q.claim_next("codex") is None  # branch exists, but is not in base
    q.update(foundation, status="shipped")
    assert q.claim_next("codex")["id"] == dependent


def test_closed_shipped_dependency_remains_satisfied(tmp_path, monkeypatch):
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    foundation = q.add(
        project="/tmp/proj", task="foundation", tag="mine",
        spec=_ready_work_order("Foundation"),
    )
    q.update(foundation, status="shipped")
    q.close(foundation, "finished")
    q.reopen(foundation)
    q.close(foundation, "archived again")
    dependent = q.add(
        project="/tmp/proj", task="dependent", depends_on=[foundation],
        spec=_ready_work_order("Dependent"),
    )

    job = q.get(dependent)
    assert q.blocked_by(job) == []
    assert q.dependency_status(job)[0]["satisfied"] is True
    assert q.claim_next("codex")["id"] == dependent


def test_queue_close_is_recoverable_and_reopens_held(tmp_path, monkeypatch):
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    jid = q.add(project="/tmp/proj", task="safe task")

    assert q.close(jid, "superseded") is True
    closed = q.get(jid)
    assert closed["status"] == "closed"
    assert closed["close_reason"] == "superseded"
    assert closed["closure_history"][0]["previous_status"] == "held"
    assert q.claim_next("codex") is None

    assert q.reopen(jid) is True
    reopened = q.get(jid)
    assert reopened["status"] == "held"
    assert reopened["tag"] == "mine"
    assert reopened["closure_history"][0]["reason"] == "superseded"


def test_queue_schema_migrates_legacy_rows(tmp_path, monkeypatch):
    import sqlite3
    from server.db import night_queue_store as q

    db = tmp_path / "night_queue.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY, project TEXT NOT NULL, task TEXT NOT NULL,
                tag TEXT NOT NULL, engine TEXT NOT NULL, priority INTEGER NOT NULL,
                status TEXT NOT NULL, origin TEXT NOT NULL, branch TEXT, base TEXT,
                summary TEXT, files_changed TEXT, engine_used TEXT,
                tokens_total INTEGER NOT NULL, tokens_billable INTEGER NOT NULL,
                created_at REAL NOT NULL, started_at REAL, ended_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO jobs VALUES (1,'/tmp/p','legacy task','auto','auto',0,"
            "'queued','queue',NULL,NULL,NULL,NULL,NULL,0,0,1,NULL,NULL)"
        )
    monkeypatch.setattr(q, "DB_PATH", db)

    q.init()
    row = q.get(1)
    assert row["task"] == "legacy task"
    assert row["spec_json"]["title"] == "legacy task"
    assert row["spec_json"]["readiness"] == "draft"
    assert row["depends_on"] == []
    assert row["status"] == "held" and row["tag"] == "mine"
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT spec_json FROM jobs WHERE id = 1"
        ).fetchone()[0]


def test_work_order_roundtrip():
    from server.work_orders import WorkOrderSpec, from_task

    spec = WorkOrderSpec(
        title="Add export", outcome="Users download CSV", plan=["Add endpoint"],
        policy="Read-only export", acceptance=["CSV opens"],
        test_handoff="Return the phone URL",
    )
    parsed = from_task(spec.as_task())
    assert parsed.title == spec.title
    assert parsed.plan == spec.plan
    assert parsed.acceptance == spec.acceptance
    assert parsed.readiness == "refined"


def test_legacy_qwen_research_is_not_left_as_coding():
    from server.work_orders import migrate_spec

    stored = _ready_work_order("Research European businesses")
    stored.update({
        "refined_by": "local-qwen",
        "context": "Find potential clients in European countries",
        "plan": ["Conduct web searches", "Review LinkedIn and business listings"],
    })
    spec = migrate_spec(stored, "find small European companies")

    assert spec.work_type == "research"


def test_rough_queue_capture_is_draft_held_and_unclaimable(tmp_path, monkeypatch):
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    jid = q.add(project="/tmp/proj", task="make login better", tag="auto")
    job = q.get(jid)

    assert job["spec_json"]["readiness"] == "draft"
    assert job["status"] == "held" and job["tag"] == "mine"
    assert q.claim_next("codex") is None


def test_refinement_replaces_prompt_but_preserves_rough_capture(
        tmp_path, monkeypatch):
    from server import work_order_refiner
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    jid = q.add(project=str(tmp_path), task="make login better")
    seen = {}

    async def fake_claude(*args, **kwargs):
        seen["prompt"] = args[1]
        return ('{"work_type":"coding","title":"Improve login feedback","outcome":"Login errors are clear",'
                '"context":"Current feedback is vague","plan":["Add messages"],'
                '"policy":"Preserve auth behavior","acceptance":["Errors are visible"],'
                '"test_handoff":"Open Login on the phone and enter a bad password",'
                '"out_of_scope":"Changing authentication","assumptions":[]}')

    monkeypatch.setattr(work_order_refiner, "_claude_cli", fake_claude)
    job = asyncio.run(work_order_refiner.refine_job(
        jid, allow_cloud=True, instructions="Keep the existing auth flow"))

    assert job["spec_json"]["readiness"] == "refined"
    assert job["spec_json"]["work_type"] == "coding"
    assert job["spec_json"]["source_text"] == "make login better"
    assert job["status"] == "held" and job["tag"] == "mine"
    assert job["task"].startswith("TITLE: Improve login feedback")
    assert "Keep the existing auth flow" in seen["prompt"]


def test_manual_queue_edit_updates_spec_and_holds_job(tmp_path, monkeypatch):
    from server import api_v2
    from server.db import night_queue_store as q
    from server.work_orders import WorkOrderSpec

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    jid = q.add(project=str(tmp_path), task="rough research")
    spec = WorkOrderSpec.model_validate({
        **_ready_work_order("Research market"),
        "work_type": "research", "source_text": "rough research",
    })
    edited = api_v2.queue_edit(jid, api_v2.QueueEditIn(
        spec=spec, depends_on=[], engine="gemini"))

    assert edited["status"] == "held"
    assert edited["spec"]["work_type"] == "research"
    assert edited["spec"]["refined_by"] == "manual"


def test_queue_engine_can_be_pinned_or_returned_to_auto(tmp_path, monkeypatch):
    from server import api_v2
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    jid = q.add(project=str(tmp_path), task="ready", spec=_ready_work_order())
    q.update(jid, engine="gemini", engine_used="gemini", status="failed")

    changed = api_v2.queue_engine(jid, api_v2.QueueEngine(engine="claude"))
    assert changed["engine"] == "claude"
    changed = api_v2.queue_engine(jid, api_v2.QueueEngine(engine="auto"))
    assert changed["engine"] == "auto"


def test_research_job_bypasses_git_and_stores_report(tmp_path, monkeypatch):
    from server import night_shift
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    spec = _ready_work_order("Research small companies")
    spec["work_type"] = "research"
    jid = q.add(project=str(tmp_path), task="research companies", spec=spec)
    job = q.get(jid)

    async def fake_research(*args, **kwargs):
        return "Three sourced leads: https://example.com", 120, 120, None

    async def no_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(night_shift.night_exec, "run_research_job", fake_research)
    monkeypatch.setattr(night_shift, "_notify_status", no_notify)
    asyncio.run(night_shift._process_job_locked(job, "gemini", str(tmp_path), jid))

    result = q.get(jid)
    assert result["status"] == "completed"
    assert "https://example.com" in result["summary"]


def test_coding_job_uses_isolated_worktree_when_live_repo_is_dirty(
        tmp_path, monkeypatch):
    from server import config, night_shift
    from server.db import night_queue_store as q

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "night@test.invalid"],
                   cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Night Test"], cwd=repo,
                   check=True)
    tracked = repo / "tracked.txt"
    tracked.write_text("committed\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True,
                   capture_output=True)
    tracked.write_text("owner's uncommitted work\n")

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    monkeypatch.setattr(night_shift, "_WORKTREE_DIR", tmp_path / "worktrees")
    monkeypatch.setattr(config, "REPO_DIR", tmp_path / "some-other-repo")
    jid = q.add(project=str(repo), task="add generated file", tag="mine",
                spec=_ready_work_order("Add generated file"))
    job = q.get(jid)

    async def fake_run(engine, cwd, task, timeout, on_spawn=None):
        (Path(cwd) / "generated.txt").write_text("made by worker\n")
        return "Implemented it", 42, 40, None

    async def no_notify(*args, **kwargs):
        return None

    from pathlib import Path
    monkeypatch.setattr(night_shift.night_exec, "run_job", fake_run)
    monkeypatch.setattr(night_shift, "_notify_status", no_notify)
    asyncio.run(night_shift._process_job_locked(
        job, "codex", str(repo), jid))

    result = q.get(jid)
    assert result["status"] == "staged"
    assert result["branch"].startswith(f"night/{jid}-")
    assert "generated.txt" in result["files_changed"]
    assert tracked.read_text() == "owner's uncommitted work\n"
    assert not (repo / "generated.txt").exists()
    assert not (tmp_path / "worktrees" / f"job-{jid}").exists()
    branch_file = subprocess.run(
        ["git", "show", f"{result['branch']}:generated.txt"], cwd=repo,
        check=True, capture_output=True, text=True).stdout
    assert branch_file == "made by worker\n"


def _deployment_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "deploy@test.invalid"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Deploy Test"], cwd=path,
                   check=True)
    (path / "owner.txt").write_text("base owner\n")
    (path / "feature.txt").write_text("base feature\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", "-b", "night/test"], cwd=path,
                   check=True, capture_output=True)
    (path / "feature.txt").write_text("deployed feature\n")
    subprocess.run(["git", "commit", "-am", "feature"], cwd=path, check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=path, check=True,
                   capture_output=True)


def test_deployment_merges_with_unrelated_owner_changes_and_records_ledger(
        tmp_path, monkeypatch):
    from server import deployment
    from server.db import deployment_store as ds, night_queue_store as q

    repo = tmp_path / "repo"
    _deployment_repo(repo)
    (repo / "owner.txt").write_text("owner is editing this\n")
    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(deployment, "_DEPLOY_WORKTREE_DIR", tmp_path / "deploy-worktrees")
    jid = q.add(project=str(repo), task="feature", spec=_ready_work_order())
    q.update(jid, status="staged", branch="night/test", base="main",
             files_changed=["feature.txt"])
    owner_head = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=repo, check=True,
        capture_output=True, text=True).stdout
    owner_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        capture_output=True, text=True).stdout

    result = deployment.deploy_branch(
        repo=str(repo), branch="night/test", base="main",
        changed_files=["feature.txt"], source="queue", ref_id=jid,
        server_touched=False)

    assert "is live" in result
    # The branch ref advances, but the owner's checkout and edits are untouched.
    assert (repo / "feature.txt").read_text() == "base feature\n"
    live = ds.latest(source="queue", ref_id=jid)
    deployed = subprocess.run(
        ["git", "show", f"{live['deployed_sha']}:feature.txt"], cwd=repo, check=True,
        capture_output=True, text=True).stdout
    assert deployed == "deployed feature\n"
    assert (repo / "owner.txt").read_text() == "owner is editing this\n"
    assert subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=repo, check=True,
        capture_output=True, text=True).stdout == owner_head
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        capture_output=True, text=True).stdout == owner_status
    assert q.get(jid)["status"] == "shipped"
    assert ds.latest(source="queue", ref_id=jid)["state"] == "live"


def test_deployment_ignores_owner_overlap_by_merging_in_isolated_worktree(
        tmp_path, monkeypatch):
    from server import deployment
    from server.db import deployment_store as ds, night_queue_store as q

    repo = tmp_path / "repo"
    _deployment_repo(repo)
    # Both owner and deployment touch the same path.
    subprocess.run(["git", "checkout", "night/test"], cwd=repo, check=True,
                   capture_output=True)
    (repo / "owner.txt").write_text("agent version\n")
    subprocess.run(["git", "commit", "-am", "overlap"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True,
                   capture_output=True)
    (repo / "owner.txt").write_text("owner version\n")
    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(deployment, "_DEPLOY_WORKTREE_DIR", tmp_path / "deploy-worktrees")
    jid = q.add(project=str(repo), task="overlap", spec=_ready_work_order())
    q.update(jid, status="staged")
    owner_head = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=repo, check=True,
        capture_output=True, text=True).stdout
    owner_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        capture_output=True, text=True).stdout

    result = deployment.deploy_branch(
        repo=str(repo), branch="night/test", base="main",
        changed_files=["owner.txt"], source="queue", ref_id=jid,
        server_touched=False)

    assert "is live" in result
    assert (repo / "owner.txt").read_text() == "owner version\n"
    assert subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=repo, check=True,
        capture_output=True, text=True).stdout == owner_head
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True,
        capture_output=True, text=True).stdout == owner_status
    live = ds.latest(source="queue", ref_id=jid)
    deployed = subprocess.run(
        ["git", "show", f"{live['deployed_sha']}:owner.txt"], cwd=repo, check=True,
        capture_output=True, text=True).stdout
    assert deployed == "agent version\n"
    assert q.get(jid)["status"] == "shipped"
    assert ds.latest(source="queue", ref_id=jid)["state"] == "live"


def test_deployment_ledger_serializes_active_releases(tmp_path, monkeypatch):
    from server.db import deployment_store as ds

    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    first, error = ds.begin(repo="/repo", branch="one", base="main",
                            source="queue", ref_id=1, server_touched=True,
                            changed_files=["server/main.py"])
    second, busy = ds.begin(repo="/repo", branch="two", base="main",
                            source="queue", ref_id=2, server_touched=True,
                            changed_files=["server/api_v2.py"])
    assert error is None and first["state"] == "merging"
    assert second is None and f"deployment #{first['id']}" in busy


def test_orphaned_running_jobs_are_failed_but_active_workers_are_kept(
        tmp_path, monkeypatch):
    import time
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    orphan = q.add(project="/repo", task="orphan", spec=_ready_work_order())
    active = q.add(project="/repo", task="active", spec=_ready_work_order())
    old = time.time() - 3600
    q.update(orphan, status="running", started_at=old, engine_used="codex")
    q.update(active, status="running", started_at=old, engine_used="claude")

    recovered = q.fail_orphaned_running({active}, grace_seconds=60)

    assert [job["id"] for job in recovered] == [orphan]
    assert q.get(orphan)["status"] == "failed"
    assert "Worker disappeared" in q.get(orphan)["summary"]
    assert q.get(active)["status"] == "running"


def test_queue_supervisor_retries_transient_failure_on_another_engine(
        tmp_path, monkeypatch):
    from server import queue_supervisor as supervisor
    from server.db import deployment_store as ds, night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    monkeypatch.setattr(supervisor, "_configured_engines",
                        lambda: ["claude", "codex", "gemini"])
    jid = q.add(project=str(tmp_path), task="recover me",
                spec=_ready_work_order(), tag="auto")
    q.update(jid, status="failed", attempt_count=1,
             attempted_engines=["claude"], engine_used="claude",
             summary="Worker disappeared before reporting a result")

    first = asyncio.run(supervisor.supervise_once(now=1000))
    assert f"#{jid} scheduled retry" in first
    waiting = q.get(jid)
    assert waiting["status"] == "failed" and waiting["next_retry_at"] == 1060
    assert "Codex" in waiting["next_action"]

    second = asyncio.run(supervisor.supervise_once(now=1061))
    assert f"#{jid} requeued on codex" in second
    recovered = q.get(jid)
    assert recovered["status"] == "queued" and recovered["engine"] == "codex"


def test_queue_supervisor_escalates_only_after_retry_budget(tmp_path, monkeypatch):
    from server import queue_supervisor as supervisor
    from server.db import deployment_store as ds, night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    async def no_notify(*args, **kwargs):
        return None
    monkeypatch.setattr(supervisor, "_notify_exhausted", no_notify)
    jid = q.add(project=str(tmp_path), task="exhausted",
                spec=_ready_work_order(), tag="auto")
    q.update(jid, status="failed", attempt_count=3, max_attempts=3,
             summary="run error: repeated CLI failure")

    asyncio.run(supervisor.supervise_once(now=2000))
    job = q.get(jid)
    assert job["status"] == "needs_you"
    assert "3/3 attempts" in job["blocker_reason"]
    assert supervisor.health_snapshot()["needs_attention"] == 1


def test_queue_explanation_says_why_and_what_happens_next(tmp_path, monkeypatch):
    from server import queue_supervisor as supervisor
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    foundation = q.add(project=str(tmp_path), task="foundation",
                       spec=_ready_work_order(), tag="auto")
    dependent = q.add(project=str(tmp_path), task="dependent",
                      spec=_ready_work_order(), tag="auto",
                      depends_on=[foundation])
    explanation = supervisor.job_explanation(q.get(dependent))
    assert f"#{foundation}" in explanation["blocker"]
    assert "supervisor" in explanation["next_action"].lower()


def test_capability_awareness_closes_exact_active_duplicate(tmp_path, monkeypatch):
    from server import capability_registry
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(capability_registry, "refresh", lambda **kwargs: 0)
    owner = q.add(
        project=str(tmp_path), task="Capability registry",
        spec=_ready_work_order("Queue capability registry"), tag="auto")
    duplicate = q.add(
        project=str(tmp_path), task="Capability registry",
        spec=_ready_work_order("Queue capability registry"), tag="auto")

    result = capability_registry.assess(duplicate)

    assert result["classification"] == "duplicate"
    assert result["match"]["id"] == owner
    closed = q.get(duplicate)
    assert closed["status"] == "closed"
    assert "queue task" in closed["close_reason"].lower()
    assert closed["closure_history"]  # recoverable, never deleted


def test_capability_awareness_recognizes_extensions_and_active_overlap(
        tmp_path, monkeypatch):
    from server import capability_registry
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(capability_registry, "refresh", lambda **kwargs: 0)
    base = q.add(
        project=str(tmp_path), task="Queue supervisor capability registry",
        spec=_ready_work_order("Queue supervisor capability registry"), tag="auto")
    overlap = q.add(
        project=str(tmp_path), task="Queue supervisor capability registry mobile display",
        spec=_ready_work_order(
            "Queue supervisor capability registry mobile display"), tag="auto")

    overlap_result = capability_registry.assess(overlap)
    waiting = q.get(overlap)
    assert overlap_result["classification"] == "conflict"
    assert waiting["status"] == "queued" and waiting["tag"] == "auto"
    assert waiting["depends_on"] == [base]

    q.update(base, status="shipped")
    extension = q.add(
        project=str(tmp_path), task="Improve queue supervisor capability registry",
        spec=_ready_work_order(
            "Improve queue supervisor capability registry"), tag="auto")
    extension_result = capability_registry.assess(extension)
    assert extension_result["classification"] == "extension"
    assert q.get(extension)["status"] == "queued"
    assert "extension" in q.get(extension)["next_action"].lower()


def test_test_name_is_context_not_proof_of_existing_capability(
        tmp_path, monkeypatch):
    from server import capability_registry
    from server.db import capability_store, night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(capability_registry, "refresh", lambda **kwargs: 0)
    capability_store.replace_source("test", [{
        "key": "test:test_phone_export", "title": "Phone export",
        "description": "Phone export", "status": "evidence",
        "evidence": ["test_phone_export"],
    }])
    jid = q.add(
        project=str(tmp_path), task="Phone export",
        spec=_ready_work_order("Phone export"), tag="auto")

    result = capability_registry.assess(jid)

    assert result["classification"] == "new"
    assert q.get(jid)["status"] == "queued"


def test_capability_awareness_never_crosses_project_boundaries(
        tmp_path, monkeypatch):
    from server import capability_registry
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(capability_registry, "refresh", lambda **kwargs: 0)
    q.add(
        project=str(tmp_path / "repo-a"), task="Add customer export",
        spec=_ready_work_order("Add customer export"), tag="auto")
    other_repo = q.add(
        project=str(tmp_path / "repo-b"), task="Add customer export",
        spec=_ready_work_order("Add customer export"), tag="auto")

    result = capability_registry.assess(other_repo)

    assert result["classification"] == "new"
    assert q.get(other_repo)["status"] == "queued"


def test_capability_registry_maps_deploy_worktree_to_owner_repo(
        tmp_path, monkeypatch):
    from types import SimpleNamespace
    from server import capability_registry, config

    owner = tmp_path / "owner"
    runtime = tmp_path / "deploy-12"
    monkeypatch.setattr(config, "REPO_DIR", runtime)
    monkeypatch.setattr(
        capability_registry.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=str(owner / ".git") + "\n"),
    )

    assert capability_registry._repo_project() == str(owner.resolve())


def test_capability_registry_rejects_false_live_deployment_evidence(
        tmp_path, monkeypatch):
    from server import capability_registry
    from server.db import deployment_store as ds, night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "queue.db")
    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    coding = q.add(
        project=str(tmp_path), task="Policy system",
        spec=_ready_work_order("Policy system"), tag="auto")
    q.update(coding, status="shipped")
    deployment, _ = ds.begin(
        repo=str(tmp_path), branch="night/policy", base="main", source="queue",
        ref_id=coding, server_touched=False, changed_files=["server/policy.py"])
    ds.update(deployment["id"], state="live", deployed_sha="not-in-current-head")
    monkeypatch.setattr(capability_registry, "_git_head", lambda: "current-head")
    monkeypatch.setattr(capability_registry, "_is_ancestor", lambda commit, head: False)
    monkeypatch.setattr(capability_registry, "_repo_project", lambda: str(tmp_path))
    monkeypatch.setattr(capability_registry, "_last_refresh", 0.0)

    capability_registry.refresh(force=True)

    from server.db import capability_store
    assert not [item for item in capability_store.list_all() if item["key"] == f"job:{coding}"]


def test_deployment_guard_never_rolls_back_an_unexpected_head(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from server import deployment_guard as guard
    from server.db import deployment_store as ds

    monkeypatch.setattr(ds, "DB_PATH", tmp_path / "deployments.db")
    item, _ = ds.begin(repo=str(tmp_path), branch="night/x", base="main",
                       source="queue", ref_id=None, server_touched=True,
                       changed_files=["server/main.py"])
    ds.update(item["id"], before_sha="good", deployed_sha="expected")
    calls = []

    def fake_run(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="someone-else\n", stderr="")

    monkeypatch.setattr(guard, "_restart", lambda *args: (True, ""))
    monkeypatch.setattr(guard, "_healthy", lambda *a, **k: (False, "bad deploy"))
    monkeypatch.setattr(guard, "_run", fake_run)
    monkeypatch.setattr(guard, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["deployment_guard", str(item["id"])])

    guard.main()

    assert not any("reset" in call for call in calls)
    assert ds.get(item["id"])["state"] == "failed"


def test_night_shift_window_wraps_past_midnight(monkeypatch):
    import datetime as dt
    from server import config, night_shift

    monkeypatch.setattr(config, "NIGHT_START", "23:00")
    monkeypatch.setattr(config, "NIGHT_END", "07:00")

    def at(h, m):
        return dt.datetime(2026, 8, 7, h, m)

    assert night_shift._in_window(at(2, 0)) is True
    assert night_shift._in_window(at(23, 30)) is True
    assert night_shift._in_window(at(12, 0)) is False


def test_night_shift_benches_engine_over_quota(monkeypatch):
    from server import config, night_shift

    monkeypatch.setattr(config, "NIGHT_ENGINES", "claude,codex,gemini")
    monkeypatch.setattr(config, "NIGHT_QUOTA_STOP_PCT", 85)
    night_shift._workers.clear()

    avail = night_shift._available_engines({"codex": 95.0, "claude": 10.0, "gemini": 50.0})
    assert "codex" not in avail
    assert set(avail) == {"claude", "gemini"}


def test_queue_add_parses_tag_engine_and_project(monkeypatch):
    from server import config
    from server.skills import queue

    monkeypatch.setattr(config, "WORKSPACE_DIR", __import__("pathlib").Path("/tmp/active"))
    monkeypatch.setattr(queue, "_resolve_project", lambda name: f"/Projects/{name}")

    project, task, tag, engine = queue._parse_add("mine codex demo: add a CSV export")
    assert (tag, engine, project) == ("mine", "codex", "/Projects/demo")
    assert task == "add a CSV export"

    # Bare task → defaults (auto/auto) in the active workspace.
    project, task, tag, engine = queue._parse_add("just do the thing")
    assert (tag, engine, task) == ("auto", "auto", "just do the thing")
    assert project == "/tmp/active"


def test_shell_infers_reply_when_action_missing(monkeypatch):
    # A router (esp. local Qwen) that returns {"reply": …} with no "action" must
    # answer, not dead-end with "unknown agent action: None".
    from server.skills import shell

    async def fake_haiku(*args, **kwargs):
        return '{"reply":"here is your answer"}'

    monkeypatch.setattr(shell, "_haiku", fake_haiku)
    result = asyncio.run(shell.ShellSkill().run("something"))
    assert result == "here is your answer"
    assert "unknown agent action" not in result


def test_shell_infers_call_when_action_missing(monkeypatch):
    # A bare {"tool": …} with no "action" should route as a call.
    from server.skills import registry, shell
    from server.skills.base import Skill

    class Probe2(Skill):
        name = "probe2"
        description = "p"
        final_output = True

        async def run(self, prompt="", **kwargs):
            return f"ran:{prompt}"

    async def fake_haiku(*args, **kwargs):
        return '{"tool":"probe2","args":"go","final":true}'

    monkeypatch.setitem(registry, "probe2", Probe2())
    monkeypatch.setattr(shell, "_haiku", fake_haiku)
    result = asyncio.run(shell.ShellSkill().run("do it"))
    assert result == "ran:go"


def test_shell_offschema_json_fails_validation_to_escalate():
    # Qwen sometimes emits valid-but-off-schema JSON (a project blob). That must
    # FAIL validation so the provider chain escalates to Claude instead of the
    # blob reaching the user. Proper decisions must pass.
    from server.skills.shell import _is_usable_decision

    project_blob = ('{"project_name":"x","description":"y",'
                    '"steps_to_handle_first":["a","b"]}')
    assert _is_usable_decision(project_blob) is False
    assert _is_usable_decision('{}') is False
    assert _is_usable_decision('not json') is False
    assert _is_usable_decision('{"action":"done","reply":"hi"}') is True
    assert _is_usable_decision('{"tool":"codex","args":"save it"}') is True
    assert _is_usable_decision('{"reply":"hi"}') is True


def test_shell_salvages_reply_on_unrecognized_decision(monkeypatch):
    # No action, no tool, no reply key — salvage text instead of a cryptic error.
    from server.skills import shell

    async def fake_haiku(*args, **kwargs):
        return '{"thoughts":"just chatting","message":"hi"}'

    monkeypatch.setattr(shell, "_haiku", fake_haiku)
    result = asyncio.run(shell.ShellSkill().run("hey"))
    assert "unknown agent action" not in result


def test_notifications_store_roundtrip(tmp_path, monkeypatch):
    from server.db import notifications_store as n

    monkeypatch.setattr(n, "DB_PATH", tmp_path / "notifications.db")

    nid = n.add(type="queue_input", title="Job #3 needs input",
                body="which db?", needs_response=True, ref_kind="queue_job", ref_id=3)
    assert n.unread_count() == 1
    assert n.get(nid)["data"] == {}          # empty data decodes to {}

    n.mark_read(nid)
    assert n.unread_count() == 0

    n.set_response(nid, "sqlite")
    row = n.get(nid)
    assert row["status"] == "answered" and row["response"] == "sqlite"

    assert n.dismiss(nid) is True
    assert n.get(nid)["status"] == "dismissed"


def test_respond_to_job_appends_answer_and_requeues(tmp_path, monkeypatch):
    from server import night_shift
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    jid = q.add(project="/tmp/p", task="add export", tag="auto", engine="codex",
                spec=_ready_work_order("Add export"))
    q.update(jid, status="awaiting_input")

    job = night_shift.respond_to_job(jid, "use SQLite")
    assert job["status"] == "queued"
    assert "Owner's answer to your question: use SQLite" in job["task"]
    # An unknown job is a clean None, not a crash.
    assert night_shift.respond_to_job(9999, "x") is None


def test_stop_job_is_safe_when_not_running(tmp_path, monkeypatch):
    from server import night_shift
    from server.db import night_queue_store as q

    monkeypatch.setattr(q, "DB_PATH", tmp_path / "night_queue.db")
    assert night_shift.stop_job(123) is False          # unknown job
    jid = q.add(project="/tmp/p", task="t", tag="auto",
                spec=_ready_work_order())
    assert night_shift.stop_job(jid) is True           # queued → parked stopped
    assert q.get(jid)["status"] == "stopped"


def test_night_settings_overlay_precedence(tmp_path, monkeypatch):
    from server import config, prefs

    monkeypatch.setattr(prefs, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(config, "NIGHT_SHIFT_ENABLED", False)
    monkeypatch.setattr(config, "NIGHT_MAX_JOBS", 12)

    # Defaults come from config until overridden.
    assert prefs.night_settings()["enabled"] is False
    assert prefs.night_settings()["max_jobs"] == 12

    prefs.set_night_settings(enabled=True, max_jobs=3)
    assert prefs.night_settings()["enabled"] is True
    assert prefs.night_settings()["max_jobs"] == 3
    # Unset keys still fall through to config.
    assert prefs.night_settings()["start"] == config.NIGHT_START


def test_android_widget_layouts_only_use_remoteviews_safe_elements():
    """Launchers reject an entire widget when any layout tag is unsupported."""
    from pathlib import Path
    import xml.etree.ElementTree as ET

    safe = {"LinearLayout", "TextView", "ImageView", "ProgressBar", "include"}
    layouts = (Path(__file__).parents[2] / "clients/gajala/android/app/src/main/res/layout")

    for layout in layouts.glob("widget*.xml"):
        tags = {element.tag.rsplit("}", 1)[-1] for element in ET.parse(layout).iter()}
        assert tags <= safe, f"{layout.name} has unsafe RemoteViews tags: {tags - safe}"


def test_codaur_usage_refresh_is_coalesced(monkeypatch):
    from types import SimpleNamespace
    from server import api_v2

    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout='{"reports":[]}', stderr="")

    monkeypatch.setattr(api_v2, "_CODAUR_CACHE", None)
    monkeypatch.setattr(api_v2.subprocess, "run", fake_run)

    assert api_v2._codaur_report() == {"reports": []}
    assert api_v2._codaur_report() == {"reports": []}
    assert calls == 1


def test_run_stream_sends_a_frame_immediately(monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from server import config, main

    async def fake_route(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        return "finished"

    monkeypatch.setattr(main.orchestrator, "route", fake_route)
    client = TestClient(main.app)
    response = client.post(
        "/run/stream",
        headers={"X-API-Token": config.API_TOKEN},
        json={"command": "shell", "prompt": "hello", "session_id": "test"},
    )
    frames = [json.loads(line) for line in response.text.splitlines()]

    assert frames[0] == {"type": "step", "label": "Thinking…"}
    assert frames[-1]["type"] == "final"
