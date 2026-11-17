"""
Maps (workspace dir, engine) → the CLI session id to resume.

So repeated claude/codex/gemini calls inside a project **continue one session**
instead of spawning a fresh one-shot every time — real continuity, and far less
clutter in ~/.claude / ~/.codex. The stored id is also what you'd hand to
`claude --resume <id>` on the Mac to pick the thread up there.

Granularity is (workspace, engine): the project pins one engine, and that engine
has one active thread. Stored at ~/.codeasachat/cli_sessions.db.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path.home() / ".codeasachat" / "cli_sessions.db"


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


def _init() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS cli_sessions (
                workspace   TEXT NOT NULL,
                engine      TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                updated_at  REAL NOT NULL,
                PRIMARY KEY (workspace, engine)
            )
        """)
        # An explicitly chosen session ("continue session <id>"). Kept apart
        # from the pointer above because that one is overwritten whenever the
        # folder's newest native session is reconciled.
        c.execute("""
            CREATE TABLE IF NOT EXISTS cli_session_pins (
                workspace   TEXT NOT NULL,
                engine      TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                PRIMARY KEY (workspace, engine)
            )
        """)
        c.commit()


_init()


def pin(workspace: str, engine: str, session_id: str) -> None:
    """Make the next run in this folder resume exactly this session, even if
    it is not the folder's newest."""
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO cli_session_pins (workspace, engine, session_id) "
            "VALUES (?, ?, ?)", (workspace, engine, session_id))
        c.commit()


def pinned(workspace: str, engine: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT session_id FROM cli_session_pins WHERE workspace = ? AND engine = ?",
            (workspace, engine)).fetchone()
    return row["session_id"] if row else None


def unpin(workspace: str, engine: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM cli_session_pins WHERE workspace = ? AND engine = ?",
                  (workspace, engine))
        c.commit()


def get(workspace: str, engine: str) -> str | None:
    with _conn() as c:
        row = c.execute(
            "SELECT session_id FROM cli_sessions WHERE workspace = ? AND engine = ?",
            (workspace, engine),
        ).fetchone()
    return row["session_id"] if row else None


def set(workspace: str, engine: str, session_id: str) -> None:
    if not session_id:
        return
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO cli_sessions (workspace, engine, session_id, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(workspace, engine) DO UPDATE SET "
            "session_id=excluded.session_id, updated_at=excluded.updated_at",
            (workspace, engine, session_id, now),
        )
        c.commit()


def clear(workspace: str, engine: str) -> None:
    """Drop a mapping — used when a resume fails (the stored session is gone),
    so the next call starts fresh instead of retrying a dead id forever."""
    with _conn() as c:
        c.execute(
            "DELETE FROM cli_sessions WHERE workspace = ? AND engine = ?",
            (workspace, engine),
        )
        c.commit()


def all_for(workspace: str) -> dict[str, str]:
    """{engine: session_id} for a workspace — for surfacing 'continue on Mac'."""
    with _conn() as c:
        rows = c.execute(
            "SELECT engine, session_id FROM cli_sessions WHERE workspace = ?",
            (workspace,),
        ).fetchall()
    return {r["engine"]: r["session_id"] for r in rows}


class SessionOwned(ValueError):
    def __init__(self, owner):
        self.owner = owner
        super().__init__('This native session belongs to another conversation. '
                         'Open that conversation or start a fresh session.')


def _conversation_init(c):
    c.execute('''CREATE TABLE IF NOT EXISTS conversation_cli_sessions (
        conversation_id TEXT NOT NULL, workspace TEXT NOT NULL, engine TEXT NOT NULL,
        session_id TEXT NOT NULL, updated_at REAL NOT NULL,
        PRIMARY KEY(conversation_id, workspace, engine),
        UNIQUE(engine, session_id))''')


def conversation_get(conversation_id, workspace, engine):
    with _conn() as c:
        _conversation_init(c)
        row = c.execute('SELECT session_id FROM conversation_cli_sessions '
                        'WHERE conversation_id=? AND workspace=? AND engine=?',
                        (conversation_id, str(Path(workspace).resolve()), engine)).fetchone()
    return row['session_id'] if row else None


def conversation_owner(engine, session_id):
    with _conn() as c:
        _conversation_init(c)
        row = c.execute('SELECT conversation_id FROM conversation_cli_sessions '
                        'WHERE engine=? AND session_id=?', (engine, session_id)).fetchone()
    return row['conversation_id'] if row else None


def conversation_set(conversation_id, workspace, engine, session_id):
    if not session_id:
        return
    with _conn() as c:
        _conversation_init(c)
        c.execute('BEGIN IMMEDIATE')
        owner = c.execute('SELECT conversation_id FROM conversation_cli_sessions '
                          'WHERE engine=? AND session_id=?', (engine, session_id)).fetchone()
        if owner and owner['conversation_id'] != conversation_id:
            raise SessionOwned(owner['conversation_id'])
        c.execute('INSERT INTO conversation_cli_sessions VALUES (?,?,?,?,?) '
                  'ON CONFLICT(conversation_id,workspace,engine) DO UPDATE SET '
                  'session_id=excluded.session_id,updated_at=excluded.updated_at',
                  (conversation_id, str(Path(workspace).resolve()), engine, session_id, time.time()))
        c.commit()


def conversation_clear(conversation_id, workspace, engine):
    with _conn() as c:
        _conversation_init(c)
        c.execute('DELETE FROM conversation_cli_sessions WHERE conversation_id=? '
                  'AND workspace=? AND engine=?',
                  (conversation_id, str(Path(workspace).resolve()), engine))
        c.commit()
