"""
The single authority on "which project is this turn running in".

Before this module the answer lived in one global (`config.WORKSPACE_DIR`) that
both the phone (`POST /api/projects/switch`) and the agent (`projects switch`)
mutated freely. Nothing bound a *turn* to a project, so a multi-step turn could
— and did — switch, switch back, and switch again while its own routing hint
still described the directory it started in.

The model here is: **the thread owns the project.**

  * Every turn resolves its project ONCE, at the start, and runs inside
    `bound(path)` — a ContextVar, so concurrent turns (phone + Telegram + Night
    Shift) never read each other's workspace.
  * A turn may change that binding AT MOST ONCE, via `rebind()`, and the change
    is irreversible for the rest of the turn. That is what makes the switch /
    switch-back thrash structurally impossible rather than merely discouraged.
  * `config.WORKSPACE_DIR` survives only as the *default* for new threads and
    for headless callers that supply no project at all.

`active()` is the accessor every skill should use. Reading
`config.WORKSPACE_DIR` directly is now a bug: it sees the default, not the
turn's project.
"""

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from server import config


# The project this turn is bound to. Unset (None) outside a turn — then `active()`
# falls back to the persisted default, which is what Telegram and one-shot CLI
# callers want.
_bound: ContextVar[Path | None] = ContextVar("workspace_bound", default=None)
# Set once per turn when the agent switches project mid-turn, so a second switch
# in the same turn can be refused instead of thrashing.
_rebound: ContextVar[Path | None] = ContextVar("workspace_rebound", default=None)


# ── the accessor ──────────────────────────────────────────────────────────────

def active() -> Path:
    """The project this turn runs in. Use this everywhere instead of
    `config.WORKSPACE_DIR`."""
    return _bound.get() or config.WORKSPACE_DIR


def name() -> str:
    """Short name of the active project (what the app shows in its header)."""
    return active().name


@contextmanager
def bound(path: Path | str | None) -> Iterator[Path]:
    """Bind one turn to a project. Restores the previous binding on exit, so
    nested/concurrent turns can't leak into each other."""
    target = Path(path).expanduser() if path else active()
    token = _bound.set(target)
    rebind_token = _rebound.set(None)
    try:
        yield target
    finally:
        _bound.reset(token)
        _rebound.reset(rebind_token)


def rebind(path: Path | str) -> tuple[bool, str]:
    """The one workspace change a turn is allowed to make.

    Returns (changed, reason). A second attempt within the same turn is refused
    — that refusal is the whole point of the invariant, so the caller should
    surface it rather than treating it as an error.
    """
    target = Path(path).expanduser()
    current = active()
    if target.resolve() == current.resolve():
        return False, "already"
    already = _rebound.get()
    if already is not None:
        return False, f"locked:{already.name}"
    _bound.set(target)
    _rebound.set(target)
    return True, "switched"


def rebound_to() -> Path | None:
    """The project this turn switched to, if it switched at all."""
    return _rebound.get()


# ── discovery + resolution ────────────────────────────────────────────────────

def parent_dir() -> Path:
    """Where projects live. PROJECTS_PARENT_DIR, else ~/Projects."""
    env = os.getenv("PROJECTS_PARENT_DIR", "")
    return Path(env).expanduser() if env else Path.home() / "Projects"


