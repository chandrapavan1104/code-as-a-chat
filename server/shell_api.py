"""/api/shell — the Gajala Terminal screen's remote shell.

Deliberately an API, not a skill: skills are callable by the chat agent,
Telegram and Night Shift, none of which hold a device key. Every route sits
behind the shared X-API-Token (the router is mounted under api_v2), and every
route that touches a shell also requires the paired device's key in
`X-Shell-Device` (see server/db/shell_store.py). REMOTE_SHELL_ENABLED=0 in the
Mac's .env switches the whole feature off.
"""

import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server import config as cfg
from server import notifier, workspace
from server.db import shell_store
from server.remote_shell import ShellError, manager

router = APIRouter(prefix="/shell", tags=["shell"])

MIN_KEY_CHARS = 32
HEARTBEAT_SECONDS = 15
MODE_POLL_SECONDS = 0.3   # a program can switch to raw mode without printing
MAX_INPUT_CHARS = 4096
_CONTROL_NAMES = {"\x03": "^C", "\x04": "^D", "\x1a": "^Z", "\t": "<tab>", "\x1b": "<esc>"}


def _enabled() -> None:
    if not cfg.REMOTE_SHELL_ENABLED:
        raise HTTPException(403, "The remote shell is turned off on the Mac "
                                 "(REMOTE_SHELL_ENABLED=0).")


def _valid_key(key: str | None) -> str:
    if not key or len(key) < MIN_KEY_CHARS or len(key) > 256:
        raise HTTPException(403, "This device has no remote-shell key. Update Gajala.")
    return key


def require_device(x_shell_device: str | None = Header(default=None)) -> dict:
    """The paired device, or 403. Applied to every route that touches a shell."""
    _enabled()
    key = _valid_key(x_shell_device)
    if not shell_store.matches(key):
        bound = shell_store.binding()
        if bound:
            raise HTTPException(403, f"The remote shell is paired with another device "
                                     f"({bound['label']}). Unpair it there, or run "
                                     f"`scripts/remote-shell unbind` on the Mac.")
        raise HTTPException(403, "This device is not paired for the remote shell yet.")
    shell_store.touch()
    return shell_store.binding() or {}


def _raise(exc: ShellError):
    raise HTTPException(exc.status, str(exc))


_KEY_NAMES = {"\x1b[A": "↑", "\x1bOA": "↑", "\x1b[B": "↓", "\x1bOB": "↓",
              "\x1b[C": "→", "\x1bOC": "→", "\x1b[D": "←", "\x1bOD": "←",
              "\x1b[H": "<home>", "\x1bOH": "<home>", "\x1b[F": "<end>", "\x1bOF": "<end>",
              "\x1b[3~": "<del>", "\x1b[5~": "<pgup>", "\x1b[6~": "<pgdn>"}
_KEY_TOKEN = re.compile(r"\x1b\[[0-9;]*[~A-Za-z]|\x1bO[A-Za-z]|[\x00-\x1f\x7f]|[^\x00-\x1f\x7f]+")


def _key_summary(data: str) -> str:
    """Raw-mode input as key names. Typed text is only counted: some raw-mode
    password fields draw their own `***`, so content is never kept."""
    out = []
    for token in _KEY_TOKEN.findall(data):
        if token in _KEY_NAMES:
            out.append(_KEY_NAMES[token])
        elif token.startswith("\x1b") and len(token) > 1:
            out.append("<key>")
        elif token in ("\r", "\n"):
            out.append("⏎")
        elif token in ("\x7f", "\b"):
            out.append("⌫")
        elif token in _CONTROL_NAMES:
            out.append(_CONTROL_NAMES[token])
        elif len(token) == 1 and ord(token) < 32:
            out.append("^" + chr(ord(token) + 64))
        else:
            out.append(f"[{len(token)} char{'s' if len(token) != 1 else ''}]")
    return " ".join(out)


def _audit_text(data: str, mode: str) -> str:
    """What the audit log keeps of an input chunk, by the terminal's mode when it
    arrived: commands verbatim, password prompts never, raw keys as key names."""
    if mode == "secret":
        return "[hidden input]"
    if mode == "keys":
        return _key_summary(data)
    text = data
    for char, name in _CONTROL_NAMES.items():
        text = text.replace(char, name)
    return text.replace("\r", "\n").rstrip("\n")


# ── pairing ───────────────────────────────────────────────────────────────────

@router.get("/status")
def status(x_shell_device: str | None = Header(default=None)):
    bound = shell_store.binding()
    return {"enabled": cfg.REMOTE_SHELL_ENABLED, "paired": bool(bound),
            "label": bound["label"] if bound else None,
            "paired_at": bound["bound_at"] if bound else None,
            "this_device": bool(x_shell_device) and shell_store.matches(x_shell_device)}


class PairBody(BaseModel):
    label: str = Field(default="phone", max_length=80)


