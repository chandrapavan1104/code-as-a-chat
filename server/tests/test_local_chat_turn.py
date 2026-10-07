"""Client-handled voice turns are durable without pretending an agent ran."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from server import config, main
from server.db import agent_runs_store, store


@pytest.fixture
def client():
    return TestClient(main.app), {"X-API-Token": config.API_TOKEN}


def _body(**updates):
    body = {
        "session_id": "app:phone::general",
        "request_id": "voice-turn-1",
        "messages": [
            {"role": "user", "content": "Play Teardrop on YouTube"},
            {"role": "assistant", "content": "Opening YouTube."},
        ],
    }
    body.update(updates)
    return body


def test_local_turn_is_authenticated_and_visible_in_history(client):
    http, headers = client
    assert http.post("/api/chat/local-turn", json=_body()).status_code == 401

    before = time.time()
    response = http.post("/api/chat/local-turn", headers=headers, json=_body())
    after = time.time()

    assert response.status_code == 200
    assert response.json() == {"stored": True, "message_count": 2}
    turns = http.get(
        "/api/chat", headers=headers,
        params={"session_id": "app:phone::general"},
    ).json()["turns"]
    assert [(turn["role"], turn["content"]) for turn in turns] == [
        ("user", "Play Teardrop on YouTube"),
        ("assistant", "Opening YouTube."),
    ]
    assert all(before <= turn["ts"] <= after for turn in turns)
    assert all(turn["run_id"] is None for turn in turns)
    assert all(turn["local_request_id"] == "voice-turn-1" for turn in turns)
    agent_runs_store.init()
    assert agent_runs_store.list_runs(session_id="app:phone::general") == []


def test_identical_request_retry_does_not_duplicate_messages(client):
    http, headers = client
    first = http.post("/api/chat/local-turn", headers=headers, json=_body())
    retry = http.post("/api/chat/local-turn", headers=headers, json=_body())

    assert first.json()["stored"] is True
    assert retry.json() == {"stored": False, "message_count": 2}
    assert store.count("app:phone::general") == 2
    with store._conn() as connection:
        receipt = connection.execute(
            "SELECT request_id, session_id, message_count "
            "FROM local_turn_receipts"
        ).fetchall()
    assert receipt == [("voice-turn-1", "app:phone::general", 2)]


def test_request_id_reuse_with_different_payload_conflicts(client):
    http, headers = client
    assert http.post(
        "/api/chat/local-turn", headers=headers, json=_body()
    ).status_code == 200

    changed = _body(messages=[{"role": "user", "content": "Different"}])
    response = http.post("/api/chat/local-turn", headers=headers, json=changed)
    assert response.status_code == 409
    assert store.count("app:phone::general") == 2


def test_receipt_failure_rolls_back_messages():
    with store._conn() as connection:
        connection.execute(
            "CREATE TRIGGER reject_local_receipt BEFORE INSERT "
            "ON local_turn_receipts BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        store.append_local_turn(
            "app:phone", "will-fail",
            [{"role": "user", "content": "Do not leave half a turn"}],
        )
    assert store.count("app:phone") == 0


@pytest.mark.parametrize("body", [
    _body(session_id=""),
    _body(request_id=""),
    _body(messages=[]),
    _body(messages=[{"role": "tool", "content": "no"}]),
    _body(messages=[{"role": "user", "content": ""}]),
    _body(messages=[{"role": "user", "content": "x" * 40_001}]),
    _body(messages=[{"role": "user", "content": "x"}] * 33),
])
def test_local_turn_validation_rejects_invalid_payload(client, body):
    http, headers = client
    response = http.post("/api/chat/local-turn", headers=headers, json=body)
    assert response.status_code == 422
    assert store.count("app:phone::general") == 0
