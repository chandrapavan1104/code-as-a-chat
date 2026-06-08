"""
mac skill — remote-control your Mac from your phone (and flex on your friends).

Subcommands (passed via prompt):
  lock                 sleep the display (locks if "require password" is on)
  sleep                full system sleep
  say <text>           speak text aloud through the Mac speakers
  notify <text>        pop an on-screen notification banner
  screenshot           capture the screen → pushed to your Telegram
  photo                webcam snap → pushed to your Telegram (needs imagesnap)
  open <url>           open a URL in the Mac's default browser
  bluetooth on|off     toggle Bluetooth (needs blueutil + Bluetooth permission)

Notes:
  • screenshot needs Screen Recording permission for the python process;
    photo needs Camera permission. macOS may prompt or require a manual grant
    in System Settings → Privacy & Security on first use.
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from server.skills.base import Skill
from server.skills import register
from server import notify

# Camera capture helper app (built in mac_helpers/, installed to ~/Applications).
# A real .app bundle so macOS TCC can grant it a stable Camera identity.
# IMPORTANT: must be launched via `open` (LaunchServices), NOT exec'd directly —
# otherwise the camera grant is attributed to the launching process (python),
# not the app, and macOS denies it.
WEBCAM_APP = Path.home() / "Applications" / "WebcamSnap.app"


async def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return (-1, "", f"timed out after {timeout}s")
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _chat_id(session_id: str | None) -> int | None:
    if session_id and session_id.startswith("tg:"):
        try:
            return int(session_id[3:])
        except ValueError:
            return None
    return None


# ── actions ───────────────────────────────────────────────────────────────────

async def _lock() -> str:
    rc, out, err = await _run(["pmset", "displaysleepnow"])
    if rc == 0:
        return ("Mac locked (display asleep).\n"
                "Tip: System Settings → Lock Screen → 'Require password "
                "immediately after sleep' makes this a true lock.")
    return f"[mac] lock failed: {err or out}"


async def _sleep() -> str:
    rc, out, err = await _run(["pmset", "sleepnow"])
    return "Mac going to sleep." if rc == 0 else f"[mac] sleep failed: {err or out}"


async def _say(text: str) -> str:
    if not text:
        return "Usage: /mac say <text>"
    rc, out, err = await _run(["say", text], timeout=30)
    return f"Spoke aloud: \"{text}\"" if rc == 0 else f"[mac] say failed: {err or out}"


async def _notify(text: str) -> str:
    if not text:
        return "Usage: /mac notify <text>"
    safe = text.replace('"', '\\"')
    script = f'display notification "{safe}" with title "Code-as-a-Chat"'
    rc, out, err = await _run(["osascript", "-e", script])
    return f"Notification shown on screen: {text}" if rc == 0 \
        else f"[mac] notify failed: {err or out}"


async def _open(url: str) -> str:
    if not url:
        return "Usage: /mac open <url>"
    rc, out, err = await _run(["open", url])
    return f"Opened on Mac: {url}" if rc == 0 else f"[mac] open failed: {err or out}"


async def _bluetooth(arg: str) -> str:
    if shutil.which("blueutil") is None:
        return "[mac] Bluetooth control needs blueutil — run: brew install blueutil"

    arg = arg.strip().lower()

    def _perm_hint(err: str) -> str:
        if "access" in err.lower() or "abort" in err.lower():
            return ("\nGrant Bluetooth access: System Settings → Privacy & "
                    "Security → Bluetooth → enable for the Code-as-a-Chat "
                    "process (or your terminal).")
        return ""

    if arg in ("on", "enable", "1"):
        rc, out, err = await _run(["blueutil", "--power", "1"])
        return "Bluetooth ON." if rc == 0 else f"[mac] bluetooth on failed: {err or out}{_perm_hint(err)}"
    if arg in ("off", "disable", "0"):
        rc, out, err = await _run(["blueutil", "--power", "0"])
        return "Bluetooth OFF." if rc == 0 else f"[mac] bluetooth off failed: {err or out}{_perm_hint(err)}"

    # no arg (or "status"/"toggle") → report current state
    rc, out, err = await _run(["blueutil", "--power"])
    if rc != 0:
        return f"[mac] bluetooth status failed: {err or out}{_perm_hint(err)}"
    on = out.strip() == "1"
    if arg == "toggle":
        rc2, out2, err2 = await _run(["blueutil", "--power", "0" if on else "1"])
        if rc2 == 0:
            return f"Bluetooth toggled {'OFF' if on else 'ON'}."
        return f"[mac] toggle failed: {err2 or out2}{_perm_hint(err2)}"
    return f"Bluetooth is {'ON' if on else 'OFF'}.  (/mac bluetooth on|off|toggle)"


async def _screenshot(session_id: str | None) -> str:
    fd, path = tempfile.mkstemp(suffix=".png", prefix="codeasachat_shot_")
    os.close(fd)
    try:
        rc, out, err = await _run(["screencapture", "-x", path], timeout=20)
        if rc != 0:
            return f"[mac] screenshot failed: {err or out}"
        ok = await notify.push_photo(path, caption="Screen capture",
                                     chat_id=_chat_id(session_id))
        if ok:
            return "Screenshot sent to your phone."
        return ("Captured the screen but the photo push failed. "
                "If this persists, the python process may need Screen Recording "
                "permission (System Settings → Privacy & Security).")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _photo(session_id: str | None) -> str:
    if not WEBCAM_APP.exists():
        return ("[mac] WebcamSnap.app not installed. Build it from "
                "mac_helpers/ (see project README).")
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="codeasachat_cam_")
    os.close(fd)
    try:
        # Launch via LaunchServices (-W waits for it to finish) so the app is
        # its own TCC-responsible process and uses its camera grant.
        rc, out, err = await _run(
            ["open", "-W", str(WEBCAM_APP), "--args", path], timeout=25
        )
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size < 5000:
            low = (err or out).lower()
            if "denied" in low or "not granted" in low:
                return ("[mac] camera permission needed. One-time grant:\n"
                        "Open ~/Applications/WebcamSnap.app, click Allow, retry.")
            return f"[mac] webcam capture produced no image (rc={rc}). {err or out}".strip()
        ok = await notify.push_photo(path, caption="Webcam snap",
                                     chat_id=_chat_id(session_id))
        if ok:
            return "Webcam photo sent to your phone."
        return "Took the photo but the Telegram push failed — check the bot token."
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── skill ─────────────────────────────────────────────────────────────────────

class MacSkill(Skill):
    name = "mac"
    description = ("Remote-control the Mac: lock, sleep, say, notify, "
                   "screenshot, photo, open")

    async def run(self, prompt: str = "", session_id: str | None = None, **kwargs) -> str:
        parts = prompt.strip().split(None, 1)
        cmd = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "lock":
            return await _lock()
        if cmd == "sleep":
            return await _sleep()
        if cmd == "say":
            return await _say(rest)
        if cmd in ("notify", "notification"):
            return await _notify(rest)
        if cmd in ("screenshot", "screen", "shot"):
            return await _screenshot(session_id)
        if cmd in ("photo", "webcam", "cam", "selfie"):
            return await _photo(session_id)
        if cmd == "open":
            return await _open(rest)
        if cmd in ("bluetooth", "bt"):
            return await _bluetooth(rest)

        return (
            "Mac control:\n"
            "  /mac lock              lock the screen\n"
            "  /mac sleep             sleep the Mac\n"
            "  /mac say <text>        speak through the speakers\n"
            "  /mac notify <text>     on-screen banner\n"
            "  /mac screenshot        screen → your phone\n"
            "  /mac photo             webcam snap → your phone\n"
            "  /mac open <url>        open a URL on the Mac\n"
            "  /mac bluetooth on|off  toggle Bluetooth"
        )


register(MacSkill())
