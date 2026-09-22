"""Shared test setup.

The agent loop records every turn it runs, so any test that drives ShellSkill
writes into the owner's real ~/.codeasachat/agent_runs.db unless the store is
redirected. Do it once, for the whole suite, rather than per test file — the
next person to write an agent test should not have to remember.
"""

import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_agent_run_traces(tmp_path, monkeypatch):
    from server import config
    from server import brain_health
    monkeypatch.setattr(brain_health, "_failures", {})
    monkeypatch.setattr(config, "JEV_ENABLED", False)
    from server.db import (agent_runs_store, assistant_tasks_store,
                           capability_store, night_queue_store, store)
    monkeypatch.setattr(agent_runs_store, "DB_PATH", tmp_path / "agent_runs.db")
    monkeypatch.setattr(assistant_tasks_store, "DB_PATH", tmp_path / "assistant_tasks.db")
    monkeypatch.setattr(capability_store, "DB_PATH", tmp_path / "capabilities.db")
    monkeypatch.setattr(night_queue_store, "DB_PATH", tmp_path / "night_queue.db")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "conversations.db")
    store._init()

    # reminders_store initializes itself at import time. Import it against a
    # temporary home when collection has not loaded it yet, then redirect it
    # for the test. This keeps refiner/API imports from touching the owner's DB.
    import sys
    if "server.db.reminders_store" not in sys.modules:
        original_home = Path.home
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        from server.db import reminders_store
        monkeypatch.setattr(Path, "home", original_home)
    else:
        from server.db import reminders_store
    monkeypatch.setattr(reminders_store, "DB_PATH", tmp_path / "reminders.db")
    reminders_store._init()
