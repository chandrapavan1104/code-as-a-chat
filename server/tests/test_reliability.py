"""Replay the failure classes found in the owner's conversation audit."""
import asyncio
import json

import pytest

from server import brain_health, config, main, workspace
from server.db import assistant_tasks_store
from server.outcomes import completion_status
from server.skills import shell, registry
from server.skills.base import Skill


def test_unverified_and_exhausted_are_not_completed():
    assert completion_status('The dashboard is functioning well.', 'done', []) == 'unverified'
    assert completion_status('No errors found', 'step_limit', []) == 'unverified'
    assert completion_status('Please upload the image.', 'done', []) == 'waiting_for_user'
    assert completion_status('Here is your rewritten draft.', 'done', []) == 'completed'


def test_unobserved_live_claim_is_not_returned_as_success(tmp_path, monkeypatch):
    async def brain(*args, **kwargs):
        return '{"action":"done","reply":"The dashboard is functioning well."}'
    monkeypatch.setattr(shell, '_haiku', brain)
    events = []
    async def event(frame):
        events.append(frame)
    async def turn():
        with workspace.bound(tmp_path):
            return await shell.ShellSkill().run('inspect dashboard', on_event=event)
    answer = asyncio.run(turn())
    assert 'functioning well' not in answer
    assert "couldn't verify" in answer
    assert next(e for e in events if e['type'] == 'completion')['status'] == 'unverified'


def test_cancelling_cli_reaps_its_process_group(monkeypatch):
    from server.skills.cli_base import CLISubprocessSkill
    calls = []
    class Process:
        pid = 12345
        async def communicate(self):
            raise asyncio.CancelledError
        async def wait(self):
            calls.append('reaped')
    async def spawn(*args, **kwargs):
        assert kwargs['start_new_session'] is True
        return Process()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr('server.skills.cli_base.os.killpg', lambda pid, sig: calls.append(pid))
    class Runner:
        timeout = 10
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(CLISubprocessSkill._spawn(Runner(), ['fake'], '/tmp'))
    assert calls == [12345, 'reaped']


def test_finished_response_requires_completion_event(tmp_path):
    work, _ = assistant_tasks_store.accept(request_id='r', session_id='app:x',
        command='shell', prompt='check my dashboard')
    task = main._finish_work(work, "I couldn't verify it", 'general', {})
    assert task['status'] == 'unverified'


def test_provider_auth_failure_is_visible_and_cools_down(monkeypatch):
    brain_health.failed('claude', RuntimeError('OAuth session expired'))
    assert not brain_health.available('claude')
    assert brain_health.snapshot()['claude']['reason'] == 'authentication expired or rejected'
    brain_health.succeeded('claude')
    assert brain_health.available('claude')


def test_claude_stdout_error_not_hidden(monkeypatch):
    class Process:
        returncode = 1
        async def communicate(self):
            return json.dumps({'is_error': True, 'result': 'OAuth session expired'}).encode(), b''
    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(shell.shutil, 'which', lambda _: '/fake/claude')
    monkeypatch.setattr(shell.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(shell, '_neutral_cwd', lambda: '/tmp')
    with pytest.raises(RuntimeError, match='OAuth session expired'):
        asyncio.run(shell._claude_cli('system', 'hello'))


def test_invalid_provider_decisions_never_become_answers(monkeypatch):
    async def invalid(*args, **kwargs):
        return 'garbled answer'
    monkeypatch.setattr(shell, '_provider_chain', lambda _: ['claude'])
    monkeypatch.setattr(shell, '_call_llm', invalid)
    with pytest.raises(RuntimeError, match='valid decision'):
        asyncio.run(shell._haiku('system', 'hi', validate=lambda _: False))


def test_duplicate_loop_stops_after_one_replay(tmp_path, monkeypatch):
    class Probe(Skill):
        name = 'loop_probe'
        description = 'Read state'
        async def run(self, prompt='', **kwargs):
            return 'No relevant data here'
    count = 0
    async def brain(*args, **kwargs):
        nonlocal count
        count += 1
        return '{"action":"call","tool":"loop_probe","args":"read"}'
    monkeypatch.setitem(registry, 'loop_probe', Probe())
    monkeypatch.setattr(shell, '_haiku', brain)
    async def turn():
        with workspace.bound(tmp_path):
            return await shell.ShellSkill().run('inspect dashboard')
    answer = asyncio.run(turn())
    assert count == 3
    assert 'unverified' in answer


def test_continuation_executes_and_receives_original(monkeypatch):
    from fastapi.testclient import TestClient
    calls = []
    async def route(command, prompt, **kwargs):
        calls.append(kwargs['work_context'])
        await kwargs['on_event']({'type': 'completion', 'status': 'unverified'})
        return 'Need another attempt'
    monkeypatch.setattr(main.orchestrator, 'route', route)
    client = TestClient(main.app)
    headers = {'X-API-Token': config.API_TOKEN}
    body = {'command': 'shell', 'prompt': 'Preserve my original wording',
            'request_id': 'one', 'session_id': 'app:test'}
    first = client.post('/run', json=body, headers=headers).json()
    body.update(request_id='two', prompt='Only fix grammar', continue_task_id=first['work']['id'])
    second = client.post('/run', json=body, headers=headers)
    assert second.status_code == 200
    assert len(calls) == 2
    assert 'Preserve my original wording' in calls[-1] and 'Only fix grammar' in calls[-1]
    body.update(request_id='one')
    client.post('/run', json=body, headers=headers)
    assert len(calls) == 2
