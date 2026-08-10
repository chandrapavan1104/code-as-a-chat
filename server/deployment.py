"""Serialized merge + deploy entry point shared by every agent surface."""

from __future__ import annotations

import os
import subprocess
import sys

from server.db import deployment_store, night_queue_store


def _git(repo: str, *args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        done = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=True, timeout=timeout)
        return done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired:
        return 124, "git operation timed out"


def _paths(porcelain: str) -> set[str]:
    return {line[3:].strip().strip('"') for line in porcelain.splitlines()
            if len(line) > 3}


def _finish_failed(deployment_id: int, detail: str, ref_id: int | None) -> str:
    deployment_store.update(deployment_id, state="failed", detail=detail)
    if ref_id is not None:
        night_queue_store.update(ref_id, status="staged", summary=detail)
    return f"Deployment stopped safely: {detail}"


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

    rc, before = _git(repo, "rev-parse", base)
    if rc != 0:
        return _finish_failed(did, f"base branch '{base}' does not exist", ref_id)
    before = before.strip()
    if _git(repo, "rev-parse", "--verify", branch)[0] != 0:
        return _finish_failed(did, f"staged branch '{branch}' does not exist", ref_id)

    current = _git(repo, "branch", "--show-current")[1].strip()
    dirty = _paths(_git(repo, "status", "--porcelain")[1])
    overlap = dirty.intersection(changed_files)
    if overlap:
        return _finish_failed(
            did, "owner changes overlap the deployment: " + ", ".join(sorted(overlap)),
            ref_id)
    if current != base:
        if dirty:
            return _finish_failed(
                did, f"live checkout is on '{current}' with unrelated owner changes; "
                     f"it was left untouched", ref_id)
        rc, out = _git(repo, "checkout", base)
        if rc != 0:
            return _finish_failed(did, f"could not switch to {base}: {out[-300:]}", ref_id)

    rc, out = _git(repo, "merge", "--no-ff", "-m", f"deploy {source} {ref_id or ''}".strip(),
                   branch)
    if rc != 0:
        _git(repo, "merge", "--abort")
        return _finish_failed(did, f"merge conflict: {out[-500:].strip()}", ref_id)
    deployed_sha = _git(repo, "rev-parse", "HEAD")[1].strip()
    deployment_store.update(did, before_sha=before, deployed_sha=deployed_sha,
                            detail="merge completed")
    if ref_id is not None:
        night_queue_store.update(ref_id, status="deploying",
                                 summary=f"Deployment #{did}: merged; verifying service.")

    if not server_touched:
        push_rc, push_out = _git(repo, "push", "origin", base)
        detail = "merged and pushed" if push_rc == 0 else f"merged; push failed: {push_out[-300:]}"
        deployment_store.update(did, state="live", detail=detail)
        if ref_id is not None:
            night_queue_store.update(ref_id, status="shipped", summary=detail)
        return f"Deployment #{did} is live: {detail}."

    subprocess.Popen(
        [sys.executable, "-m", "server.deployment_guard", str(did)], cwd=repo,
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": repo},
    )
    return (f"Deployment #{did} merged. Restart, authenticated API checks, "
            "Tailscale verification, push, and safe rollback now run automatically.")
