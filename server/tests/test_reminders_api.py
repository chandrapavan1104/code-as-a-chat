import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


def _stores(tmp_path, monkeypatch):
    from server.db import notes_store, reminders_store
    monkeypatch.setattr(reminders_store, "DB_PATH", tmp_path / "reminders.db")
    monkeypatch.setattr(notes_store, "DB_PATH", tmp_path / "notes.db")
    notes_store._init()
    return reminders_store, notes_store


def test_reminder_edit_preserves_repeat_zone_and_note_link(tmp_path, monkeypatch):
    reminders, notes = _stores(tmp_path, monkeypatch)
    note_id = notes.add(None, "todo", "Ship build", "Verify the release")
    due = time.time() + 3600
    reminder_id = reminders.add("Check release", due, recurrence="daily",
                                timezone="America/Los_Angeles", until_note_id=note_id)
    from server.reminders_api import ReminderEdit, edit_reminder

    edited = edit_reminder(reminder_id, ReminderEdit(
        text="  Verify   release ", due_at=due + 600))

    assert edited["text"] == "Verify release"
    assert edited["due_at"] == due + 600
    assert edited["recurrence"] == "daily"
    assert edited["timezone"] == "America/Los_Angeles"
    assert edited["until_note_id"] == note_id


@pytest.mark.parametrize("fields", [
    {"due_at": 1},
    {"timezone": "Mars/Olympus"},
    {"recurrence": "weekly"},
    {"text": "   "},
    {"until_note_id": 9999},
])
def test_reminder_edit_rejects_invalid_fields_without_mutation(tmp_path, monkeypatch, fields):
    reminders, _ = _stores(tmp_path, monkeypatch)
    due = time.time() + 3600
    reminder_id = reminders.add("Check", due, recurrence="daily", timezone="UTC")
    before = reminders.get(reminder_id)
    from server.reminders_api import ReminderEdit, edit_reminder
    with pytest.raises((HTTPException, ValidationError)):
        edit_reminder(reminder_id, ReminderEdit(**fields))
    assert reminders.get(reminder_id) == before


def test_cancel_stops_active_reminder_without_notification(tmp_path, monkeypatch):
    reminders, _ = _stores(tmp_path, monkeypatch)
    reminder_id = reminders.add("Check later", time.time() + 3600,
                                recurrence="daily", timezone="UTC")
    from server.reminders_api import cancel_reminder
    assert cancel_reminder(reminder_id) is None
    assert reminders.get(reminder_id)["fired"] == 1
    assert reminders.list_pending() == []
    with pytest.raises(HTTPException) as error:
        cancel_reminder(reminder_id)
    assert error.value.status_code == 404
