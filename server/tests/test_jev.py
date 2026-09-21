"""Jev must never make quota loss or malformed decisions break a turn."""
import asyncio
import json

import httpx
import pytest

from server import config, jev


@pytest.mark.parametrize('mode', ['quota', 'timeout', 'malformed', 'uncertain', 'success'])
def test_decision_fallback_and_circuit(monkeypatch, mode):
    monkeypatch.setattr(config, 'JEV_ENABLED', True)
    monkeypatch.setattr(config, 'TYPESAFE_API_KEY', 'test-only')
    monkeypatch.setattr(jev, '_retry_after', 0.0)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append(kwargs)
            if mode == 'timeout':
                raise httpx.ReadTimeout('slow')
            return httpx.Response(
                429 if mode == 'quota' else 200,
                request=httpx.Request('POST', url),
                json={} if mode == 'malformed' else {'answers': {'decision': {
                    'choice': 'yes', 'confidence': 0.5 if mode == 'uncertain' else 0.95}}})

    monkeypatch.setattr(jev.httpx, 'AsyncClient', Client)
    result = asyncio.run(jev.decide('test', {}, {'yes': 'yes'}))
    assert result == ('yes' if mode == 'success' else None)
    if mode in ('quota', 'timeout', 'malformed'):
        assert asyncio.run(jev.decide('test', {}, {'yes': 'yes'})) is None
        assert len(calls) == 1


def test_low_confidence_does_not_change_completion(monkeypatch):
    async def uncertain(*args):
        return None
    monkeypatch.setattr(jev, 'decide', uncertain)
    assert not asyncio.run(jev.unsupported_success('request', 'done', [{'result': 'ok'}]))


def test_continuity_sends_only_bounded_recent_text(monkeypatch):
    async def capture(purpose, state, criteria):
        assert len(state['recent']) == 4
        assert all(len(r['content']) == 2000 for r in state['recent'])
        assert 'secret_field' not in str(state)
        return 'correction'
    monkeypatch.setattr(jev, 'decide', capture)
    history = [{'role': 'user', 'content': 'x' * 3000, 'secret_field': 'unused'}] * 10
    assert asyncio.run(jev.continuity('try again', history)) == 'correction'


@pytest.mark.parametrize('flagged', [False, True])
def test_shell_review_repairs_claim_without_repeating_tool(monkeypatch, tmp_path, flagged):
    from server import workspace
    from server.skills import registry, shell
    from server.skills.base import Skill

    calls = []

    class Probe(Skill):
        name = 'jev_probe'
        description = 'Observe playback'

        async def run(self, prompt='', **kwargs):
            calls.append(prompt)
            return 'Search page opened. Playback status is unknown.'

    decisions = iter([
        {'action': 'call', 'tool': 'jev_probe', 'args': 'observe'},
        {'action': 'done', 'reply': 'Playing on the TV.'},
        {'action': 'done', 'reply': 'The search page is open; TV playback remains unverified.'},
    ])
    inputs = []

    async def brain(system, text, **kwargs):
        inputs.append(text)
        return json.dumps(next(decisions))

    async def review(prompt, reply, steps):
        return flagged and reply == 'Playing on the TV.'

    monkeypatch.setitem(registry, 'jev_probe', Probe())
    monkeypatch.setattr(shell, '_haiku', brain)
    monkeypatch.setattr(jev, 'unsupported_success', review)

    async def turn():
        with workspace.bound(tmp_path):
            return await shell.ShellSkill().run('Play music on the TV')

    result = asyncio.run(turn())
    assert calls == ['observe']
    assert len(inputs) == (3 if flagged else 2)
    assert result == ('The search page is open; TV playback remains unverified.'
                      if flagged else 'Playing on the TV.')
