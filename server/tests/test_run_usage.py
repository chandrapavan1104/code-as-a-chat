import asyncio
import json

from server import run_usage, workspace
from server.db import agent_runs_store
from server.skills import shell
from server.skills.claude_code import ClaudeCodeSkill


def test_outside_a_turn_nothing_is_recorded():
    run_usage._calls.set(None)
    run_usage.add("router", "x", input_tokens=5)
    assert run_usage.summary() is None


def test_summary_totals_and_partial_cost():
    run_usage.start()
    run_usage.add("router", "claude-sonnet", input_tokens=1200, output_tokens=80, cost_usd=0.0042)
    run_usage.add("codex", "gpt-6-astra", input_tokens=50000)
    s = run_usage.summary()
    assert (s["input_tokens"], s["output_tokens"]) == (51200, 80)
    assert s["cost_usd"] == 0.0042 and s["cost_complete"] is False


def test_claude_cost_is_read_from_cli_output():
    out = json.dumps({"result": "ok", "total_cost_usd": 0.0123,
                      "usage": {"input_tokens": 10, "output_tokens": 5}})
    assert ClaudeCodeSkill().extract_cost(out) == 0.0123
    assert ClaudeCodeSkill().extract_cost("not json") is None


def test_turn_usage_is_stored_with_the_trace(monkeypatch, tmp_path):
    async def fake_haiku(*args, **kwargs):
        run_usage.add("router", "claude-sonnet-x", input_tokens=900,
                      output_tokens=40, cost_usd=0.001)
        return '{"action":"done","reply":"hi"}'
    monkeypatch.setattr(shell, "_haiku", fake_haiku)

    async def turn():
        with workspace.bound(tmp_path):
            return await shell.ShellSkill().run("hello", session_id="app:t")
    asyncio.run(turn())
    run = agent_runs_store.list_runs("app:t")[0]
    usage = agent_runs_store.get(run["id"])["usage"]
    assert usage["calls"][0]["model"] == "claude-sonnet-x"
    assert usage["cost_usd"] == 0.001 and usage["cost_complete"] is True
