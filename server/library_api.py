"""Bounded native Library views reuse the existing file and CLI session stores."""
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from server import workspace
from server.conversation_api import NewConversation, create_conversation, _owner
from server.db import cli_sessions_store
from server.skills import sessions
from server.skills.filemanager import FileManagerSkill

router = APIRouter(prefix='/library', tags=['library'])


def _project(project):
    result = workspace.resolve(project) if project else workspace.active()
    if result is None or not result.is_dir():
        raise HTTPException(404, 'Project folder not found')
    return result.resolve()


def _path(raw, project):
    base = _project(project)
    return (Path(raw).expanduser() if Path(raw).is_absolute() else base / raw).resolve()


@router.get('/options')
def options():
    from server.skills.reminders import _local_timezone
    try:
        timezone = _local_timezone()
    except ValueError:
        timezone = 'UTC'
    return {'timezone': timezone}


@router.get('/files')
def files(path: str = '', project: str | None = None):
    folder = _path(path, project)
    if not folder.is_dir():
        raise HTTPException(404, 'Folder not found')
    try:
        children = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        items = []
        for child in children[:500]:
            try:
                items.append({'name': child.name, 'path': str(child), 'is_dir': child.is_dir(),
                              'size': child.stat().st_size if child.is_file() else None})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, 'This folder is not readable')
    return {'path': str(folder), 'current_path': str(folder),
            'parent': str(folder.parent) if folder.parent != folder else None,
            'items': items, 'truncated': len(children) > 500}


class ShareFile(BaseModel):
    path: str
    project: str | None = None


@router.post('/files/share')
def share_file(body: ShareFile):
    result = FileManagerSkill._share(_path(body.path, body.project))
    if not result.ok:
        raise HTTPException(400, result.message)
    return result.data


def _sessions(engine, project=None):
    if engine not in ('all', 'claude', 'codex', 'gemini'):
        raise HTTPException(422, 'Unknown session engine')
    rows = sessions._all_sessions()
    root = _project(project) if project else None
    return [r for r in rows if (engine == 'all' or r['engine'] == engine)
            and (root is None or Path(r.get('cwd') or '/').resolve() == root)]


def _session(id, engine):
    rows = [r for r in _sessions(engine) if r['id'] == id]
    if len(rows) != 1:
        raise HTTPException(404, 'Exact session not found')
    return rows[0]


def _public(row):
    return {k: v for k, v in row.items() if k != 'path'}


@router.get('/sessions')
def list_sessions(engine: str = 'all', project: str | None = None):
    rows = sorted(_sessions(engine, project), key=lambda r: r['mtime'], reverse=True)
    return {'items': [_public(r) for r in rows[:100]], 'truncated': len(rows) > 100}


@router.get('/sessions/detail')
def session_detail(id: str, engine: str):
    row = _session(id, engine)
    turns = getattr(sessions, f'_extract_turns_{engine}')(Path(row['path']))
    selected = turns[-40:]
    budget = 100000
    output = []
    truncated = len(turns) > 40
    for role, content in selected:
        clipped = content[:min(10000, budget)]
        truncated |= len(clipped) != len(content)
        if clipped:
            output.append({'role': role, 'content': clipped})
        budget -= len(clipped)
    return {**_public(row), 'turns': output, 'truncated': truncated}


class ContinueSession(BaseModel):
    id: str
    engine: str
    client_id: str


@router.post('/sessions/continue')
def continue_session(body: ContinueSession):
    _owner(body.client_id)
    row = _session(body.id, body.engine)
    folder = Path(row.get('cwd') or '').expanduser()
    if not row.get('cwd') or not folder.is_dir():
        raise HTTPException(404, 'Session project folder no longer exists')
    conversation = create_conversation(NewConversation(client_id=body.client_id,
        title=f"{body.engine.title()} session {body.id[:8]}", project=str(folder)))
    cli_sessions_store.pin(str(folder), body.engine, body.id)
    return {**conversation, 'engine': body.engine}
