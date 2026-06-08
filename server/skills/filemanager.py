from pathlib import Path
from server.skills.base import Skill
from server.skills import register
from server import config


class FileManagerSkill(Skill):
    name = "filemanager"
    description = "List a directory or read a file.  Usage: /files <path>"

    async def run(self, prompt: str = "", **kwargs) -> str:
        raw = prompt.strip()

        # Optional explicit action prefix: "list ~/foo" or "read ~/foo/bar.py"
        action = "auto"
        if raw.startswith("list "):
            action, raw = "list", raw[5:].strip()
        elif raw.startswith("read "):
            action, raw = "read", raw[5:].strip()

        if not raw:
            raw = str(config.WORKSPACE_DIR)

        path = Path(raw).expanduser().resolve()

        if not path.exists():
            return f"Path not found: {path}"

        if action == "list" or (action == "auto" and path.is_dir()):
            return self._list(path)
        return self._read(path)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _list(self, path: Path) -> str:
        if not path.is_dir():
            return self._read(path)

        try:
            entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            return f"Permission denied: {path}"

        lines = [f"{path}/", ""]
        for e in entries:
            if e.is_dir():
                lines.append(f"  [DIR]  {e.name}/")
            else:
                lines.append(f"  [FILE] {e.name}  ({self._fmt(e.stat().st_size)})")

        lines.append(f"\n{sum(1 for e in path.iterdir() if e.is_dir())} dirs, "
                     f"{sum(1 for e in path.iterdir() if e.is_file())} files")
        return "\n".join(lines)

    def _read(self, path: Path) -> str:
        if not path.is_file():
            return f"Not a file: {path}"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            return f"Permission denied: {path}"
        except Exception as exc:
            return f"Error reading {path.name}: {exc}"

        lines = text.splitlines()
        preview = lines[:150]
        suffix = f"\n\n... ({len(lines) - 150} more lines)" if len(lines) > 150 else ""
        return f"=== {path} ===\n" + "\n".join(preview) + suffix

    @staticmethod
    def _fmt(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}"
            size //= 1024
        return f"{size:.0f} TB"


register(FileManagerSkill())
