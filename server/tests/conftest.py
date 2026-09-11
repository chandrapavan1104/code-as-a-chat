"""Shared test setup.

The agent loop records every turn it runs, so any test that drives ShellSkill
writes into the owner's real ~/.codeasachat/agent_runs.db unless the store is
redirected. Do it once, for the whole suite, rather than per test file — the
next person to write an agent test should not have to remember.
"""

import pytest


@pytest.fixture(autouse=True)
def isolate_agent_run_traces(tmp_path, monkeypatch):
    from server.db import (agent_runs_store, assistant_tasks_store,
                           capability_store, store)
    monkeypatch.setattr(agent_runs_store, "DB_PATH", tmp_path / "agent_runs.db")
    monkeypatch.setattr(assistant_tasks_store, "DB_PATH", tmp_path / "assistant_tasks.db")
    monkeypatch.setattr(capability_store, "DB_PATH", tmp_path / "capabilities.db")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "conversations.db")
    store._init()
