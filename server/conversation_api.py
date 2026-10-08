"""Conversation identities are independent of the workspace used for a turn."""
import re
import time
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from server.db import store

router = APIRouter()

def _owner(value: str) -> str:
    if not re.fullmatch(r'(?:app|tg):[A-Za-z0-9_-]{1,128}', value):
        raise HTTPException(400, 'Expected a client installation/chat identifier')
    return value


def _init(c):
    c.execute('CREATE TABLE IF NOT EXISTS chat_conversations '
              '(session_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, '
              'project TEXT, created_at REAL NOT NULL)')


class NewConversation(BaseModel):
    client_id: str
    title: str = Field(default='New conversation', min_length=1, max_length=120)
    project: str | None = None


@router.post('/conversations')
def create_conversation(body: NewConversation):
    owner = _owner(body.client_id)
    sid = f'{owner}:chat:{uuid.uuid4().hex}'
    with store._conn() as c:
        _init(c)
        c.execute('INSERT INTO chat_conversations VALUES (?,?,?,?,?)',
                  (sid, owner, body.title, body.project, time.time()))
        c.commit()
    return {'session_id': sid, 'title': body.title, 'project': body.project, 'legacy': False}


@router.get('/conversations')
def conversations(client_id: str):
    owner = _owner(client_id)
    with store._conn() as c:
        _init(c)
        rows = c.execute('SELECT session_id, MAX(ts) FROM conversations GROUP BY session_id').fetchall()
        meta = {r[0]: r for r in c.execute('SELECT session_id,title,project,created_at FROM chat_conversations WHERE owner=?', (owner,))}
        c.commit()
    times = {sid: ts for sid, ts in rows if store.client_scope(sid) == owner}
    for sid, m in meta.items():
        times.setdefault(sid, m[3])
    items = []
    for sid, ts in times.items():
        m = meta.get(sid)
        suffix = sid.split('::', 1)[-1] if '::' in sid else 'General'
        items.append({'session_id': sid, 'title': m[1] if m else suffix,
                      'project': m[2] if m else (suffix if '::' in sid else None),
                      'updated_at': ts, 'legacy': m is None})
    return {'items': sorted(items, key=lambda x: x['updated_at'], reverse=True)[:100]}


@router.get('/assistant/capabilities')
def capabilities():
    from server import domain_skills, tool_runtime, prefs
    from server.skills import registry
    return {'skills': domain_skills.manifest(), 'tools': tool_runtime.manifest(),
            'legacy_adapters': [{'name': name, 'enabled': prefs.is_skill_enabled(name),
                                 'input_schema': {'type': 'string'}, 'availability': 'unprobed'}
                                for name in registry if name != 'shell']}


@router.get('/assistant/operations')
def operation_history(session_id: str):
    from server import tool_runtime
    return {'items': tool_runtime.operations(session_id)}