def candidates() -> list[Path]:
    """Every candidate project directory, alphabetical."""
    parent = parent_dir()
    if not parent.is_dir():
        return []
    return sorted(
        (p for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )


def resolve(target: str) -> Path | None:
    """Match a name / substring / path to a project directory.

    Exact name → unique substring → slug match (so a session-id slug like
    "code-as-a-chat" finds "Code-as-a-chat") → explicit path.
    """
    if not target or not target.strip():
        return None
    target = target.strip().strip("'\"")

    if "/" in target or target.startswith("~"):
        p = Path(target).expanduser().resolve()
        return p if p.is_dir() else None

    cands = candidates()
    low = target.lower()

    for c in cands:
        if c.name.lower() == low:
            return c

    matches = [c for c in cands if low in c.name.lower()]
    if len(matches) == 1:
        return matches[0]

    wanted = slug(target)
    slugged = [c for c in cands if slug(c.name) == wanted]
    if len(slugged) == 1:
        return slugged[0]
    return None


def suggestions(target: str, limit: int = 6) -> list[str]:
    """Closest project names for a failed match — so a bad switch can say what
    the user probably meant instead of silently doing nothing."""
    cands = [c.name for c in candidates()]
    low = (target or "").lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", low) if len(t) > 2]
    scored: list[tuple[int, str]] = []
    for cname in cands:
        cl = cname.lower()
        score = sum(1 for t in tokens if t in cl)
        if low and low in cl:
            score += 2
        if score:
            scored.append((score, cname))
    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    return [n for _, n in scored[:limit]] or cands[:limit]


# ── session-id ↔ project ──────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    """The app's directory slug. Must stay identical to `_sidFor` in
    clients/gajala/lib/screens/chat_screen.dart, which builds a session id as
    "<install>::<slug>" — that suffix is how the server recovers a thread's
    project when the client is too old to send one explicitly."""
    return _SLUG_RE.sub("-", (value or "").lower()).strip("-")


def for_session(session_id: str | None) -> Path | None:
    """Recover a thread's project from its session id, if it encodes one."""
    if not session_id or "::" not in session_id:
        return None
    suffix = session_id.rsplit("::", 1)[1].strip()
    if not suffix:
        return None
    return resolve(suffix)


def for_turn(project: str | None, session_id: str | None) -> Path:
    """Resolve the project a turn should run in.

    Order: an explicit project from the client → the project encoded in the
    session id → the persisted default. Deliberately never raises: an unknown
    project falls back rather than failing a chat turn, and the reply's trace
    records where it actually ran.
    """
    if project and project.strip():
        hit = resolve(project)
        if hit:
            return hit
    hit = for_session(session_id)
    if hit:
        return hit
    return config.WORKSPACE_DIR


# ── description (what the phone shows) ────────────────────────────────────────

def prettify(path: Path) -> str:
    """~-relative path — what a human recognises at a glance."""
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home) else s


def _git_head(git_dir: Path) -> str | None:
    """Current branch from .git/HEAD, read as a file. Avoids ~60 subprocess
    spawns when the phone lists a directory of 30 projects."""
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        return head.split("/", 2)[-1] if "/" in head else head[4:].strip()
    return head[:8] or None      # detached HEAD → short sha


def _git_remote(git_dir: Path) -> str | None:
    """origin's URL, condensed to owner/repo, straight out of .git/config."""
    try:
        text = (git_dir / "config").read_text()
    except OSError:
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text, re.DOTALL)
    if not m:
        return None
    url = m.group(1).removesuffix(".git")
    parts = [p for p in re.split(r"[:/]", url) if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else url


_CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def describe(path: Path, *, is_active: bool = False) -> dict:
    """Everything the Projects list needs to be honest about a directory: where
    it actually is on disk, whether it's a repo, and how stale it is."""
    git_dir = path / ".git"
    is_git = git_dir.is_dir()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    return {
        "name": path.name,
        "path": str(path),
        "display_path": prettify(path),
        "active": is_active,
        "is_git": is_git,
        "branch": _git_head(git_dir) if is_git else None,
        "remote": _git_remote(git_dir) if is_git else None,
        "last_modified": mtime,
        "has_context": any((path / f).exists() for f in _CONTEXT_FILES),
    }


def describe_all() -> list[dict]:
    """Every candidate project, with the active one flagged."""
    current = active().resolve()
    out = []
    for c in candidates():
        try:
            is_active = c.resolve() == current
        except OSError:
            is_active = False
        out.append(describe(c, is_active=is_active))
    return out


# ── persistence (only for user-initiated switches) ────────────────────────────

STATE_FILE = Path.home() / ".codeasachat" / "state.json"


def persist_default(path: Path) -> None:
    """Make `path` the default for NEW threads. Only an explicit user action
    (the Projects screen, `/projects switch` from Telegram) should call this —
    an agent's mid-turn rebind must not repoint everyone else's default."""
    import json
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    state["workspace_dir"] = str(path)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    config.WORKSPACE_DIR = path