@router.post("/pair")
async def pair(body: PairBody, x_shell_device: str | None = Header(default=None)):
    _enabled()
    key = _valid_key(x_shell_device)
    if shell_store.matches(key):
        return {"paired": True, "label": shell_store.binding()["label"]}
    label = body.label.strip() or "phone"
    if not shell_store.bind(key, label):
        bound = shell_store.binding()
        raise HTTPException(409, f"The remote shell is already paired with "
                                 f"{bound['label'] if bound else 'another device'}. Unpair it "
                                 f"there, or run `scripts/remote-shell unbind` on the Mac.")
    shell_store.audit("pair", device=label)
    # Trust on first use: make sure the owner hears about every new pairing.
    await notifier.notify_app("shell_paired", "Remote shell paired",
                              f"{label} can now run commands on the Mac. If this wasn't "
                              f"you, run `scripts/remote-shell unbind` on the Mac.",
                              telegram=True)
    return {"paired": True, "label": label}


@router.delete("/pair")
async def unpair(x_shell_device: str | None = Header(default=None)):
    device = require_device(x_shell_device)
    manager.shutdown()
    shell_store.unbind()
    shell_store.audit("unpair", device=device.get("label"))
    return {"paired": False}


# ── sessions ──────────────────────────────────────────────────────────────────

class NewSession(BaseModel):
    project: str | None = Field(default=None, max_length=200)
    cols: int = 80
    rows: int = 40


def _start_dir(project: str | None) -> Path:
    if not project:
        return Path(workspace.active() or Path.home())
    if project == "~":
        return Path.home()
    hit = workspace.resolve(project)
    if hit is None:
        raise HTTPException(404, f"Unknown project: {project}")
    return Path(hit)


@router.get("/sessions")
def list_sessions(x_shell_device: str | None = Header(default=None)):
    require_device(x_shell_device)
    return {"sessions": [s.info() for s in manager.sessions.values()]}


@router.post("/sessions")
async def create_session(  # async: the PTY reader joins the event loop
        body: NewSession, x_shell_device: str | None = Header(default=None)):
    device = require_device(x_shell_device)
    try:
        session = manager.create(_start_dir(body.project), body.cols, body.rows)
    except ShellError as exc:
        _raise(exc)
    shell_store.audit("start", session_id=session.id, device=device.get("label"),
                      cwd=session.cwd)
    return session.info()


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str, x_shell_device: str | None = Header(default=None)):
    device = require_device(x_shell_device)
    try:
        session = await manager.close(session_id)
    except ShellError as exc:
        _raise(exc)
    shell_store.audit("close", session_id=session_id, device=device.get("label"),
                      cwd=session.cwd)
    return session.info()


class Input(BaseModel):
    data: str = Field(max_length=MAX_INPUT_CHARS)


@router.post("/sessions/{session_id}/input")
def send_input(session_id: str, body: Input, x_shell_device: str | None = Header(default=None)):
    device = require_device(x_shell_device)
    try:
        session = manager.get(session_id)
        mode = session.mode()               # read before writing: the prompt's own mode
        session.write(body.data)
    except ShellError as exc:
        _raise(exc)
    shell_store.audit("input", session_id=session_id, device=device.get("label"),
                      cwd=session.cwd, text=_audit_text(body.data, mode))
    return {"ok": True, "end": session.end}


@router.get("/sessions/{session_id}/output")
async def stream_output(session_id: str, offset: int = 0,
                        x_shell_device: str | None = Header(default=None)):
    """NDJSON: `output` frames (with `next` to resume from), `gap` when asked-for
    output has rotated out, `mode` when the program in front switches between
    line / keys / secret input, `heartbeat` every 15 s of silence, `exit` at the end."""
    require_device(x_shell_device)
    try:
        session = manager.get(session_id)
    except ShellError as exc:
        _raise(exc)

    async def frames():
        pos = max(0, offset)
        shown = (session.mode(), session.foreground())
        yield json.dumps({"type": "hello", **session.info(),
                          "mode": shown[0], "program": shown[1]}) + "\n"
        last = time.monotonic()
        while True:
            now_mode = (session.mode(), session.foreground())
            if now_mode != shown and not session.closed:
                shown = now_mode
                yield json.dumps({"type": "mode", "mode": shown[0], "program": shown[1]}) + "\n"
            changed = session.changed
            gap, data, nxt = session.read(pos)
            if gap is not None:
                yield json.dumps({"type": "gap", "from": gap, "to": nxt - len(data)}) + "\n"
            if data:
                yield json.dumps({"type": "output", "offset": nxt - len(data),
                                  "next": nxt, "data": data.decode("utf-8", "replace")},
                                 ensure_ascii=False) + "\n"
                pos, last = nxt, time.monotonic()
                continue
            pos = nxt
            if session.closed:
                yield json.dumps({"type": "exit", "code": session.exit_code, "next": pos}) + "\n"
                return
            try:
                await asyncio.wait_for(changed.wait(), MODE_POLL_SECONDS)
            except TimeoutError:
                pass
            if time.monotonic() - last >= HEARTBEAT_SECONDS:
                yield json.dumps({"type": "heartbeat", "next": pos}) + "\n"
                last = time.monotonic()

    return StreamingResponse(frames(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})
