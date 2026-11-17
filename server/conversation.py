"""Conversation-owned context. Derived extracts never become execution evidence."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps

from server.db import store
from server import config

_enabled = lambda: config.CONVERSATION_CONTEXT_ENABLED
_held: ContextVar[tuple | None] = ContextVar('conversation_lock', default=None)
_package: ContextVar[dict | None] = ContextVar('conversation_package', default=None)
_locks: dict[str, tuple[asyncio.Lock, int]] = {}


@asynccontextmanager
async def turn_lock(session_id):
    """Serialize before user-message insertion, including disconnected stream workers."""
    if not session_id or _held.get() == (session_id, asyncio.current_task()):
        yield
        return
    lock, count = _locks.get(session_id, (asyncio.Lock(), 0))
    _locks[session_id] = (lock, count + 1)
    try:
        async with lock:
            token = _held.set((session_id, asyncio.current_task()))
            package_token = _package.set(None)
            try:
                yield
            finally:
                _package.reset(package_token)
                _held.reset(token)
    finally:
        _, count = _locks[session_id]
        if count == 1:
            del _locks[session_id]
        else:
            _locks[session_id] = (lock, count - 1)


def serialized(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        sid = kwargs.get('session_id')
        async with turn_lock(sid):
            return await fn(*args, **kwargs)
    return wrapped


def _init(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS conversation_state (
        session_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
        watermark INTEGER NOT NULL, state TEXT NOT NULL)''')


def state(session_id: str) -> dict:
    """An extractive summary: user statements retain provenance and original wording.

    No extra provider call is required. Assistant prose is never promoted to facts.
    Rebuild from durable messages makes recovery and projection failure safe.
    """
    with store._conn() as conn:
        _init(conn)
        previous = conn.execute('SELECT version,watermark,state FROM conversation_state '
                                'WHERE session_id=?', (session_id,)).fetchone()
        rows = conn.execute('SELECT id,role,content,run_id FROM conversations '
                            'WHERE session_id=? ORDER BY id', (session_id,)).fetchall()
        watermark = rows[-1][0] if rows else 0
        if previous and previous[1] == watermark:
            return json.loads(previous[2])
        users = [{'message_id': r[0], 'text': r[2][:2000], 'truncated': len(r[2]) > 2000}
                 for r in rows if r[1] == 'user']
        # Explicit constraints get their own preserved lane, not model reinterpretation.
        import re
        corrections = [u for u in users if re.search(
            r"\b(instead|actually|correction|don't|do not|must|always|never|prefer|want)\b", u['text'], re.I)]
        decisions = [u for u in users if re.search(
            r'\b(use|choose|chosen|keep|switch to|go with)\b', u['text'], re.I)]
        old = rows[:-24]
        extracts = [{'message_id': r[0], 'text': r[2][:600], 'truncated': len(r[2]) > 600}
                    for r in old if r[1] == 'user']
        summary = extracts[:4] + extracts[-16:] if len(extracts) > 20 else extracts
        value = {'version': (previous[0] if previous else 0) + 1,
                 'watermark': watermark, 'summary_watermark': old[-1][0] if old else 0,
                 'summary_kind': 'user_source_extracts', 'summary': summary,
                 'summary_omitted': max(0, len(extracts) - len(summary)),
                 'requirements': users[-16:], 'corrections': corrections[-24:],
                 'decisions': decisions[-16:],
                 'omitted_user_messages': max(0, len(users) - 16)}
        # Retain observed outcomes recorded independently of assistant wording.
        if previous:
            value['observations'] = json.loads(previous[2]).get('observations', [])
        else:
            value['observations'] = []
        conn.execute('INSERT INTO conversation_state VALUES (?,?,?,?) '
                     'ON CONFLICT(session_id) DO UPDATE SET version=excluded.version, '
                     'watermark=excluded.watermark,state=excluded.state',
                     (session_id, value['version'], watermark, json.dumps(value)))
        conn.commit()
        return value


def observe(session_id, run_id, status, steps):
    if not session_id:
        return
    # Append-only projection; a late background result cannot replace newer user facts.
    with store._conn() as conn:
        _init(conn)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT version,state FROM conversation_state WHERE session_id=?',
                           (session_id,)).fetchone()
        value = json.loads(row[1]) if row else {'version': 0, 'watermark': 0}
        observations = value.get('observations', [])
        observations = [o for o in observations if o.get('run_id') != run_id]
        observations.append({'run_id': run_id, 'status': status, 'tools': [
            {'tool': s.get('tool'), 'status': s.get('status'),
             'structured': s.get('structured'), 'summary': str(s.get('result', ''))[:1200]}
            for s in steps[-6:]]})
        value['observations'] = observations[-12:]
        value['version'] = value.get('version', 0) + 1
        conn.execute('INSERT INTO conversation_state VALUES (?,?,?,?) '
                     'ON CONFLICT(session_id) DO UPDATE SET version=excluded.version,state=excluded.state',
                     (session_id, value['version'], value.get('watermark', 0), json.dumps(value)))
        conn.commit()


def compile_context(session_id, prompt, recent, *, reply_context=None, work_context=''):
    from server import context_resolver
    current_id = store.current_turn_ids().get('user_message_id')
    history = [r for r in recent if r.get('id') != current_id]
    # For non-HTTP callers no turn context exists; remove only the last exact user row.
    if not current_id and history and history[-1]['role'] == 'user' and history[-1]['content'] == prompt:
        history = history[:-1]
    projection = {}
    failed = False
    if session_id and _enabled():
        try:
            projection = state(session_id)
        except Exception:
            failed = True  # Raw messages remain available; derived state is optional.
    from server.skills.shell import ShellSkill
    parts = [ShellSkill._format_context(history)]
    if projection:
        # Current request and recent raw messages occur once; their extracts add no context.
        recent_ids = {r.get('id') for r in recent}
        derived = {k: v for k, v in projection.items()}
        for key in ('requirements', 'corrections', 'decisions', 'summary'):
            derived[key] = [r for r in derived.get(key, []) if r['message_id'] not in recent_ids]
        parts.append('<conversation_state>\nHistorical user-source extracts; later corrections prevail. '
                     'Observed tool results are evidence; assistant prose is not. '
                     'Omitted/truncated sources can be retrieved by message ID.\n'
                     + json.dumps(derived, ensure_ascii=False) + '\n</conversation_state>')
    resolved = context_resolver.resolve(prompt, session_id, reply_context)
    parts.append(resolved)
    if work_context:
        work_context = work_context.replace('ORIGINAL REQUEST: ' + prompt, 'ORIGINAL REQUEST: [current request below]')
        work_context = work_context.replace(': ' + prompt + '\n', ': [current request below]\n')
        parts.append('<DURABLE_WORK>\n' + work_context + '\n</DURABLE_WORK>')
    text = '\n'.join(parts)
    # Retain bounded source sections with explicit omissions, never truncate the current request.
    cap = 100000
    truncated = len(text) > cap
    if truncated:
        text = text[:70000] + '\n[Context excerpt; retrieve full message sources.]\n' + text[-30000:]
    package = {'text': text, 'version': projection.get('version', 0),
               'source_message_ids': sorted({r['id'] for r in history if r.get('id')} |
                   {r['message_id'] for key in ('summary', 'requirements', 'corrections', 'decisions')
                    for r in projection.get(key, []) if r['message_id'] != current_id} |
                   ({reply_context['message_id']} if isinstance(reply_context, dict) and reply_context.get('message_id') else set())),
               'summary_watermark': projection.get('summary_watermark', 0),
               'truncated': truncated or any(len(r['content']) > 6000 for r in history),
               'projection_failed': failed, 'conversation_id': session_id}
    _package.set(package)
    return package


def current():
    return _package.get()


def diagnostics(package=None):
    value = package or current() or {}
    return {k: v for k, v in value.items() if k != 'text'}
