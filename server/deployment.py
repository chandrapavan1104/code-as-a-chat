"""Serialized merge + deploy entry point shared by every agent surface."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from server.db import deployment_store, night_queue_store

_DEPLOY_WORKTREE_DIR = Path.home() / ".codeasachat" / "deploy_worktrees"
_SERVER_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.codeasachat.server.plist"


def _git(repo: str, *args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=True, timeout=timeout)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return 124, "git operation timed out"


def _server_runtime(repo: str) -> str:
    """Current launchd source checkout, falling back to the owner repo."""
    try:
        with _SERVER_PLIST.open("rb") as handle:
            return str(plistlib.load(handle).get("WorkingDirectory") or repo)
    except (OSError, plistlib.InvalidFileException):
        return repo


def _discard_worktree(repo: str, path: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(path))
    _git(repo, "worktree", "prune")


def _prepare_merge_worktree(repo: str, deployment_id: int,
                            base_sha: str) -> tuple[Path | None, str]:
    """Create a detached merge checkout; the owner's checkout is never read."""
    _DEPLOY_WORKTREE_DIR.mkdir(parents=True, exist_ok=True)
    path = _DEPLOY_WORKTREE_DIR / f"deploy-{deployment_id}"
    _discard_worktree(repo, path)
    rc, output = _git(repo, "worktree", "add", "--detach", str(path), base_sha)
    return (path, "") if rc == 0 else (None, output[-500:].strip())


def _finish_failed(deployment_id: int, detail: str, ref_id: int | None) -> str:
    deployment_store.update(deployment_id, state="failed", detail=detail)
    if ref_id is not None:
        night_queue_store.update(ref_id, status="staged", summary=detail)
    return f"Deployment stopped safely: {detail}"


def deployed_base(repo: str, base: str) -> str:
    """Verified base commit without consulting/mutating the owner's checkout."""
    live = deployment_store.latest_live(repo, base)
    if live and live.get("deployed_sha"):
        return live["deployed_sha"]
    return _git(repo, "rev-parse", f"refs/heads/{base}")[1].strip()


def deploy_branch(*, repo: str, branch: str, base: str, changed_files: list[str],
                  source: str, ref_id: int | None = None,
                  server_touched: bool = False) -> str:
    """Merge and either finish immediately or hand restart to the detached guard."""
    deployment, busy = deployment_store.begin(
        repo=repo, branch=branch, base=base, source=source, ref_id=ref_id,
        server_touched=server_touched, changed_files=changed_files)
    if deployment is None:
        return f"Deployment queued behind the active release: {busy}. Try again shortly."
    did = deployment["id"]

    before = deployed_base(repo, base)
    if not before:
        return _finish_failed(did, f"base branch '{base}' does not exist", ref_id)
    before = before.strip()
    if _git(repo, "rev-parse", "--verify", branch)[0] != 0:
        return _finish_failed(did, f"staged branch '{branch}' does not exist", ref_id)

    worktree, error = _prepare_merge_worktree(repo, did, before)
    if worktree is None:
        return _finish_failed(did, f"could not create isolated merge checkout: {error}",
                              ref_id)
    rc, out = _git(str(worktree), "merge", "--no-ff", "-m",
                   f"deploy {source} {ref_id or ''}".strip(), branch)
    if rc != 0:
        _git(str(worktree), "merge", "--abort")
        _discard_worktree(repo, worktree)
        return _finish_failed(did, f"merge conflict: {out[-500:].strip()}", ref_id)
    deployed_sha = _git(str(worktree), "rev-parse", "HEAD")[1].strip()
    previous_runtime = _server_runtime(repo) if server_touched else None
    deployment_store.update(did, before_sha=before, deployed_sha=deployed_sha,
                            runtime_path=str(worktree),
                            previous_runtime=previous_runtime,
                            detail="isolated merge completed")
    if ref_id is not None:
        night_queue_store.update(ref_id, status="deploying",
                                 summary=f"Deployment #{did}: merged; verifying service.")

    if not server_touched:
        push_rc, push_out = _git(repo, "push", "origin",
                                 f"{deployed_sha}:refs/heads/{base}")
        detail = "merged and pushed" if push_rc == 0 else f"merged; push failed: {push_out[-300:]}"
        deployment_store.update(did, state="live", detail=detail)
        if ref_id is not None:
            night_queue_store.update(ref_id, status="shipped", summary=detail)
        _discard_worktree(repo, worktree)
        return f"Deployment #{did} is live: {detail}."

    try:
        subprocess.Popen(
            [sys.executable, "-m", "server.deployment_guard", str(did)],
            cwd=worktree, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONPATH": str(worktree)},
        )
    except Exception as exc:
        _discard_worktree(repo, worktree)
        return _finish_failed(did, f"could not launch deployment guard: {exc}", ref_id)
    return (f"Deployment #{did} merged. Restart, authenticated API checks, "
            "Tailscale verification, push, and safe rollback now run automatically.")
