# Gajala Terminal (remote shell)

Library → Terminal runs live zsh sessions on the Mac from the phone.

## How it works

- `server/remote_shell.py` — one zsh per session in a pseudo-terminal
  (`+Z`: no line editor, `TERM=dumb`, login PATH, minimal environment without
  the server's `.env` secrets). A fresh single-threaded interpreter claims the
  pty as the controlling terminal and execs zsh, so Ctrl-C and job control work
  without forking the multi-threaded server. `cd`, variables and prompts persist.
- Output lives in a 256 KB in-memory ring buffer per session, addressed by byte
  offset. Nothing is written to disk.
- `server/shell_api.py` (`/api/shell`): `status`, `pair` (POST/DELETE),
  `sessions` (GET/POST/DELETE), `sessions/{id}/input`, and
  `sessions/{id}/output?offset=N` — NDJSON `hello` / `output` (with `next`) /
  `gap` / `heartbeat` (15 s) / `exit`. The phone reconnects from its last offset.
- Limits: 3 live sessions, 30 min idle, 12 h lifetime; closing hangs up the whole
  process group; server shutdown kills every shell.

## Security

1. Same `X-API-Token` as every `/api` route; tailnet-only (Tailscale Serve).
2. Paired device: the phone's random key (secure storage) in `X-Shell-Device`;
   the Mac keeps only its SHA-256 and accepts exactly one device. The first phone
   to pair is accepted (trust on first use) and a push + Telegram alert is sent.
   Other clients — including Telegram, widgets, the chat agent and anyone with
   only the token — get 403. It is an API, not a skill, so the agent cannot call it.
3. `REMOTE_SHELL_ENABLED=0` in `.env` turns it off.
4. Audit log (`~/.codeasachat/shell.db`): pair/start/input/close with time,
   session and directory. Output is never stored. Input sent while the terminal
   has echo off (password prompts) is recorded as `[hidden input]`.
5. The phone asks for fingerprint / screen lock every time Terminal opens.

On the Mac: `scripts/remote-shell status | unbind | audit [N]`.

## Limits

Full-screen programs (vim, top, less) do not render; colours are stripped.
Residual risk: before the first pairing, a holder of the API token could pair
first; the push alert reveals it and `scripts/remote-shell unbind` removes it.
