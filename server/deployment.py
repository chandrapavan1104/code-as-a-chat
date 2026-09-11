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


def _preserve_commit(repo: str, deployment_id: int, commit: str) -> None:
    """Keep detached deployment commits reachable until ledger maintenance."""
    _git(repo, "update-ref", f"refs/codeasachat/deployments/{deployment_id}", commit)


def _sync_remote_base(repo: str, worktree: Path, base: str) -> tuple[bool, str]:
    """Merge the newest remote base without touching the owner's checkout."""
    _git(repo, "fetch", "origin", base, timeout=120)
    remote = f"refs/remotes/origin/{base}"
    if _git(repo, "rev-parse", "--verify", remote)[0] != 0:
        return True, ""
    if _git(str(worktree), "merge-base", "--is-ancestor", remote, "HEAD")[0] == 0:
        return True, ""
    rc, out = _git(str(worktree), "merge", "--no-ff", "-m",
                   f"sync origin/{base} before deployment", remote)
    return rc == 0, out[-500:].strip()


def _apply_job_delta(repo: str, worktree: Path, source_base: str,
                     branch: str, changed_files: list[str]) -> tuple[bool, str]:
    """Replay only a job's own net change, excluding its stale base ancestry."""
    try:
        # Agents maintain these mirrored context files after meaningful work.
        # When a job also has real implementation files, the historical context
        # edit is metadata—not a reason to reject otherwise clean code replay.
        context_files = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}
        product_files = [path for path in changed_files if path not in context_files]
        paths = product_files or changed_files
        diff = subprocess.run(
            ["git", "-C", repo, "diff", "--binary", source_base, branch,
             "--", *paths],
            capture_output=True, timeout=120)
        if diff.returncode != 0:
            return False, diff.stderr.decode(errors="replace")[-500:]
        applied = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--3way", "--index"],
            input=diff.stdout, capture_output=True, timeout=120)
        if applied.returncode != 0:
            return False, applied.stderr.decode(errors="replace")[-500:]
        if not diff.stdout:
            return False, "the staged branch has no changes relative to its source base"
        rc, out = _git(str(worktree), "commit", "-m", "replay queued change")
        return rc == 0, out[-500:].strip()
    except subprocess.TimeoutExpired:
        return False, "replaying the queued change timed out"


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
                  server_touched: bool = False,
                  source_base_sha: str | None = None) -> str:
    """Merge and either finish immediately or hand restart to the detached guard."""
    repo = deployment_store.canonical_repo(repo)
    deployment, busy = deployment_store.begin(
        repo=repo, branch=branch, base=base, source=source, ref_id=ref_id,
        server_touched=server_touched, changed_files=changed_files)
    if deployment is None:
        return f"Deployment queued behind the active release: {busy}. Try again shortly."
    did = deployment["id"]
    if source_base_sha:
        deployment_store.update(did, source_base_sha=source_base_sha)

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
    synced, sync_error = _sync_remote_base(repo, worktree, base)
    if not synced:
        _git(str(worktree), "merge", "--abort")
        _discard_worktree(repo, worktree)
        return _finish_failed(did, f"remote-base merge conflict: {sync_error}", ref_id)
    if source_base_sha:
        merged, out = _apply_job_delta(
            repo, worktree, source_base_sha, branch, changed_files)
    else:
        rc, out = _git(str(worktree), "merge", "--no-ff", "-m",
                       f"deploy {source} {ref_id or ''}".strip(), branch)
        merged = rc == 0
    if not merged:
        _git(str(worktree), "merge", "--abort")
        _discard_worktree(repo, worktree)
        return _finish_failed(did, f"merge conflict: {out[-500:].strip()}", ref_id)
    deployed_sha = _git(str(worktree), "rev-parse", "HEAD")[1].strip()
    _preserve_commit(repo, did, deployed_sha)
    previous_runtime = _server_runtime(repo) if server_touched else None
    deployment_store.update(did, before_sha=before, deployed_sha=deployed_sha,
                            runtime_path=str(worktree),
                            previous_runtime=previous_runtime,
                            source_base_sha=source_base_sha,
                            detail="isolated merge completed")
    if ref_id is not None:
        night_queue_store.update(ref_id, status="deploying",
                                 summary=f"Deployment #{did}: merged; verifying service.")

    if not server_touched:
        push_rc, push_out = _git(repo, "push", "origin",
                                 f"{deployed_sha}:refs/heads/{base}")
        if push_rc == 0:
            detail, state = "merged and pushed", "live"
            if ref_id is not None:
                night_queue_store.update(ref_id, status="shipped", summary=detail)
        else:
            detail, state = f"push failed; safely retained for retry: {push_out[-300:]}", "push_failed"
            if ref_id is not None:
                night_queue_store.update(
                    ref_id, status="staged", summary=detail,
                    failure_kind="push_failed", blocker_reason=None,
                    next_action="Supervisor will replay this change onto the latest published base.")
        deployment_store.update(did, state=state, detail=detail)
        _discard_worktree(repo, worktree)
        return (f"Deployment #{did} is live: {detail}." if state == "live" else
                f"Deployment #{did} is retained for automatic retry: {detail}.")

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
