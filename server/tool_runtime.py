"""Typed tool contracts and durable operation leases, separate from skill guidance."""
from __future__ import annotations
import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from server.skills.base import SkillResult
from server.db import store
from server import context_resolver, workspace

DB_PATH = Path.home() / '.codeasachat' / 'tool_operations.db'

class ToolInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

class MessageInput(ToolInput):
    message_id: int = Field(gt=0)

class SearchInput(ToolInput):
    query: str = Field(min_length=2, max_length=500)
    all_conversations: bool = False
    limit: int = Field(default=10, ge=1, le=30)

class FileInput(ToolInput):
    path: str = Field(min_length=1, max_length=2048)
    project: str | None = None

class ProjectInput(ToolInput):
    project: str | None = None

class EmptyInput(ToolInput):
    pass

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: type[ToolInput]
    execute: Callable
    version: int = 1
    effect: str = 'read'
    executor: str = 'local'
    timeout: int = 30
    authorize: Callable | None = None

    def manifest(self):
        return {'name': self.name, 'version': self.version, 'description': self.description,
                'input_schema': self.schema.model_json_schema(), 'effect': self.effect,
                'executor': self.executor, 'timeout_seconds': self.timeout,
                'available': True, 'verification': 'structured source observation'}


def _message(args, session):
    row = store.get_message(args.message_id, session, all_client=True)
    if row is None:
        raise ValueError('Message not found in this client namespace')
    return context_resolver.source(row, 40000)


def _search(args, session):
    from server import context_index
    return {'sources': [context_resolver.source(row, 800) for row in context_index.search(
        session, args.query, all_conversations=args.all_conversations, limit=args.limit)]}


def _file(args, session):
    if args.project:
        target = workspace.resolve(args.project)
        if target is None:
            raise ValueError('Project was not uniquely resolved; clarify the target')
        with workspace.bound(target):
            return context_resolver.read_project_file(args.path)
    return context_resolver.read_project_file(args.path)


def _inspect(args, session):
    root = workspace.resolve(args.project) if args.project else workspace.active()
    if root is None:
        raise ValueError('Project was not uniquely resolved; clarify the target')
    return {'project': str(root), 'exists': root.is_dir(),
            'entries': sorted(p.name for p in root.iterdir() if not p.name.startswith('.'))[:100]}

TOOLS = {s.name: s for s in (
    ToolSpec('context.read_message', 'Retrieve exact historical message by ID with source provenance.', MessageInput, _message),
    ToolSpec('context.search', 'Search owned messages; results are excerpts, retrieve exact selected source.', SearchInput, _search),
    ToolSpec('context.read_file', 'Read a text source inside the active project; hidden/credential files excluded.', FileInput, _file),
    ToolSpec('project.inspect', 'Inspect the bound project without switching conversations.', ProjectInput, _inspect),
)}

class ToolAdapter:
    final_output = False
    passthrough = False
    def __init__(self, spec):
        self.spec = spec
    async def run(self, prompt='', session_id=None, **kwargs):
        if not session_id:
            return SkillResult('blocked', 'ERROR: This tool requires a conversation identity')
        try:
            args = self.spec.schema.model_validate_json(prompt or '{}')
            if self.spec.effect != 'read' and (self.spec.authorize is None or not self.spec.authorize(args, kwargs)):
                return SkillResult('blocked', 'ERROR: This operation requires a validated authorization before execution')
            data = self.spec.execute(args, session_id)
            return SkillResult('succeeded', json.dumps(data, ensure_ascii=False), data=data,
                               evidence=[self.spec.name + ': observed local source'])
        except (ValidationError, ValueError, OSError, UnicodeError) as exc:
            return SkillResult('failed', 'ERROR: ' + str(exc))


def adapter(name):
    return ToolAdapter(TOOLS[name]) if name in TOOLS else None


def _connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS operations ('
              'id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT, tool TEXT, '
              'state TEXT NOT NULL, lease_until REAL, result TEXT, updated_at REAL)')
    return c


def _update(key, state, result=None):
    with _connection() as c:
        c.execute('UPDATE operations SET state=?,lease_until=?,result=COALESCE(?,result),updated_at=? WHERE id=?',
                  (state, time.time()+45 if state=='running' else None, result, time.time(), key))


async def execute(executor, name, args, *, session_id, request_id=None, **kwargs):
    """Replay known results, never replay an uncertain side effect after lease loss."""
    request = request_id or uuid.uuid4().hex
    key = hashlib.sha256(json.dumps([session_id, request, name, args], ensure_ascii=False).encode()).hexdigest()
    with _connection() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM operations WHERE id=?', (key,)).fetchone()
        if old:
            if old['state'] == 'finished' and old['result']:
                saved = json.loads(old['result'])
                return SkillResult(**saved) if isinstance(saved, dict) else saved
            return SkillResult('blocked', 'ERROR: This operation is still running or its prior outcome is uncertain. Inspect the existing trace before retrying.')
        c.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?,?)',
                  (key, session_id, request, name, 'running', time.time()+45, None, time.time()))
    async def heartbeat():
        while True:
            await asyncio.sleep(15)
            _update(key, 'running')
    pulse = asyncio.create_task(heartbeat())
    try:
        result = await executor.run(args, session_id=session_id, request_id=request_id, **kwargs)
        saved = result.as_dict() if isinstance(result, SkillResult) else str(result)
        _update(key, 'finished', json.dumps(saved, ensure_ascii=False))
        return result
    except asyncio.CancelledError:
        _update(key, 'cancelled')
        raise
    except Exception:
        _update(key, 'unknown')
        raise
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)


def operations(session_id):
    with _connection() as c:
        rows = c.execute('SELECT id,request_id,tool,state,lease_until,updated_at FROM operations WHERE session_id=? ORDER BY updated_at DESC LIMIT 100', (session_id,)).fetchall()
    return [{**dict(row), 'state': ('unknown' if row['state']=='running' and row['lease_until']<time.time() else row['state'])} for row in rows]


def manifest():
    return [spec.manifest() for spec in TOOLS.values()]
