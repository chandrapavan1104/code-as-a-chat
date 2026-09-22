import asyncio
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

from server.db import reminders_store as store


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "reminders.db")
    store._init()


def test_daily_advance_preserves_local_wall_time_across_dst(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    zone = "America/Los_Angeles"
    due = dt.datetime(2026, 3, 7, 9, tzinfo=dt.timezone(dt.timedelta(hours=-8))).timestamp()
    rid = store.add("deploy", due, recurrence="daily", timezone=zone)

    assert store.complete_delivery(rid, now=due)
    row = store.list_pending()[0]
    local = dt.datetime.fromtimestamp(row["due_at"], dt.timezone.utc).astimezone(ZoneInfo(zone))
    assert (local.hour, local.minute) == (9, 0)
    assert local.date() == dt.date(2026, 3, 8)


def test_duplicate_normalized_schedule_returns_existing_id(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    due = 2_000_000_000.0
    first = store.add("  Check   deploy ", due, chat_id=4, recurrence="daily", timezone="UTC")
    second = store.add("check deploy", due, chat_id=4, recurrence="daily", timezone="UTC")
    assert second == first
    assert len(store.list_pending()) == 1
    empty_tz_id = store.add("other", due, recurrence="daily", timezone="")
    empty_row = next(row for row in store.list_pending() if row["id"] == empty_tz_id)
    assert empty_row["timezone"] == "UTC"


def test_daily_completion_skips_missed_occurrences(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    due = 2_000_000_000.0
    rid = store.add("check", due, recurrence="daily", timezone="UTC")
    store.complete_delivery(rid, now=due + 4 * 86400)
    assert store.list_pending()[0]["due_at"] > due + 4 * 86400


def test_scheduler_records_app_alert_once_when_telegram_fails(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    rid = store.add("check", 1.0, chat_id=4)
    from server import scheduler

    calls = {"app": 0}
    async def telegram(*_args, **_kwargs):
        return False
    async def app(*_args, **_kwargs):
        calls["app"] += 1
        return 11
    monkeypatch.setattr(scheduler.notify, "push_text", telegram)
    monkeypatch.setattr("server.notifier.notify_app", app)
    asyncio.run(scheduler._check_reminders())
    asyncio.run(scheduler._check_reminders())
    assert calls["app"] == 1
    assert store.list_pending()[0]["fired"] == 0


def test_scheduler_daily_delivery_advances_across_dst(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    from server import scheduler
    due = dt.datetime(2026, 3, 8, 9, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
    rid = store.add("daily check", due, recurrence="daily", timezone="America/Los_Angeles")
    async def telegram(*_args, **_kwargs):
        return True
    async def app(*_args, **_kwargs):
        return 1
    monkeypatch.setattr(scheduler.notify, "push_text", telegram)
    monkeypatch.setattr("server.notifier.notify_app", app)
    monkeypatch.setattr(scheduler.reminders_store, "due_now", lambda: [store.list_pending()[0]])
    asyncio.run(scheduler._check_reminders())
    row = store.list_pending()[0]
    local = dt.datetime.fromtimestamp(row["due_at"], dt.timezone.utc).astimezone(ZoneInfo("America/Los_Angeles"))
    assert row["id"] == rid
    assert (local.hour, local.minute) == (9, 0)
    assert local.date() > dt.date.today()


def test_scheduler_stops_reminder_for_completed_note(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    from server import scheduler
    from server.db import notes_store
    monkeypatch.setattr(notes_store, "DB_PATH", tmp_path / "notes.db")
    notes_store._init()
    note_id = notes_store.add(None, "todo", "Deploy", "Deploy it")
    notes_store.set_status(note_id, "done")
    rid = store.add("deploy", 1.0, until_note_id=note_id)
    calls = {"telegram": 0}
    async def telegram(*_args, **_kwargs):
        calls["telegram"] += 1
        return True
    monkeypatch.setattr(scheduler.notify, "push_text", telegram)
    asyncio.run(scheduler._check_reminders())
    assert calls["telegram"] == 0
    assert not store.list_pending()
    assert store.delete(rid)


def test_scheduler_exception_does_not_repeat_app_alert(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    from server import scheduler
    rid = store.add("check", 1.0)
    calls = {"app": 0}
    async def telegram(*_args, **_kwargs):
        return False
    async def app(*_args, **_kwargs):
        calls["app"] += 1
        raise RuntimeError("transport failed")
    monkeypatch.setattr(scheduler.notify, "push_text", telegram)
    monkeypatch.setattr("server.notifier.notify_app", app)
    asyncio.run(scheduler._check_reminders())
    asyncio.run(scheduler._check_reminders())
    assert calls["app"] == 1
    assert store.list_pending()[0]["id"] == rid


def test_skill_rejects_null_past_and_weekday_mismatch_without_storage(monkeypatch, tmp_path):
    _store(tmp_path, monkeypatch)
    from server.skills import reminders
    monkeypatch.setattr(reminders, "_local_timezone", lambda: "UTC")
    monkeypatch.setattr(reminders, "_projects_for_prompt", lambda: "(none)")
    async def fake_haiku(*_args, **_kwargs):
        return json.dumps({"text": "check", "due_at": None, "timezone": "UTC"})
    monkeypatch.setattr(reminders, "_haiku", fake_haiku)
    result = asyncio.run(reminders._create("remind me sometime", None))
    assert result.status == "failed"
    assert not store.list_pending()

    async def past(*_args, **_kwargs):
        return json.dumps({"text": "check", "due_at": "2000-01-01 09:00", "timezone": "UTC"})
    monkeypatch.setattr(reminders, "_haiku", past)
    assert asyncio.run(reminders._create("remind me yesterday", None)).status == "failed"
    assert not store.list_pending()

    async def mismatch(*_args, **_kwargs):
        future = dt.datetime(2030, 1, 2, 9, tzinfo=dt.timezone.utc)  # Wednesday
        return json.dumps({"text": "check", "due_at": future.strftime("%Y-%m-%d %H:%M"), "timezone": "UTC"})
    monkeypatch.setattr(reminders, "_haiku", mismatch)
    assert asyncio.run(reminders._create("remind me Monday", None)).status == "failed"
    assert not store.list_pending()


def test_skill_requires_explicit_daily_and_existing_note(monkeypatch, tmp_path):
    _store(tmp_path, monkeypatch)
    from server.skills import reminders
    from server.db import notes_store
    monkeypatch.setattr(notes_store, "DB_PATH", tmp_path / "notes.db")
    notes_store._init()
    monkeypatch.setattr(reminders, "_local_timezone", lambda: "UTC")
    monkeypatch.setattr(reminders, "_projects_for_prompt", lambda: "(none)")
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    async def daily(*_args, **_kwargs):
        return json.dumps({"text": "check", "due_at": future.strftime("%Y-%m-%d %H:%M"), "timezone": "UTC", "recurrence": "daily"})
    monkeypatch.setattr(reminders, "_haiku", daily)
    result = asyncio.run(reminders._create("remind me later", None))
    assert result.status == "failed"
    assert not store.list_pending()

    async def unknown(*_args, **_kwargs):
        return json.dumps({"text": "check", "due_at": future.strftime("%Y-%m-%d %H:%M"), "timezone": "UTC", "recurrence": "weekly"})
    monkeypatch.setattr(reminders, "_haiku", unknown)
    assert asyncio.run(reminders._create("remind me weekly", None)).status == "failed"
    assert not store.list_pending()

    async def linked(*_args, **_kwargs):
        return json.dumps({"text": "check", "due_at": future.strftime("%Y-%m-%d %H:%M"), "timezone": "UTC", "recurrence": "daily", "until_note_id": 42})
    monkeypatch.setattr(reminders, "_haiku", linked)
    assert asyncio.run(reminders._create("remind me daily until note 42 is done", None)).status == "failed"
    assert not store.list_pending()
