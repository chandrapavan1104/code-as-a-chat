"""Conversation continuity contracts, using simulated providers and no side effects."""
import asyncio
import json

import pytest
from server import conversation, context_resolver, workspace
from server.db import store, cli_sessions_store, native_sessions, agent_runs_store
from server.skills.cli_base import CLISubprocessSkill
from server.skills import shell
from server.outcomes import completion_status

SID = 'app:owner:chat:first'
OTHER = 'app:owner:chat:second'


def test_current_request_once_and_exact_reply():
    parent = store.append_turn(SID, 'user', 'A source outside the recent window')
    for i in range(30):
        store.append_turn(SID, 'assistant', f'Other answer {i}')
    with store.turn_context(SID, parent):
        store.append_turn(SID, 'user', 'Explain that exact source')
        p = conversation.compile_context(SID, 'Explain that exact source', store.get_recent(SID, 12),
                                         reply_context={'message_id': parent})
    assert 'Explain that exact source' not in p['text']  # Added once by the caller, outside history.
    assert 'A source outside the recent window' in p['text']
    assert parent in p['source_message_ids']


def test_projection_is_user_sourced_and_survives_rebuild():
    first = store.append_turn(SID, 'user', 'Never publish without my confirmation')
    store.append_turn(SID, 'assistant', 'I deployed it and charged $140.38')
    for i in range(35):
        store.append_turn(SID, 'user', f'Another topic {i}')
    p = conversation.compile_context(SID, 'What did we agree?', store.get_recent(SID, 12))
    assert 'Never publish' in p['text'] and str(first) in p['text']
    assert 'charged $140.38' not in p['text']
    a = conversation.state(SID)
    assert a['summary_watermark'] > 0
    assert conversation.state(SID)['version'] == a['version']
    conversation.observe(SID, 'new-run', 'failed', [{'tool': 'claude', 'status': 'failed', 'result': 'auth rejected'}])
    store.append_turn(SID, 'user', 'Actually use Codex instead')
    b = conversation.state(SID)
    assert b['observations'][-1]['run_id'] == 'new-run'
    assert b['corrections'][-1]['text'] == 'Actually use Codex instead'


def test_summary_failure_falls_back_to_raw_messages(monkeypatch):
    store.append_turn(SID, 'user', 'Important preceding question')
    monkeypatch.setattr(conversation, 'state', lambda _: (_ for _ in ()).throw(ValueError('broken projection')))
    p = conversation.compile_context(SID, 'why?', store.get_recent(SID, 12))
    assert p['projection_failed'] and 'Important preceding question' in p['text']


def test_retrieval_defaults_to_current_conversation():
    store.append_turn(SID, 'user', 'Earlier research privacy budget local models')
    store.append_turn(OTHER, 'user', 'Earlier research privacy budget secret other project')
    result = context_resolver.resolve('remember research privacy budget', SID)
    assert 'local models' in result and 'secret other project' not in result


def test_native_newest_cannot_redirect_conversation(tmp_path, monkeypatch):
    class Fake(CLISubprocessSkill):
        cli_name = 'claude'
    cwd = str(tmp_path)
    cli_sessions_store.set(cwd, 'claude', 'legacy-project-session')
    cli_sessions_store.conversation_set(SID, cwd, 'claude', 'conversation-session')
    monkeypatch.setattr(native_sessions, 'latest', lambda *_: ('newer-mac-session', 999))
    assert Fake()._resume_id(cwd, SID) == 'conversation-session'
    assert Fake()._resume_id(cwd, OTHER) is None
    assert cli_sessions_store.get(cwd, 'claude') == 'legacy-project-session'
    monkeypatch.setenv('CONVERSATION_CONTEXT_ENABLED', '0')
    assert Fake()._resume_id(cwd, SID) == 'conversation-session'


