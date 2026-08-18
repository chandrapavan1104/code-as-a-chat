"""
auth skill — check and repair the coding CLIs' login from your phone.

The coding agents (Claude Code, Codex, Gemini) authenticate with their own
providers. When a session expires you'd normally have to walk to the Mac to run
`codex login`. This skill surfaces login state to the phone and drives Codex's
device-code flow so you can re-authenticate from anywhere:

  /auth              → login status of every coding CLI
  /auth codex        → start a Codex device-code login (returns a URL + code you
                       open in your phone's browser and approve yourself)
  /auth claude       → guidance for Claude Code (no device flow; re-run on the Mac)

Security: this NEVER handles your password or completes the sign-in. It only
starts the provider's own device-code flow and shows you the one-time code —
you approve it in your own browser. The server just reads the resulting status.
"""

import asyncio
import re
import shutil

from server.skills.base import Skill
from server.skills import register

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# Device codes look like "YOU1-GZJKS" — 3–6 alnum, a dash, 3–6 alnum.
_CODE = re.compile(r"\b([A-Z0-9]{3,6}-[A-Z0-9]{3,6})\b")
_URL = re.compile(r"https://\S+")

_LOGIN_LOG = "/tmp/codeasachat_codex_login.log"


async def _sh(*args: str, timeout: int = 20) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 124, "(timed out)"
    return proc.returncode, _ANSI.sub("", out.decode(errors="replace")).strip()


async def _codex_status() -> str:
    if shutil.which("codex") is None:
        return "codex: not installed"
    rc, out = await _sh("codex", "login", "status", timeout=15)
    first = (out.splitlines() or ["(no output)"])[0]
    # "Logged in using ChatGPT" is success; "Not logged in" must NOT match on the
    # "logged in" substring.
    ok = "logged in using" in out.lower()
    return f"codex: {'✅ ' if ok else '⚠️ '}{first}"


async def _claude_status() -> str:
    if shutil.which("claude") is None:
        return "claude: not installed"
    # No status subcommand; a tiny probe tells us if auth works right now.
    rc, out = await _sh("claude", "-p", "ok", "--model", "haiku", timeout=30)
    low = out.lower()
    if rc == 0 and "error" not in low and out:
        return "claude: ✅ working"
    if any(w in low for w in ("login", "auth", "expired", "401", "unauthor")):
        return "claude: ⚠️ needs login — run `claude` on the Mac to re-auth"
    return f"claude: ⚠️ {out[:80] or 'no response'}"


async def _gemini_status() -> str:
    if shutil.which("gemini") is None:
        return "gemini: not installed"
    rc, out = await _sh("gemini", "-p", "ok", "--output-format", "json",
                        "--skip-trust", timeout=30)
    low = out.lower()
    if any(w in low for w in ("expired", "401", "unauthor", "api key",
                              "not authenticated", "please login", "please log in")):
        return "gemini: ⚠️ needs login — run `gemini` on the Mac to re-auth"
    return "gemini: ✅ working" if rc == 0 else f"gemini: ⚠️ {out[:80] or 'error'}"


async def _status_all() -> str:
    lines = await asyncio.gather(_codex_status(), _claude_status(), _gemini_status())
    return "🔐 Coding CLI auth\n" + "\n".join(lines) + \
        "\n\nRe-login from here: /auth codex"


async def _codex_device_login() -> str:
    """Start Codex's device-code flow detached, read back the URL + one-time code.
    The process keeps running so it completes once you approve in the browser;
    then `/auth` will show 'logged in'."""
    if shutil.which("codex") is None:
        return "codex is not installed on this Mac."

    # Detached so it survives this request and finishes when you approve.
    import subprocess
    with open(_LOGIN_LOG, "w") as log:
        subprocess.Popen(["codex", "login", "--device-auth"],
                         stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)

    # Give it a moment to print the URL + code.
    code = url = None
    for _ in range(8):
        await asyncio.sleep(1)
        try:
            with open(_LOGIN_LOG) as f:
                text = _ANSI.sub("", f.read())
        except OSError:
            text = ""
        m_code = _CODE.search(text)
        m_url = _URL.search(text)
        if m_code and m_url:
            code, url = m_code.group(1), m_url.group(0)
            break

    if not code:
        return ("Started the Codex login but couldn't read the code. Try `/auth "
                "codex` again, or run `codex login --device-auth` on the Mac.")

    return (
        "🔐 Re-authenticate Codex — do this on your phone:\n\n"
        f"1. Open:  {url}\n"
        f"2. Enter code:  {code}\n"
        "3. Sign in and approve.\n\n"
        "The code expires in ~15 min. Only approve because YOU started this. "
        "Once done, send /auth to confirm codex is back. Your password is never "
        "seen here — you sign in directly with the provider."
    )


class AuthSkill(Skill):
    name = "auth"
    description = "Check / repair coding-CLI login (codex device-code login from your phone)."
    final_output = True
    agent_doc = (
        'Check or repair the coding CLIs\' authentication (Claude Code / Codex / '
        'Gemini). Use when a coding tool fails with an auth / login / OAuth / '
        '"session expired" / 401 error, or when the user asks to log in or check '
        'auth. args: "" or "status" (show login state of all) | "codex" (start a '
        'device-code login the user completes in their phone browser) | "claude" '
        '| "gemini". NEVER handles passwords — it only surfaces the provider\'s '
        'own one-time code.'
    )

    async def run(self, prompt: str = "", **kwargs) -> str:
        arg = prompt.strip().lower()
        if arg in ("", "status", "check"):
            return await _status_all()
        if arg in ("codex", "openai", "login", "codex login", "relogin"):
            return await _codex_device_login()
        if arg in ("claude", "anthropic"):
            return ("Claude Code has no phone device-flow. If it's failing auth, "
                    "run `claude` once on the Mac to sign in again. Send /auth to "
                    "check current status.")
        if arg in ("gemini", "google"):
            return ("Gemini CLI has no phone device-flow here. If it's failing "
                    "auth, run `gemini` on the Mac to re-auth. /auth to check.")
        return await _status_all()


register(AuthSkill())
