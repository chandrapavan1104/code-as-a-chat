import asyncio

from server.db import store
from server.skills.memory import MemorySkill


def test_search_is_session_scoped_and_get_returns_exact_text():
    store.append_turn("app:one", "user", "The finalized LinkedIn draft\nKeep this wording.")
    store.append_turn("app:one", "assistant", "I saved the draft.")
    store.append_turn("app:other", "user", "The finalized LinkedIn draft for another project")
    store.append_turn("tg:42", "user", "The finalized LinkedIn draft")

    results = store.search("app:one", "LinkedIn")
    assert len(results) == 1
    assert results[0]["session_id"] == "app:one"
    assert results[0]["content"] == "The finalized LinkedIn draft\nKeep this wording."

    message = store.get_message(results[0]["id"], "app:one")
    assert message["content"] == "The finalized LinkedIn draft\nKeep this wording."
    assert store.get_message(results[0]["id"], "tg:42") is None


def test_explicit_client_search_and_pagination():
    for session in ("app:one", "app:two", "tg:42"):
        store.append_turn(session, "user", "shared decision text")
    matches = store.search("app:one", "shared decision", all_client=True, limit=1)
    assert len(matches) == 1
    assert matches[0]["session_id"].startswith("app:")
    page = store.search("app:one", "shared decision", all_client=True, limit=1, offset=1)
    assert len(page) == 1
    assert page[0]["id"] != matches[0]["id"]
    assert all(item["session_id"].startswith("app:") for item in matches + page)


def test_memory_skill_search_previews_and_get_is_exact():
    exact = "Original wording " + ("x" * 260)
    store.append_turn("app:one", "user", exact)
    skill = MemorySkill()

    result = asyncio.run(skill.run("search original wording", session_id="app:one"))
    assert "MEMORY MATCHES" in result
    assert "Use memory get" in result
    assert len(exact) > 200
    assert exact not in result

    item = store.search("app:one", "Original wording")[0]
    result = asyncio.run(skill.run(f"get {item['id']}", session_id="app:one"))
    assert exact in result


def test_memory_skill_explicit_cross_project_search_does_not_cross_clients():
    store.append_turn("app:one", "user", "dashboard final copy")
    store.append_turn("app:two", "user", "dashboard final copy")
    store.append_turn("tg:42", "user", "dashboard final copy")
    result = asyncio.run(MemorySkill().run(
        "search all dashboard final copy", session_id="app:one"))
    assert "app:one" in result or "app:two" in result
    assert "tg:42" not in result