def test_session_exclusivity_engine_switch_and_canonical_path(tmp_path):
    cwd = str(tmp_path)
    cli_sessions_store.conversation_set(SID, cwd, 'claude', 'owned')
    assert cli_sessions_store.conversation_get(SID, cwd + '/.', 'claude') == 'owned'
    assert cli_sessions_store.conversation_get(SID, cwd, 'codex') is None
    with pytest.raises(cli_sessions_store.SessionOwned):
        cli_sessions_store.conversation_set(OTHER, cwd, 'claude', 'owned')
    cli_sessions_store.conversation_set(OTHER, cwd, 'claude', 'separate')
    assert cli_sessions_store.conversation_get(SID, cwd, 'claude') == 'owned'


def test_turn_lock_orders_history_and_cancellation_releases():
    async def go():
        entered = asyncio.Event()
        release = asyncio.Event()
        order = []
        async def first():
            async with conversation.turn_lock(SID):
                store.append_turn(SID, 'user', 'first')
                order.append('first')
                entered.set()
                await release.wait()
                store.append_turn(SID, 'assistant', 'first reply')
        async def second():
            async with conversation.turn_lock(SID):
                order.append('second')
                assert [r['content'] for r in store.get_recent(SID)] == ['first', 'first reply']
        a = asyncio.create_task(first()); await entered.wait()
        b = asyncio.create_task(second()); await asyncio.sleep(0)
        assert order == ['first']
        release.set(); await asyncio.gather(a, b)
        async with conversation.turn_lock(SID):
            waiter = asyncio.create_task(second())
            # Child tasks inherit the lock context; real HTTP requests start outside it.
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError): await waiter
        assert SID not in conversation._locks
    asyncio.run(go())


@pytest.mark.parametrize('reply', [
    'A deployed service is a program made available to users.',
    'Saved means the text was persisted; created describes making a new item.',
    'The $140.38 value in the earlier error was a session estimate, not an invoice. I cannot verify your actual bill.',
])
def test_explanations_are_not_new_execution_claims(reply):
    # Honest uncertainty may remain unverified; it must not be rejected as fabricated execution.
    from server.outcomes import execution_claim
    assert not execution_claim(reply)
    if 'cannot' not in reply:
        assert completion_status(reply, 'done', []) == 'completed'


def test_try_again_handoff_includes_prior_corrections(tmp_path, monkeypatch):
    from server.skills.base import Skill, SkillResult
    from server import tool_runtime
    store.append_turn(SID, 'user', 'Implement a compact UI. Never modify trading permissions.')
    store.append_turn(SID, 'assistant', 'The first build failed.')
    captured = []
    class Coder(Skill):
        name = 'claude'
        description = 'Simulated coding tool'
        async def run(self, prompt='', **kwargs):
            captured.append((prompt, kwargs))
            return SkillResult('succeeded', 'Build verified')
    replies = iter([json.dumps({'action': 'call', 'tool': 'claude', 'args': 'Retry build', 'final': True})])
    async def brain(*args, **kwargs): return next(replies)
    monkeypatch.setattr(shell, '_haiku', brain)
    from server.skills import registry
    monkeypatch.setitem(registry, 'claude', Coder())
    original = shell.get_skill
    monkeypatch.setattr(shell, 'get_skill', lambda name: Coder() if name == 'claude' else original(name))
    async def go():
        async with conversation.turn_lock(SID):
            with workspace.bound(tmp_path), store.turn_context(SID):
                store.append_turn(SID, 'user', 'try again')
                return await shell.ShellSkill().run('try again', session_id=SID, request_id='retry-once')
    result = asyncio.run(go())
    assert captured, result
    assert 'Never modify trading permissions' in captured[0][0]
    assert captured[0][1]['context_included']
    assert 'Build verified' in result
    run = agent_runs_store.list_runs(SID)[0]
    detail = agent_runs_store.get(run['id'])
    assert detail['context']['source_message_ids']
    assert 'Never modify' not in json.dumps(detail['context'])


def test_bill_followup_is_preserved_without_tools(tmp_path, monkeypatch):
    store.append_turn(SID, 'assistant', 'The error output included an estimated session total of $140.38.')
    answer = 'That number came from the earlier CLI error. It does not establish an actual bill.'
    async def brain(*args, **kwargs):
        assert '$140.38' in args[1]
        return json.dumps({'action': 'done', 'response_kind': 'explanation', 'reply': answer})
    monkeypatch.setattr(shell, '_haiku', brain)
    async def go():
        async with conversation.turn_lock(SID):
            with workspace.bound(tmp_path):
                return await shell.ShellSkill().run('what is 140.38 bill?', session_id=SID)
    assert asyncio.run(go()) == answer


