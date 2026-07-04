"""
Shared media helpers: where uploaded/produced images live, and the sandbox
guard for serving files back to the app.

Images flow two ways:
  • inbound  — the phone uploads a screenshot; /api/upload saves it here and the
    agent reads it (via the claude tool) from the returned path.
  • outbound — a skill produces an image (e.g. /mac screenshot) into this dir and
    emits an "[image: <path>]" marker; the app fetches it via /api/file.

Both live under UPLOADS_DIR so /api/file can serve them without exposing the
whole filesystem. is_served_path() is the allow-list the file endpoint enforces.
"""

from pathlib import Path

from server import config

UPLOADS_DIR = Path.home() / ".codeasachat" / "uploads"


def ensure_uploads_dir() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR


def is_served_path(p: Path) -> bool:
    """True if `p` is safe to serve to the app: a real file inside the uploads
    dir or the active workspace. Resolves symlinks/.. first so a crafted path
    can't escape the allow-listed roots."""
    try:
        rp = p.resolve()
    except OSError:
        return False
    if not rp.is_file():
        return False
    roots = [UPLOADS_DIR.resolve()]
    try:
        roots.append(Path(config.WORKSPACE_DIR).resolve())
    except OSError:
        pass
    return any(rp == root or root in rp.parents for root in roots)
