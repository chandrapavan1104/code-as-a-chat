"""Local full-text indexing with exact client scope and original-source retrieval."""
from server.db import store
import re
import sqlite3


def search(session_id, query, limit=10, all_conversations=False):
    terms = re.findall(r'[\w-]+', query)[:12]
    if not terms:
        return []
    with store._conn() as c:
        try:
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='message_fts'").fetchone()
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(content, content='conversations', content_rowid='id')")
            c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON conversations BEGIN INSERT INTO message_fts(rowid,content) VALUES(new.id,new.content); END")
            c.execute("CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON conversations BEGIN INSERT INTO message_fts(message_fts,rowid,content) VALUES('delete',old.id,old.content); END")
            if not exists:
                c.execute("INSERT INTO message_fts(message_fts) VALUES('rebuild')")
            match = ' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
            owner = store.client_scope(session_id)
            escaped = owner.replace('\\', '\\\\').replace('%','\\%').replace('_','\\_')
            scope = '(m.session_id = ? OR m.session_id LIKE ? ESCAPE \'\\\' OR m.session_id LIKE ? ESCAPE \'\\\')' if all_conversations else 'm.session_id = ?'
            parameters = [owner, escaped+'::%', escaped+':chat:%'] if all_conversations else [session_id]
            rows = c.execute('SELECT m.id,m.session_id,m.role,m.content,m.ts,m.run_id FROM message_fts '
                             'JOIN conversations m ON m.id=message_fts.rowid WHERE message_fts MATCH ? AND '+scope+
                             ' ORDER BY rank LIMIT ?', [match, *parameters, limit]).fetchall()
            c.commit()
        except sqlite3.OperationalError:
            return store.search(session_id, query, limit=limit, all_client=all_conversations)
    owner = store.client_scope(session_id)
    return [{'id': r[0], 'session_id': r[1], 'role': r[2], 'content': r[3], 'ts': r[4], 'run_id': r[5]}
            for r in rows if (store.client_scope(r[1]) == owner if all_conversations else r[1] == session_id)][:limit]
