"""Corrections survive rolling history without rewriting diary entries."""
import asyncio

import pytest

from pathlib import Path


@pytest.fixture(autouse=True)
def diary_db(tmp_path, monkeypatch):
    global store, diary
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'home', staticmethod(lambda: tmp_path))
        from server.db import diary_store as store
        from server.skills import diary
    monkeypatch.setattr(store, 'DB_PATH', tmp_path / 'diary.db')
    store._init()


def test_correction_persists_and_excludes_old_assistant_judgments():
    store.add('user', 'love', 'Getting to know someone.')
    store.add('anna', 'love', 'Old unsupported judgment.')
    store.set_context_note('relationship', 'No deep feelings; interest unknown.')
    for i in range(12):
        store.add('user', 'general', f'New update {i}')
    assert store.context_notes()[0]['content'] == 'No deep feelings; interest unknown.'
    assert 'Old unsupported judgment' not in diary._context_block(store.since(0))
    assert 'Getting to know someone.' in diary._context_block(store.since(0))
    # History remains available to the owner, even though it no longer primes replies.
    assert any(r['content'] == 'Old unsupported judgment.' for r in store.since(0))
    store.add('anna', 'general', 'New friendly reply.')
    assert 'New friendly reply.' in diary._context_block()
    store.set_context_note('relationship', 'Updated clarification.')
    assert len(store.context_notes()) == 1
    assert store.context_notes()[0]['content'] == 'Updated clarification.'


def test_conversation_includes_correction_and_current_entry(monkeypatch):
    store.set_context_note('preference', 'No unsolicited advice.')
    captured = {}

    async def fake_model(system, prompt, **kwargs):
        captured['prompt'] = prompt
        return '{"category":"general","reply":"Hey, nice to meet you!"}'

    monkeypatch.setattr(diary, '_haiku', fake_model)
    reply = asyncio.run(diary._converse('Say hello to my friend.'))
    assert reply == 'Hey, nice to meet you!'
    assert 'No unsolicited advice.' in captured['prompt']
    assert 'Say hello to my friend.' in captured['prompt']
    assert [r['role'] for r in store.recent()] == ['user', 'anna']


def test_weekly_review_uses_full_week_and_corrections(monkeypatch):
    store.add('user', 'career', 'Early week milestone.')
    for i in range(12):
        store.add('user', 'general', f'Update {i}')
    store.set_context_note('preference', 'Friendly review please.')
    captured = {}

    async def fake_model(system, prompt, **kwargs):
        captured['prompt'] = prompt
        return '{"category":"general","reply":"Good week."}'

    monkeypatch.setattr(diary, '_haiku', fake_model)
    asyncio.run(diary.DiarySkill().run('review'))
    assert 'Early week milestone.' in captured['prompt']
    assert 'Friendly review please.' in captured['prompt']


def test_provider_failure_still_saves_current_entry(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError('offline')

    monkeypatch.setattr(diary, '_haiku', fail)
    assert 'Entry saved' in asyncio.run(diary._converse('Keep this update.'))
    assert store.recent()[0]['content'] == 'Keep this update.'
