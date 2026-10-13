"""Catalog refresh must never manufacture provider availability."""
import time
from server import model_catalog as catalog


def test_refresh_replaces_removed_models(monkeypatch):
    monkeypatch.setattr(catalog, '_cache', {})
    rows = [{'id': 'new-release'}]
    monkeypatch.setattr(catalog, '_discover', lambda e: rows)
    assert catalog.get('codex')['models'] == rows
    rows = [{'id': 'next-release'}]
    assert catalog.get('codex', refresh=True)['models'] == rows


def test_refresh_coalesces(monkeypatch):
    monkeypatch.setattr(catalog, '_cache', {})
    calls = []
    monkeypatch.setattr(catalog, '_discover', lambda e: calls.append(e) or [{'id': 'current'}])
    catalog.get('claude')
    catalog.get('claude')
    assert calls == ['claude']


def test_failed_refresh_marks_previous_stale(monkeypatch):
    monkeypatch.setattr(catalog, '_cache', {'gemini': {
        'models': [{'id': 'previous'}], 'updated_at': time.time()}})
    def fail(e):
        raise RuntimeError('private error detail')
    monkeypatch.setattr(catalog, '_discover', fail)
    result = catalog.get('gemini')
    assert result['models'] == [{'id': 'previous'}]
    assert result['stale'] and result['error']
    assert 'private' not in result['error']


def test_failed_refresh_drops_expired_models(monkeypatch):
    monkeypatch.setattr(catalog, '_cache', {'codex': {
        'models': [{'id': 'old'}], 'updated_at': time.time() - catalog.MAX_STALE - 1}})
    monkeypatch.setattr(catalog, '_discover', lambda e: [])
    assert catalog.get('codex')['models'] == []


def test_exact_gemini_model_is_preserved():
    from server.skills.model_switch import _resolve
    assert _resolve('gemini gemini-new-pro-preview')[:2] == ('gemini', 'gemini-new-pro-preview')


def test_codex_default_omits_model_flag(monkeypatch):
    from server import config
    from server.skills.codex import CodexSkill
    monkeypatch.setattr(config, 'CODEX_MODEL', '')
    assert '--model' not in CodexSkill().build_command('hello')
    cmd = CodexSkill().build_command('hello', model='chosen')
    assert cmd[cmd.index('--model') + 1] == 'chosen'


def test_api_presets_and_metadata_agree(monkeypatch):
    from server import api_v2, prefs
    monkeypatch.setattr(catalog, 'snapshot', lambda **kw: {
        'claude': {'models': [{'id': 'sonnet', 'resolved_model': 'latest-sonnet'}]}})
    monkeypatch.setattr(prefs, 'get_coding_engine', lambda: 'claude')
    result = api_v2.get_model()
    assert result['presets']['claude'] == ['sonnet']
    assert result['catalogs']['claude']['models'][0]['resolved_model'] == 'latest-sonnet'
    assert result['routing']['openai_backup']


def test_claude_timeout_reason_is_visible(monkeypatch):
    from server import brain_health
    monkeypatch.setattr(brain_health, '_failures', {})
    brain_health.failed('claude', TimeoutError('Claude routing call timed out after 120s'))
    assert '120s' in brain_health.snapshot()['claude']['reason']


def test_codex_catalog_paginates_and_excludes_hidden(monkeypatch):
    import io
    class Process:
        stdin = io.StringIO()
        stdout = io.StringIO(
            '{"id":1,"result":{}}\n'
            '{"id":2,"result":{"data":[{"model":"first"},{"model":"hidden","hidden":true}],"nextCursor":"more"}}\n'
            '{"id":3,"result":{"data":[{"model":"second"}],"nextCursor":null}}\n')
        def kill(self): pass
        def poll(self): return 0
        def communicate(self): return ('', '')
    process = Process()
    monkeypatch.setattr(catalog.subprocess, 'Popen', lambda *a, **kw: process)
    assert [m['id'] for m in catalog._rpc('codex')] == ['first', 'second']
    assert '"cursor": "more"' in process.stdin.getvalue()


def test_chat_bootstrap_skips_catalog_discovery(monkeypatch):
    from server import api_v2, prefs
    def forbidden(**kwargs):
        raise AssertionError("chat bootstrap must not discover models")
    monkeypatch.setattr(catalog, 'snapshot', forbidden)
    monkeypatch.setattr(prefs, 'get_coding_engine', lambda: 'codex')
    result = api_v2.get_model(include_catalog=False)
    assert result['engine'] == 'codex'
    assert result['catalogs'] == {}
