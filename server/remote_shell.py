"""Live shell sessions on the Mac for Gajala's Terminal screen.

Each session is one real zsh in a pseudo-terminal, so `cd`, environment
variables and aliases persist between commands, prompts (y/n) can be
answered, and Ctrl-C reaches the running program through the terminal's own
line discipline. zsh runs without its line editor (+Z): the kernel then does
the echoing, which keeps the output plain text and makes the terminal's own
mode meaningful — `mode()` reads it to tell the phone whether the program in
front wants a line, raw keys (arrow-key menus) or a password, and the audit
log uses the same reading to decide what it may keep.

Output goes to an in-memory ring buffer addressed by absolute byte offset.
Viewers stream from any offset and resume from the last one they saw after a
dropped connection; nothing is written to disk. The child gets a minimal
environment (no server secrets from .env) and a login shell's PATH.
"""

import asyncio
import fcntl
import logging
import os
import signal
import struct
import subprocess
import sys
import termios
import time
import uuid
from pathlib import Path

import psutil

log = logging.getLogger(__name__)

MAX_SESSIONS = 3
BUFFER_BYTES = 256 * 1024
IDLE_SECONDS = 30 * 60          # no input and no output
MAX_LIFETIME_SECONDS = 12 * 3600
CLOSED_RETENTION_SECONDS = 5 * 60  # keep a finished session's last output readable
MAX_FRAME_BYTES = 64 * 1024
SHELL = "/bin/zsh"
SHELL_ARGS = [SHELL, "+Z", "+o", "promptsp", "-l", "-i"]
# A fresh, single-threaded interpreter claims the pty as its controlling
# terminal (needed for Ctrl-C and job control) and becomes zsh. pty.fork()
# would run Python in a child forked from this multi-threaded server, which
# can deadlock; subprocess starts its child without running any Python.
_CLAIM_TTY = ("import fcntl,os,sys,termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); "
              "os.execv(sys.argv[1], sys.argv[1:])")


