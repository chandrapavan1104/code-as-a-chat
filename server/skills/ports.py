"""
ports skill — inspect listening TCP ports and kill processes.

Subcommands (passed via prompt):
  (empty) | list           list every listening port + owning process
  mine                     only ports owned by the current user
  show <port>              detail for a port (cmdline, started, address)
  kill <pid>               send SIGTERM to a PID
  kill :<port>             kill whoever is listening on a port
  kill -9 <pid>            SIGKILL (use sparingly)

Uses `lsof` (no root needed for user-owned processes). Falls back to a clear
message if lsof is missing or returns nothing.
"""

import asyncio
import getpass
import os
import re
import shutil
import signal
from datetime import datetime

import psutil

from server.skills.base import Skill
from server.skills import register


LSOF_TIMEOUT = 8
SYSTEM_PID_FLOOR = 100  # below this is almost always launchd-spawned daemons


# ── data gathering ────────────────────────────────────────────────────────────

async def _run_lsof() -> str:
    if shutil.which("lsof") is None:
        return ""
    proc = await asyncio.create_subprocess_exec(
        "lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=LSOF_TIMEOUT)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ""
    return stdout_b.decode(errors="replace")


def _parse_port(addr: str) -> int | None:
    """Last colon-separated chunk is the port. Handles IPv6 like [::1]:8000."""
    m = re.search(r":(\d+)$", addr)
    return int(m.group(1)) if m else None


async def _list_ports() -> list[dict]:
    raw = await _run_lsof()
    if not raw:
        return []

    rows: list[dict] = []
    for line in raw.splitlines()[1:]:  # drop header
        parts = line.split(None, 8)
        if len(parts) < 9:
            continue
        command, pid_s, user, _fd, _type, _dev, _size, _node, rest = parts
        # `rest` is "ADDRESS (LISTEN)"
        addr = rest.split(" ", 1)[0]
        port = _parse_port(addr)
        if port is None:
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        rows.append({
            "port": port,
            "pid": pid,
            "command": command,
            "user": user,
            "address": addr,
        })

    # Dedupe: same (port, pid) appears for IPv4 + IPv6
    seen: set[tuple[int, int]] = set()
    unique: list[dict] = []
    for r in rows:
        key = (r["port"], r["pid"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    unique.sort(key=lambda r: r["port"])
    return unique


# ── views ─────────────────────────────────────────────────────────────────────

def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _format_rows(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    lines = []
    for r in rows:
        mine = "*" if r["user"] == getpass.getuser() else " "
        lines.append(f"{mine} :{r['port']:<6}  {_truncate(r['command'], 16):<16}  pid {r['pid']}")
    return "\n".join(lines)


async def _list_view(mine_only: bool = False) -> str:
    rows = await _list_ports()
    if not rows:
        return "No listening ports found (or lsof unavailable)."

    if mine_only:
        me = getpass.getuser()
        rows = [r for r in rows if r["user"] == me]
        if not rows:
            return f"No listening ports owned by {me}."

    label = "MY LISTENING PORTS" if mine_only else "LISTENING PORTS"
    lines = [f"{label} ({len(rows)}):  * = yours", ""]
    lines.append(_format_rows(rows))
    lines += [
        "",
        "USAGE:",
        "• /ports show <port>     details",
        "• /ports kill <pid>      sigterm a PID",
        "• /ports kill :<port>    kill the port owner",
        "• /ports mine            only yours",
    ]
    return "\n".join(lines)


async def _show_view(target: str) -> str:
    target = target.strip().lstrip(":")
    try:
        port = int(target)
    except ValueError:
        return f"Invalid port: {target!r}"

    rows = [r for r in await _list_ports() if r["port"] == port]
    if not rows:
        return f"Nothing listening on port :{port}"

    out = [f"PORT :{port}", ""]
    for r in rows:
        out.append(f"PID:     {r['pid']}")
        out.append(f"COMMAND: {r['command']}")
        out.append(f"USER:    {r['user']}")
        out.append(f"ADDRESS: {r['address']}")
        try:
            p = psutil.Process(r["pid"])
            cmdline = " ".join(p.cmdline()) or p.name()
            out.append(f"CMDLINE: {_truncate(cmdline, 120)}")
            out.append(f"STARTED: {datetime.fromtimestamp(p.create_time()).strftime('%Y-%m-%d %H:%M')}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        out.append("")
    return "\n".join(out).rstrip()


async def _kill_view(rest: str) -> str:
    parts = rest.split()
    if not parts:
        return "Usage: /ports kill <pid>  |  kill :<port>  |  kill -9 <pid>"

    use_sigkill = False
    if parts[0] == "-9":
        use_sigkill = True
        parts.pop(0)
    if not parts:
        return "Specify a pid or :<port>."

    target = parts[0]
    sig = signal.SIGKILL if use_sigkill else signal.SIGTERM
    sig_name = "SIGKILL" if use_sigkill else "SIGTERM"

    # Port form
    if target.startswith(":"):
        try:
            port = int(target[1:])
        except ValueError:
            return f"Invalid port: {target}"
        rows = [r for r in await _list_ports() if r["port"] == port]
        if not rows:
            return f"Nothing listening on port :{port}"
        results = []
        for r in rows:
            results.append(_kill_pid(r["pid"], sig, sig_name, r["command"]))
        return "\n".join(results)

    # PID form
    try:
        pid = int(target)
    except ValueError:
        return f"Invalid pid: {target}"

    if pid < SYSTEM_PID_FLOOR and not use_sigkill:
        return (f"Refusing to kill pid {pid} — looks like a system process. "
                f"If you really mean it: /ports kill -9 {pid}")

    try:
        name = psutil.Process(pid).name()
    except psutil.NoSuchProcess:
        return f"No process with pid {pid}"
    except psutil.AccessDenied:
        name = "?"

    return _kill_pid(pid, sig, sig_name, name)


def _kill_pid(pid: int, sig: int, sig_name: str, name: str) -> str:
    try:
        os.kill(pid, sig)
        return f"Sent {sig_name} to pid {pid} ({name})"
    except ProcessLookupError:
        return f"pid {pid} not found"
    except PermissionError:
        return f"Permission denied — pid {pid} ({name}) owned by another user"


# ── skill ─────────────────────────────────────────────────────────────────────

class PortsSkill(Skill):
    name = "ports"
    description = "Listening TCP ports: /ports | show <port> | kill <pid|:<port>>"
    final_output = True
    agent_doc = ('Listening TCP ports and who owns them. Can also kill processes. '
                 'args: "" (list) | "mine" | "show <port>" | "kill <pid>" | "kill :<port>"')

    async def run(self, prompt: str = "", **kwargs) -> str:
        args = prompt.strip().split()
        if not args:
            return await _list_view()

        cmd = args[0].lower()

        if cmd in ("list", "ls"):
            return await _list_view()
        if cmd in ("mine", "me"):
            return await _list_view(mine_only=True)
        if cmd == "show":
            if len(args) < 2:
                return "Usage: /ports show <port>"
            return await _show_view(args[1])
        if cmd == "kill":
            return await _kill_view(" ".join(args[1:]))

        # Bare number → assume "show <port>"
        if cmd.isdigit() or cmd.startswith(":"):
            return await _show_view(cmd)

        return ("Unknown subcommand. Try: /ports | show <port> | "
                "kill <pid> | kill :<port> | mine")


register(PortsSkill())
