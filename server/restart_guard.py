"""
Detached restart guard for the fix agent's server-side ships.

The server can't safely restart itself — killing uvicorn would kill the very
request doing the restart, so a broken change could strand you with no way back
in from the phone. So `/fix ship` (for server changes) spawns THIS as a separate,
detached process. It restarts the server, verifies /health, and if the new code
doesn't come up it hard-resets the repo to the last-good commit and restarts that
— then pushes the outcome to your phone.

Usage: python -m server.restart_guard <repo_dir> <last_good_commit>
"""

import os
import subprocess
import sys
import time
import urllib.request


def _restart() -> None:
    uid = str(os.getuid())
    plist = os.path.expanduser("~/Library/LaunchAgents/com.codeasachat.server.plist")
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/com.codeasachat.server"],
                   capture_output=True)
    subprocess.run(["pkill", "-f", "uvicorn server.main"], capture_output=True)
    time.sleep(1)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist], capture_output=True)


def _healthy(tries: int = 8) -> bool:
    for _ in range(tries):
        try:
            with urllib.request.urlopen("http://localhost:8000/health", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _push(title: str, body: str) -> None:
    try:
        import asyncio
        from server import fcm
        if fcm.available():
            asyncio.run(fcm.push_all(title, body, data={"type": "fix_result"}))
    except Exception:
        pass


def main() -> None:
    repo, good = sys.argv[1], sys.argv[2]
    _restart()
    if _healthy():
        _push("Fix shipped ✅", "Server is healthy on the new code.")
        return
    # New code didn't boot — revert and bring the last-good version back up.
    subprocess.run(["git", "-C", repo, "reset", "--hard", good], capture_output=True)
    _restart()
    _push("Fix rolled back ↩️",
          "The server didn't come up on the new code, so I reverted to the last "
          "good commit. Your phone still works — tell me what to change.")


if __name__ == "__main__":
    main()
