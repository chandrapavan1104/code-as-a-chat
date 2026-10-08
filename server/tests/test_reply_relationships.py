import pytest
from fastapi import HTTPException

from server import main
from server.db import store


@pytest.fixture
def reply_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "conversation.db")
    store._init()


def test_reply_context_links_new_user_and_assistant_to_exact_source(reply_db):
    source_id = store.append_turn("app:install-a::project", "assistant", "Old report")
    with store.turn_context("app:install-a::project", source_id):
        user_id = store.append_turn("app:install-a::project", "user", "Compare these")
        # shell._remember still appends the user row; context makes it idempotent.
        assert store.append_turn("app:install-a::project", "user", "Compare these") == user_id
        assistant_id = store.append_turn("app:install-a::project", "assistant", "Here is the comparison")
        assert store.current_turn_ids() == {
            "user_message_id": user_id,
            "assistant_message_id": assistant_id,
        }

    rows = store.get_recent("app:install-a::project", n=3)
    assert rows[-2]["id"] == user_id
    assert rows[-2]["reply_to_message_id"] == source_id
    assert rows[-1]["reply_to_message_id"] == user_id


def test_reply_context_includes_full_old_source_and_ancestor_chain(reply_db):
    with store.turn_context("app:install-a::project"):
        old_user = store.append_turn("app:install-a::project", "user", "Original question")
        old_assistant = store.append_turn("app:install-a::project", "assistant", "Original answer")
    body = main.RunRequest(command="shell", prompt="Elaborate", session_id="app:install-a::project",
                           reply_to_message_id=old_assistant)
    context = main._validated_reply_context(body)
    assert context == {
        "message_id": old_assistant,
        "role": "assistant",
        "content": "Original answer",
        "ancestors": [{"id": old_user, "role": "user", "content": "Original question"}],
    }


def test_reply_rejects_message_from_another_session(reply_db):
    foreign_id = store.append_turn("app:install-b::project", "assistant", "Private other chat")
    body = main.RunRequest(command="shell", prompt="Reply", session_id="app:install-a::project",
                           reply_to_message_id=foreign_id)
    with pytest.raises(HTTPException) as exc:
        main._validated_reply_context(body)
    assert exc.value.status_code == 404
    assert store.count("app:install-a::project") == 0


def test_chat_history_returns_source_preview(reply_db):
    source_id = store.append_turn("app:install-a::project", "user", "The exact source")
    with store.turn_context("app:install-a::project", source_id):
        store.append_turn("app:install-a::project", "user", "Follow up")
        store.append_turn("app:install-a::project", "assistant", "Answer")
    from server import api_v2
    response = api_v2.chat_history("app:install-a::project", limit=5)["turns"]
    assert response[-2]["reply_to_message_id"] == source_id
    assert response[-2]["reply_to_content"] == "The exact source"
    assert response[-2]["reply_to_role"] == "user"


def test_request_id_cannot_be_retargeted_to_another_message(reply_db):
    first = store.append_turn("app:install-a::project", "user", "First source")
    second = store.append_turn("app:install-a::project", "user", "Second source")
    assert store.bind_request_reply("stable-id", "app:install-a::project", first)
    assert not store.bind_request_reply("stable-id", "app:install-a::project", second)


def test_client_search_escapes_install_id_wildcards(reply_db):
    store.append_turn("app:owner_x::one", "user", "shared phrase")
    store.append_turn("app:ownerX::one", "user", "shared phrase")
    rows = store.search("app:owner_x::one", "shared phrase", all_client=True)
    assert [row["session_id"] for row in rows] == ["app:owner_x::one"]
