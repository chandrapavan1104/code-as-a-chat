"""
Reads each CLI's OWN session store to find the latest session for a project.

Why this exists: `cli_sessions_store` is a server-side pointer, and only the
server writes to it. When you run `claude` (or codex/gemini) yourself in a
project on the Mac, the CLI opens a session the server never hears about — so
the next Gajala turn resumes the server's older id and the thread forks. Reading
the CLI's own store makes the Mac's usage visible, so both sides can converge on
one session per (project, engine).

Layouts (verified on this host):
  claude  ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl   id = filename
          The dir encoding is version-unstable (dark-mamba vs dark_mamba), so we
          match the authoritative "cwd" field inside the file instead. Claude
          records the REALPATH (/private/tmp/x for /tmp/x).
  codex   ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
          id + cwd in the first line's session_meta.payload.
  gemini  ~/.gemini/tmp/<slug>/chats/session-*.jsonl
          first line has sessionId + projectHash = sha256(abs project path).

Cost control: files are walked newest-first and we stop at the first match — the
answer is almost always the first file or two. SCAN_CAP bounds the worst case.
"""

import hashlib
import json
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
GEMINI_TMP = Path.home() / ".gemini" / "tmp"

# Most files we'll stat/read before giving up (newest-first, so a miss this deep
# means there is no recent session for this project anyway).
SCAN_CAP = 300
# Claude writes `cwd` a few events in (queue-operation lines come first).
HEAD_LINES = 60


def _real(path: str) -> str:
    """Canonical form for comparing paths — CLIs record resolved realpaths."""
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return str(path)


def _by_mtime(paths) -> list[tuple[Path, float]]:
    """(path, mtime) newest-first, skipping anything we can't stat."""
    out = []
    for p in paths:
        try:
            out.append((p, p.stat().st_mtime))
        except OSError:
            continue
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _head_lines(path: Path, limit: int = HEAD_LINES):
    """Yield the first `limit` parsed JSON objects — streamed, so a 30MB session
    file costs the same as a small one."""
    try:
        with open(path, "r", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= limit:
                    return
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        return


# ── per-engine lookups ────────────────────────────────────────────────────────

def _latest_claude(workspace: str) -> tuple[str, float] | None:
    if not CLAUDE_PROJECTS.exists():
        return None
    target = _real(workspace)
    for path, mtime in _by_mtime(CLAUDE_PROJECTS.glob("*/*.jsonl"))[:SCAN_CAP]:
        for ev in _head_lines(path):
            cwd = ev.get("cwd")
            if not cwd:
                continue
            if _real(cwd) == target:
                return path.stem, mtime
            break   # cwd found but different project — next file
    return None


def _latest_codex(workspace: str) -> tuple[str, float] | None:
    if not CODEX_SESSIONS.exists():
        return None
    target = _real(workspace)
    for path, mtime in _by_mtime(CODEX_SESSIONS.rglob("rollout-*.jsonl"))[:SCAN_CAP]:
        for ev in _head_lines(path, limit=5):
            if ev.get("type") != "session_meta":
                continue
            payload = ev.get("payload") or {}
            cwd, sid = payload.get("cwd"), payload.get("id")
            if cwd and sid and _real(cwd) == target:
                return sid, mtime
            break
    return None


def _latest_gemini(workspace: str) -> tuple[str, float] | None:
    if not GEMINI_TMP.exists():
        return None
    want = hashlib.sha256(_real(workspace).encode()).hexdigest()
    for path, mtime in _by_mtime(GEMINI_TMP.glob("*/chats/*.jsonl"))[:SCAN_CAP]:
        for ev in _head_lines(path, limit=3):
            sid = ev.get("sessionId")
            if sid and ev.get("projectHash") == want:
                return sid, mtime
            break
    return None


_LOOKUPS = {
    "claude": _latest_claude,
    "codex": _latest_codex,
    "gemini": _latest_gemini,
}


def latest(workspace: str, engine: str) -> tuple[str, float] | None:
    """(session_id, mtime) of the newest native session for this project+engine,
    or None. Best-effort: never raises — a lookup failure just means we fall back
    to the server's own pointer."""
    fn = _LOOKUPS.get(engine)
    if fn is None:
        return None
    try:
        return fn(workspace)
    except Exception:
        return None