class ShellError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _child_env() -> dict[str, str]:
    home = str(Path.home())
    keep = {k: os.environ[k] for k in ("USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
            if k in os.environ}
    return {**keep, "HOME": home, "SHELL": SHELL, "TERM": "dumb",
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": keep.get("LANG", "en_US.UTF-8"), "GAJALA_REMOTE_SHELL": "1",
            # Pagers are the usual accidental full-screen app (git log, git diff,
            # man) and cannot be driven from the phone; print straight through.
            "PAGER": "cat", "GIT_PAGER": "cat", "MANPAGER": "cat",
            # `git commit` without -m would open vi. `true` leaves the message
            # empty, so git aborts with a clear "empty commit message" instead.
            "GIT_EDITOR": "true"}


def _utf8_safe_end(chunk: bytes | bytearray) -> int:
    """Length of [chunk] without a trailing, still-incomplete UTF-8 character."""
    for back in range(1, min(4, len(chunk)) + 1):
        byte = chunk[-back]
        if byte & 0x80 == 0:
            return len(chunk)
        if byte & 0xC0 == 0xC0:
            need = 2 if byte >> 5 == 0b110 else 3 if byte >> 4 == 0b1110 else 4
            return len(chunk) - back if back < need else len(chunk)
    return len(chunk)


class Session:
    def __init__(self, cwd: Path, cols: int, rows: int):
        self.id = uuid.uuid4().hex[:12]
        self.cwd = str(cwd)
        self.created = self.last_active = time.time()
        self.buffer = bytearray()
        self.start = 0                      # absolute offset of buffer[0]
        self.closed = False
        self.closed_at: float | None = None
        self.exit_code: int | None = None
        self._event = asyncio.Event()
        fd, slave = os.openpty()
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", _CLAIM_TTY, *SHELL_ARGS],
                stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=_child_env(),
                start_new_session=True, close_fds=True)
        except OSError:
            os.close(fd)
            raise
        finally:
            os.close(slave)
        self.pid, self.fd = self.proc.pid, fd   # new session: pid is also the pgid
        os.set_blocking(fd, False)
        asyncio.get_running_loop().add_reader(fd, self._on_readable)

    # ── output ────────────────────────────────────────────────────────────
    @property
    def end(self) -> int:
        return self.start + len(self.buffer)

    @property
    def changed(self) -> asyncio.Event:
        """Grab this BEFORE reading, then await it: no wake-up can be missed."""
        return self._event

    def _wake(self) -> None:
        self._event.set()
        self._event = asyncio.Event()

    def _on_readable(self) -> None:
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        except OSError:                     # EIO: the shell side has gone
            data = b""
        if not data:
            self._finish()
            return
        self.buffer += data
        overflow = len(self.buffer) - BUFFER_BYTES
        if overflow > 0:
            del self.buffer[:overflow]
            self.start += overflow
        self.last_active = time.time()
        self._wake()

    def read(self, offset: int) -> tuple[int | None, bytes, int]:
        """(gap_start or None, bytes, next_offset) from [offset].

        A gap means the viewer asked for output that has already rotated out of
        the buffer; it resumes at the oldest byte still held.
        """
        gap = None
        if offset < self.start:
            gap, offset = offset, self.start
        offset = min(offset, self.end)
        chunk = self.buffer[offset - self.start: offset - self.start + MAX_FRAME_BYTES]
        if not self.closed:
            chunk = chunk[:_utf8_safe_end(chunk)]
        return gap, bytes(chunk), offset + len(chunk)

    # ── input ─────────────────────────────────────────────────────────────
    def mode(self) -> str:
        """How the program in front is reading the terminal.

        line   — canonical with echo: commands and `read` prompts; a text line
                 plus Enter is right.
        keys   — raw (non-canonical): menus, y/n keys, fzf, REPLs; every key
                 must go through as it is pressed, Enter as \\r.
        secret — canonical with echo off: a password prompt.
        """
        if self.closed:
            return "line"
        try:
            lflag = termios.tcgetattr(self.fd)[3]
        except (termios.error, OSError):
            return "line"
        if not lflag & termios.ICANON:
            return "keys"
        return "line" if lflag & termios.ECHO else "secret"

    def foreground(self) -> str | None:
        """Name of the program in front, or None when it is the shell itself."""
        if self.closed:
            return None
        try:
            pgid = os.tcgetpgrp(self.fd)
            if pgid <= 0 or pgid == self.pid:   # 0: nobody owns the tty yet (startup)
                return None
            return psutil.Process(pgid).name()
        except (OSError, psutil.Error):
            return None

    def write(self, data: str) -> None:
        if self.closed:
            raise ShellError(409, "This session has ended.")
        raw = data.encode()
        while raw:
            try:
                sent = os.write(self.fd, raw)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            raw = raw[sent:]
        self.last_active = time.time()

    # ── lifecycle ─────────────────────────────────────────────────────────
    def _finish(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.closed_at = time.time()
        try:
            asyncio.get_running_loop().remove_reader(self.fd)
        except (RuntimeError, ValueError):
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self._reap()
        self._wake()

    def _reap(self) -> None:
        code = self.proc.poll()
        if code is not None:
            self.exit_code = code

    def kill(self) -> None:
        """Hang up the whole process group (the shell and anything it started)."""
        for sig in (signal.SIGHUP, signal.SIGCONT):
            try:
                os.killpg(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def force_kill(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self._finish()
        self._reap()

    def info(self) -> dict:
        return {"id": self.id, "cwd": self.cwd, "created": self.created,
                "last_active": self.last_active, "closed": self.closed,
                "exit_code": self.exit_code, "end": self.end}


class ShellManager:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def live(self) -> list[Session]:
        return [s for s in self.sessions.values() if not s.closed]

    def create(self, cwd: Path, cols: int = 80, rows: int = 40) -> Session:
        if len(self.live()) >= MAX_SESSIONS:
            raise ShellError(429, f"At most {MAX_SESSIONS} shell sessions can be open. "
                                  "Close one first.")
        if not cwd.is_dir():
            raise ShellError(404, f"Folder not found: {cwd}")
        session = Session(cwd, max(20, min(cols, 300)), max(10, min(rows, 200)))
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise ShellError(404, "No such shell session (it may have expired).")
        return session

    async def close(self, session_id: str) -> Session:
        session = self.get(session_id)
        session.kill()
        for _ in range(20):                 # give it 2 s to exit on SIGHUP
            if session.closed:
                break
            await asyncio.sleep(0.1)
        if not session.closed:
            session.force_kill()
        session._reap()
        self.sessions.pop(session_id, None)
        return session

    async def reap_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.reap_once()
            except Exception as exc:        # never let the reaper die
                log.warning("shell reaper: %s", exc)

    async def reap_once(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        closed = []
        for session in list(self.sessions.values()):
            if session.closed:
                session._reap()
                if now - (session.closed_at or now) > CLOSED_RETENTION_SECONDS:
                    self.sessions.pop(session.id, None)
                continue
            if (now - session.last_active > IDLE_SECONDS
                    or now - session.created > MAX_LIFETIME_SECONDS):
                await self.close(session.id)
                closed.append(session.id)
        return closed

    def shutdown(self) -> None:
        """Server exit: shells run in their own sessions and would outlive it."""
        for session in list(self.sessions.values()):
            if not session.closed:
                session.force_kill()
        self.sessions.clear()


manager = ShellManager()
