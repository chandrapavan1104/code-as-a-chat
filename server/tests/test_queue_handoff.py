import json


def test_research_outcome_requires_a_sourced_report():
    from server.night_exec import parse_research_outcome

    assert parse_research_outcome("just some notes").status == "unverified"
    assert parse_research_outcome(
        json.dumps({"status": "waiting_input", "question": "Which market?"})
    ).status == "waiting_input"
    assert parse_research_outcome(
        json.dumps({"status": "report_complete", "report": "missing image"})
    ).status == "blocked"
    assert parse_research_outcome(
        json.dumps({"status": "report_complete", "report": "Finding https://example.com"})
    ).status == "report_complete"


def test_queue_capture_keeps_only_validated_attachment_refs(tmp_path, monkeypatch):
    from server.db import night_queue_store as store
    from server.skills import queue

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "queue.db")
    good = tmp_path / "served.png"
    good.write_bytes(b"image")
    monkeypatch.setattr(queue, "_resolve_project", lambda name: str(tmp_path))
    monkeypatch.setattr("server.media.is_served_path", lambda path: path == good)

    jid = store.add(
        project=str(tmp_path), task="inspect the screenshot", tag="auto",
        spec={"attachment_refs": [str(good)]},
    )
    job = store.get(jid)
    assert job["spec_json"]["attachment_refs"] == [str(good)]


def test_attachment_capture_uses_current_source_only(tmp_path, monkeypatch):
    from server.skills.queue import _attachment_refs

    good = tmp_path / "current.png"
    old = tmp_path / "old.png"
    good.write_bytes(b"current")
    old.write_bytes(b"old")
    monkeypatch.setattr(
        "server.media.is_served_path", lambda path: path in {good, old})
    current = f"[User sent an image, saved at: {good}]\nqueue this"
    prior = f"[User sent an image, saved at: {old}]"
    assert _attachment_refs("queue this", "app:one", current) == [str(good)]
    assert _attachment_refs("queue this", "app:one", prior) == [str(old)]
