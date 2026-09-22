from pathlib import Path
import uuid

from server.skills.base import Skill, SkillResult
from server.skills import register
from server import workspace
from server.media import ensure_uploads_dir


MAX_SHARE_BYTES = 200 * 1024 * 1024


class FileManagerSkill(Skill):
    name = "filemanager"
    command = "files"
    description = "List, read, or share a file. Usage: /files [list|read|share] <path>"
    final_output = True
    agent_doc = ('Lists directories, reads text files, or shares a Mac file to the phone. '
                 'Use args="share <path>" for any readable regular file (including PDFs, '
                 'images, archives, and code); the result includes a downloadable file marker. '
                 'For code snippets, preserve the content inside fenced ``` blocks.')

    async def run(self, prompt: str = "", **kwargs) -> str:
        raw = prompt.strip()

        # Optional explicit action prefix: "list ~/foo" or "read ~/foo/bar.py"
        action = "auto"
        if raw.startswith("list "):
            action, raw = "list", raw[5:].strip()
        elif raw.startswith("read "):
            action, raw = "read", raw[5:].strip()
        elif raw.startswith("share "):
            action, raw = "share", raw[6:].strip()

        if not raw:
            raw = str(workspace.active())

        path = self._resolve_path(raw)

        if not path.exists():
            return f"Path not found: {path}"

        if action == "share":
            return self._share(path)

        if action == "list" or (action == "auto" and path.is_dir()):
            return self._list(path)
        return self._read(path)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_path(raw: str) -> Path:
        """Resolve relative paths inside the active workspace, not the server
        repo. A bare known project name resolves to that project as a fallback,
        which keeps a just-created project usable during a workspace handoff."""
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path.resolve()

        in_workspace = (workspace.active() / path).resolve()
        if in_workspace.exists():
            return in_workspace

        if len(path.parts) == 1:
            try:
                from server.skills.projects import _resolve as resolve_project
                project = resolve_project(raw)
                if project is not None:
                    return project.resolve()
            except Exception:
                pass
        return in_workspace

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
            with path.open("rb") as stream:
                sample = stream.read(8192)
            if b"\x00" in sample:
                return (f"Binary file: {path} ({self._fmt(path.stat().st_size)}). "
                        "Use /files share <path> to download it to the phone.")
            text = path.read_text(encoding="utf-8")
        except PermissionError:
            return f"Permission denied: {path}"
        except UnicodeDecodeError:
            return (f"Binary file: {path} ({self._fmt(path.stat().st_size)}). "
                    "Use /files share <path> to download it to the phone.")
        except Exception as exc:
            return f"Error reading {path.name}: {exc}"

        lines = text.splitlines()
        preview = lines[:150]
        suffix = f"\n\n... ({len(lines) - 150} more lines)" if len(lines) > 150 else ""
        return f"=== {path} ===\n" + "\n".join(preview) + suffix

    @staticmethod
    def _share(path: Path) -> SkillResult:
        if not path.is_file():
            return SkillResult("failed", f"Cannot share: not a regular file: {path}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            return SkillResult("failed", f"Cannot inspect {path}: {exc}")
        if size > MAX_SHARE_BYTES:
            return SkillResult(
                "failed",
                f"File is too large to share ({FileManagerSkill._fmt(size)}; max 200 MB).",
            )
        target_dir = None
        try:
            target_dir = ensure_uploads_dir() / "shared" / uuid.uuid4().hex
            target_dir.mkdir(parents=True, exist_ok=False)
            # Marker syntax uses ] and a single line; retain the original
            # basename in metadata while making the staged filename safe.
            safe_name = path.name.replace("]", "_").replace("\n", "_").replace("\r", "_")
            target = target_dir / (safe_name or "shared-file")
            copied = 0
            with path.open("rb") as source, target.open("wb") as dest:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > MAX_SHARE_BYTES:
                        target.unlink(missing_ok=True)
                        target_dir.rmdir()
                        return SkillResult("failed", "File grew beyond the 200 MB share limit.")
                    dest.write(chunk)
        except PermissionError:
            if target_dir is not None:
                import shutil
                shutil.rmtree(target_dir, ignore_errors=True)
            return SkillResult("failed", f"Permission denied: {path}")
        except OSError as exc:
            if target_dir is not None:
                import shutil
                shutil.rmtree(target_dir, ignore_errors=True)
            return SkillResult("failed", f"Could not stage {path.name}: {exc}")
        marker = f"[file: {target}]"
        return SkillResult(
            "succeeded",
            f"Shared {path.name} ({FileManagerSkill._fmt(copied)}). {marker}",
            data={"path": str(target), "name": path.name, "size": copied,
                  "source": str(path)},
            evidence=[str(target)],
        )

    @staticmethod
    def _fmt(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}"
            size //= 1024
        return f"{size:.0f} TB"


register(FileManagerSkill())
