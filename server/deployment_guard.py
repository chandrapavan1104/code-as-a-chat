"""Detached verifier/rollback worker for a deployment that restarts this server."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
import urllib.request

from server.db import deployment_store, night_queue_store


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _restart() -> tuple[bool, str]:
    uid = str(os.getuid())
    label = f"gui/{uid}/com.codeasachat.server"
    plist = os.path.expanduser("~/Library/LaunchAgents/com.codeasachat.server.plist")
    _run("launchctl", "bootout", label)
    _run("pkill", "-f", "uvicorn server.main")
    time.sleep(1)
    result = _run("launchctl", "bootstrap", f"gui/{uid}", plist)
    if result.returncode != 0:
        # bootstrap can race a prior bootout; one retry is deterministic and safe.
        time.sleep(2)
        result = _run("launchctl", "bootstrap", f"gui/{uid}", plist)
    loaded = _run("launchctl", "print", label)
    return loaded.returncode == 0, (result.stderr or loaded.stderr).strip()


def _request(url: str, token: str | None = None) -> bool:
    headers = {"X-API-Token": token} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def _tailscale_health_url() -> str | None:
    candidates = ["/Applications/Tailscale.app/Contents/MacOS/Tailscale",
                  "/opt/homebrew/bin/tailscale", "tailscale"]
    for binary in candidates:
        try:
            result = _run(binary, "serve", "status")
        except FileNotFoundError:
            continue
        match = re.search(r"https://\S+", result.stdout)
        if match:
            return match.group(0).rstrip("/") + "/health"
    return None


def _healthy(repo: str, tries: int = 10) -> tuple[bool, str]:
    token_path = os.path.expanduser("~/.codeasachat/api_token")
    token = open(token_path).read().strip() if os.path.exists(token_path) else ""
    public = _tailscale_health_url()
    for _ in range(tries):
        local = _request("http://127.0.0.1:8000/health")
        system = _request("http://127.0.0.1:8000/api/system", token)
        skills = _request("http://127.0.0.1:8000/api/skills", token)
        tailnet = True if not public else _request(public)
        if local and system and skills and tailnet:
            return True, "localhost, authenticated APIs, and Tailscale are healthy"
        time.sleep(2)
    return False, "health verification failed (localhost/API/Tailscale)"


def _notify(title: str, body: str, ref_id: int | None) -> None:
    try:
        from server.notifier import notify_app
        asyncio.run(notify_app("deployment", title, body, ref_kind="queue_job",
                               ref_id=ref_id))
    except Exception:
        pass


def main() -> None:
    did = int(sys.argv[1])
    item = deployment_store.get(did)
    if not item:
        return
    repo, base = item["repo"], item["base"]
    deployment_store.update(did, state="restarting", detail="restarting launchd service")
    loaded, restart_error = _restart()
    deployment_store.update(did, state="verifying", detail="checking local and tailnet APIs")
    healthy, detail = _healthy(repo) if loaded else (False, f"launchd failed: {restart_error}")
    if healthy:
        push = _run("git", "-C", repo, "push", "origin", base)
        if push.returncode != 0:
            detail += "; push failed (code remains live locally)"
        deployment_store.update(did, state="live", detail=detail)
        if item["ref_id"] is not None:
            night_queue_store.update(item["ref_id"], status="shipped", summary=detail)
        _notify(f"Deployment #{did} live ✅", detail, item["ref_id"])
        return

    deployment_store.update(did, state="rolling_back", detail=detail)
    head = _run("git", "-C", repo, "rev-parse", "HEAD").stdout.strip()
    if head != item["deployed_sha"]:
        reason = ("automatic rollback could not safely preserve owner changes; "
                  "HEAD changed after deployment, so no Git mutation was attempted")
        deployment_store.update(did, state="failed", detail=reason)
        if item["ref_id"] is not None:
            night_queue_store.update(item["ref_id"], status="needs_you", summary=reason)
        _notify(f"Deployment #{did} needs recovery", reason, item["ref_id"])
        return
    rollback = _run("git", "-C", repo, "reset", "--keep", item["before_sha"])
    if rollback.returncode != 0:
        reason = ("automatic rollback could not safely preserve owner changes; "
                  "the failed deployment branch and ledger were retained")
        deployment_store.update(did, state="failed", detail=reason)
        if item["ref_id"] is not None:
            night_queue_store.update(item["ref_id"], status="needs_you", summary=reason)
        _notify(f"Deployment #{did} needs recovery", reason, item["ref_id"])
        return
    _restart()
    recovered, recovery_detail = _healthy(repo, tries=8)
    state = "rolled_back" if recovered else "failed"
    reason = f"rolled back safely: {detail}" if recovered else f"rollback restart failed: {recovery_detail}"
    deployment_store.update(did, state=state, detail=reason)
    if item["ref_id"] is not None:
        night_queue_store.update(item["ref_id"], status="staged", summary=reason)
    _notify(f"Deployment #{did} rolled back ↩️", reason, item["ref_id"])


if __name__ == "__main__":
    main()
