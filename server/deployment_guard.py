"""Detached verifier/rollback worker for a deployment that restarts this server."""

from __future__ import annotations

import asyncio
import os
import plistlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from server.db import deployment_store, night_queue_store


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _set_runtime(path: str) -> tuple[bool, str]:
    plist = os.path.expanduser("~/Library/LaunchAgents/com.codeasachat.server.plist")
    try:
        with open(plist, "rb") as handle:
            data = plistlib.load(handle)
        data["WorkingDirectory"] = path
        temporary = plist + ".deployment.tmp"
        with open(temporary, "wb") as handle:
            plistlib.dump(data, handle)
        os.replace(temporary, plist)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _restart(runtime_path: str) -> tuple[bool, str]:
    uid = str(os.getuid())
    label = f"gui/{uid}/com.codeasachat.server"
    plist = os.path.expanduser("~/Library/LaunchAgents/com.codeasachat.server.plist")
    configured, error = _set_runtime(runtime_path)
    if not configured:
        return False, f"could not configure isolated runtime: {error}"
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


def _probe(url: str, token: str | None = None) -> str:
    """'ok', or why not ('HTTP 502', 'timeout', 'refused', ...)."""
    headers = {"X-API-Token": token} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=5) as response:
            return "ok" if response.status == 200 else f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:  # timeout, connection refused, DNS, TLS
        reason = getattr(exc, "reason", exc)
        return f"{type(reason).__name__}: {reason}"[:120]


def _request(url: str, token: str | None = None) -> bool:
    return _probe(url, token) == "ok"


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
    failing: dict[str, str] = {}
    for _ in range(tries):
        checks = {
            "localhost": _probe("http://127.0.0.1:8000/health"),
            "/api/system": _probe("http://127.0.0.1:8000/api/system", token),
            "/api/skills": _probe("http://127.0.0.1:8000/api/skills", token),
            "tailscale": _probe(public) if public else "ok",
        }
        failing = {name: why for name, why in checks.items() if why != "ok"}
        if not failing:
            return True, "localhost, authenticated APIs, and Tailscale are healthy"
        time.sleep(2)
    # Say which check failed and how: "health verification failed" alone made
    # two rollbacks (#48, #56) impossible to diagnose.
    detail = "; ".join(f"{name}: {why}" for name, why in failing.items())
    return False, f"health verification failed (localhost/API/Tailscale) — {detail}"


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
    loaded, restart_error = _restart(item["runtime_path"] or repo)
    deployment_store.update(did, state="verifying", detail="checking local and tailnet APIs")
    healthy, detail = _healthy(repo) if loaded else (False, f"launchd failed: {restart_error}")
    if healthy:
        push = _run("git", "-C", repo, "push", "origin",
                    f"{item['deployed_sha']}:refs/heads/{base}")
        state = "live" if push.returncode == 0 else "push_failed"
        if push.returncode != 0:
            detail += "; push failed—healthy runtime retained and retry scheduled"
        deployment_store.update(did, state=state, detail=detail)
        if item["ref_id"] is not None:
            night_queue_store.update(
                item["ref_id"], status="shipped" if state == "live" else "staged",
                summary=detail, failure_kind=None if state == "live" else "push_failed",
                next_action=(None if state == "live" else
                             "Supervisor will replay this change onto the published base."))
        _notify(f"Deployment #{did} {'live ✅' if state == 'live' else 'awaiting push retry'}",
                detail, item["ref_id"])
        return

    deployment_store.update(did, state="rolling_back", detail=detail)
    # No owner branch/ref was changed: rollback is purely switching launchd back
    # to its prior isolated runtime. Owner files, index, and HEAD are untouched.
    _restart(item["previous_runtime"] or repo)
    recovered, recovery_detail = _healthy(repo, tries=8)
    state = "rolled_back" if recovered else "failed"
    reason = f"rolled back safely: {detail}" if recovered else f"rollback restart failed: {recovery_detail}"
    deployment_store.update(did, state=state, detail=reason)
    if item["ref_id"] is not None:
        night_queue_store.update(item["ref_id"], status="staged", summary=reason)
    _notify(f"Deployment #{did} rolled back ↩️", reason, item["ref_id"])


if __name__ == "__main__":
    main()