def test_older_work_cannot_overwrite_newer_revision():
    from server.main import _finish_work
    from server.db import assistant_tasks_store as tasks
    old, _ = tasks.accept(request_id='one', session_id=SID, command='shell', prompt='first')
    newer, _ = tasks.accept(request_id='two', session_id=SID, command='shell', prompt='actually stop', continue_task_id=old['id'])
    final = _finish_work(old, 'old success', 'project', {'status': 'completed'})
    assert final['revision'] == newer['revision'] and final['latest_prompt'] == 'actually stop'
    assert final['status'] == 'accepted'


def test_provider_retry_receives_identical_context(tmp_path, monkeypatch):
    store.append_turn(SID, 'user', 'Keep the original decision: use local files only')
    inputs = []
    async def brain(system, supplied, **kwargs):
        inputs.append(supplied)
        if len(inputs) == 1:
            raise RuntimeError('provider unavailable')
        return json.dumps({'action': 'done', 'reply': 'The original decision remains: local files only.'})
    monkeypatch.setattr(shell, '_haiku', brain)
    async def go():
        async with conversation.turn_lock(SID):
            with workspace.bound(tmp_path):
                return await shell.ShellSkill().run('what did we decide?', session_id=SID)
    assert 'local files only' in asyncio.run(go())
    assert len(inputs) == 2 and inputs[0] == inputs[1]


def test_http_turns_wait_before_inserting_user_message(tmp_path, monkeypatch):
    from server import main
    from fastapi import BackgroundTasks
    events = []
    async def fake_route(command, prompt, **kwargs):
        events.append((prompt, [r['content'] for r in store.get_recent(SID)]))
        await asyncio.sleep(0.02)
        shell.ShellSkill._remember(SID, prompt, 'Reply to ' + prompt)
        return 'Reply to ' + prompt
    monkeypatch.setattr(main.orchestrator, 'route', fake_route)
    async def go():
        first = main.RunRequest(command='shell', prompt='first', session_id=SID,
                                project=str(tmp_path), request_id='http-first')
        second = main.RunRequest(command='shell', prompt='second', session_id=SID,
                                 project=str(tmp_path), request_id='http-second')
        result = await asyncio.gather(main.run(first, BackgroundTasks()), main.run(second, BackgroundTasks()))
        assert all(r['result'].startswith('Reply') for r in result)
    asyncio.run(go())
    assert events == [('first', ['first']), ('second', ['first', 'Reply to first', 'second'])]


def test_fresh_session_is_kept_for_backup_model(tmp_path, monkeypatch):
    from server.skills.claude_code import ClaudeCodeSkill
    from server import prefs
    monkeypatch.setattr('server.skills.cli_base.shutil.which', lambda _: '/simulated/claude')
    monkeypatch.setattr(prefs, 'get_coding_model', lambda _: 'opus')
    monkeypatch.setattr(prefs, 'get_backup_model', lambda _: 'sonnet')
    skill = ClaudeCodeSkill()
    monkeypatch.setattr(skill, '_sync_context', lambda: None)
    calls = []
    async def spawn(command, cwd):
        calls.append(command)
        if len(calls) == 1:
            return 1, '{"session_id":"fresh-owned","is_error":true,"result":"model unavailable"}', ''
        return 0, '{"session_id":"fresh-owned","result":"backup answer"}', ''
    monkeypatch.setattr(skill, '_spawn', spawn)
    async def go():
        async with conversation.turn_lock(SID):
            with workspace.bound(tmp_path):
                return await skill.run('continue with the same constraints', session_id=SID)
    result = asyncio.run(go())
    assert len(calls) == 2 and '--resume' not in calls[0]
    assert calls[1][calls[1].index('--resume') + 1] == 'fresh-owned'
    assert 'backup answer' in result
