"""Uncommitted changes in a project, structured for the phone's review screen.

Read-only: it only runs `git diff`/`git status`-class commands, with optional
locks disabled so it never contends with a CLI agent working in the same repo.
Untracked files are included as new files, because "what did the agent just
write" is usually exactly those.
"""

import json
import os
import re
import subprocess
from pathlib import Path

MAX_FILE_BYTES = 60_000      # per-file patch shown on the phone
MAX_TOTAL_BYTES = 400_000    # whole response, as serialized JSON
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")


class NotARepo(Exception):
    pass


def _git(repo: Path, *args: str, ok=(0,)) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        timeout=20, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        errors="replace")
    if proc.returncode not in ok:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:300])
    return proc.stdout


def parse_unified(text: str, max_file_bytes: int = MAX_FILE_BYTES) -> list[dict]:
    """`git diff` output → one dict per file with numbered lines."""
    files: list[dict] = []
    for chunk in re.split(r"(?m)^(?=diff --git )", text):
        if not chunk.startswith("diff --git "):
            continue
        header, _, _ = chunk.partition("\n@@")
        m = re.match(r"diff --git a/(.+?) b/(.+)$", chunk.splitlines()[0])
        old_path, new_path = (m.group(1), m.group(2)) if m else ("", "")
        status = "M"
        if "\nnew file mode" in header or "--- /dev/null" in header:
            status = "A"
        elif "\ndeleted file mode" in header or "+++ /dev/null" in header:
            status = "D"
        elif "\nrename from " in header:
            status = "R"
        f = {
            "path": old_path if status == "D" else new_path,
            "old_path": old_path if status == "R" else None,
            "status": status,
            "binary": bool(re.search(r"(?m)^Binary files .* differ$", header)),
            "additions": 0, "deletions": 0, "hunks": [],
            "truncated": len(chunk.encode()) > max_file_bytes,
            "patch": chunk[:max_file_bytes],
        }
        hunk = None
        old_no = new_no = 0
        used = 0
        for line in chunk.splitlines():
            h = _HUNK.match(line)
            if h:
                old_no, new_no = int(h.group(1)), int(h.group(2))
                hunk = {"header": line, "lines": []}
                f["hunks"].append(hunk)
                continue
            if hunk is None or line.startswith("\\"):
                continue
            kind = line[:1]
            if kind == "+":
                f["additions"] += 1
                entry = {"t": "+", "old": None, "new": new_no}
                new_no += 1
            elif kind == "-":
                f["deletions"] += 1
                entry = {"t": "-", "old": old_no, "new": None}
                old_no += 1
            else:
                entry = {"t": " ", "old": old_no, "new": new_no}
                old_no += 1
                new_no += 1
            used += len(line) + 40  # ~JSON overhead of one numbered line
            if used <= max_file_bytes:
                entry["text"] = line[1:]
                hunk["lines"].append(entry)
        files.append(f)
    return files


def _untracked(repo: Path) -> str:
    """Untracked, non-ignored files rendered as "new file" diffs."""
    out = []
    names = [n for n in _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
             .split("\0") if n]
    for name in names:
        out.append(_git(repo, "diff", "--no-color", "--no-index", "--",
                        "/dev/null", name, ok=(0, 1)))
    return "".join(out)


def collect(repo: Path, *, max_file_bytes: int = MAX_FILE_BYTES,
            max_total_bytes: int = MAX_TOTAL_BYTES) -> dict:
    repo = Path(repo)
    try:
        _git(repo, "rev-parse", "--is-inside-work-tree")
    except (RuntimeError, OSError):
        raise NotARepo(f"{repo.name} is not a Git repository")
    try:
        head = _git(repo, "rev-parse", "--short", "HEAD").strip()
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        tracked = _git(repo, "diff", "HEAD", "--no-color", "--no-ext-diff", "-M")
    except RuntimeError:  # a repository with no commits yet
        head, branch = None, None
        tracked = _git(repo, "diff", "--cached", "--no-color", "--no-ext-diff")
    files = parse_unified(tracked + _untracked(repo), max_file_bytes)

    kept, used, omitted = [], 0, 0
    for f in files:
        # Measure what is actually sent: the numbered lines roughly double the
        # raw patch, so budgeting on the patch alone overshot by 3x.
        size = len(json.dumps(f).encode())
        if used + size > max_total_bytes:
            omitted += 1
            continue
        used += size
        kept.append(f)
    return {
        "project": repo.name,
        "branch": branch,
        "head": head,
        "files": kept,
        "omitted_files": omitted,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }
