"""Validated edit/cancel controls for existing reminders."""

from __future__ import annotations

import time
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.db import notes_store, reminders_store

router = APIRouter()


class ReminderEdit(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: float | None = Field(default=None, allow_inf_nan=False)
    recurrence: Literal["none", "daily"] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    until_note_id: int | None = None


@router.patch("/reminders/{reminder_id}")
def edit_reminder(reminder_id: int, body: ReminderEdit):
    current = reminders_store.get(reminder_id)
    if current is None or current.get("fired"):
        raise HTTPException(404, "active reminder not found")
    fields = body.model_dump(exclude_unset=True)
    for required in ("text", "due_at", "recurrence", "timezone"):
        if required in fields and fields[required] is None:
            raise HTTPException(422, f"{required} cannot be null")
    if "text" in fields:
        fields["text"] = " ".join((fields["text"] or "").split())
        if not fields["text"]:
            raise HTTPException(422, "reminder text cannot be empty")
    due_at = fields.get("due_at", current["due_at"])
    if due_at <= time.time():
        raise HTTPException(422, "reminder time must be in the future")
    timezone = fields.get("timezone", current["timezone"])
    try:
        ZoneInfo(timezone)
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, f"unknown timezone: {timezone}")
    note_id = fields.get("until_note_id", current["until_note_id"])
    if note_id is not None:
        notes_store._init()
        if notes_store.get(note_id) is None:
            raise HTTPException(422, "linked note does not exist")
    if "due_at" in fields:
        try:
            fields["due_at"] = float(fields["due_at"])
        except (TypeError, ValueError):
            raise HTTPException(422, "due_at must be a Unix timestamp")
    try:
        if not reminders_store.update_pending(reminder_id, **fields):
            raise HTTPException(404, "active reminder not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return reminders_store.get(reminder_id)


@router.post("/reminders/{reminder_id}/cancel", status_code=204)
def cancel_reminder(reminder_id: int):
    if not reminders_store.cancel(reminder_id):
        raise HTTPException(404, "active reminder not found")
