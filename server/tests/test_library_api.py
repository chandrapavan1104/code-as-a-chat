from pathlib import Path
import pytest
from fastapi import HTTPException
from server import library_api, workspace
from server.db import cli_sessions_store, store


def test_files_are_scoped_and_shared_copy_survives_source_change(tmp_path):
    source = tmp_path / 'hello.txt'
    source.write_text('hello')
    with workspace.bound(tmp_path):
        result = library_api.files()
        assert result['current_path'] == str(tmp_path)
        assert next(i for i in result['items'] if i['name'] == source.name)['size'] == 5
        shared = library_api.share_file(library_api.ShareFile(path=source.name))
        source.write_text('changed')
        assert Path(shared['path']).read_text() == 'hello'
    assert workspace.active() != tmp_path


def test_missing_share_has_readable_error(tmp_path):
    with workspace.bound(tmp_path), pytest.raises(HTTPException) as exc:
        library_api.share_file(library_api.ShareFile(path='missing.txt'))
    assert exc.value.status_code == 400
    assert 'regular file' in exc.value.detail


def test_sessions_require_exact_engine_and_id_and_cap_preview(tmp_path, monkeypatch):
    rows = [{'id': 'abc123', 'engine': 'codex', 'cwd': str(tmp_path),
             'mtime': 1, 'preview': 'hello', 'path': tmp_path/'native.jsonl'}]
    monkeypatch.setattr(library_api.sessions, '_all_sessions', lambda: rows)
    monkeypatch.setattr(library_api.sessions, '_extract_turns_codex',
                        lambda p: [('user', 'x'*20000)]*45)
    with pytest.raises(HTTPException):
        library_api.session_detail('abc', 'codex')
    with pytest.raises(HTTPException):
        library_api.session_detail('abc123', 'claude')
    detail = library_api.session_detail('abc123', 'codex')
    assert detail['truncated']
    assert sum(len(t['content']) for t in detail['turns']) <= 100000
    assert 'path' not in detail


def test_continue_pins_exact_session_and_creates_independent_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(library_api.sessions, '_all_sessions', lambda: [
        {'id': 'abc123', 'engine': 'codex', 'cwd': str(tmp_path), 'mtime': 1,
         'preview': 'hello', 'path': tmp_path/'native.jsonl'}])
    pins = []
    monkeypatch.setattr(cli_sessions_store, 'pin', lambda *args: pins.append(args))
    result = library_api.continue_session(library_api.ContinueSession(
        id='abc123', engine='codex', client_id='app:testing'))
    assert result['session_id'].startswith('app:testing:chat:')
    assert result['project'] == str(tmp_path)
    assert pins == [(str(tmp_path), 'codex', 'abc123')]
    assert workspace.active() != tmp_path
    with pytest.raises(HTTPException):
        library_api.continue_session(library_api.ContinueSession(
            id='abc123', engine='codex', client_id='app:testing::wrong'))


def test_native_routes_are_mounted_and_authenticated():
    from fastapi.testclient import TestClient
    from server.main import app
    client = TestClient(app)
    for path in ('/api/library/options', '/api/library/files',
                 '/api/library/sessions', '/api/ports'):
        assert client.get(path).status_code == 401
    from server import config
    response = client.get('/api/library/options', headers={'X-API-Token': config.API_TOKEN})
    assert response.status_code == 200
    assert response.json()['timezone']
