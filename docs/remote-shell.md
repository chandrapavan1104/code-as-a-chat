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

## Interactive programs (raw-key mode)

The Mac reads the terminal's own mode and streams `mode` frames
(`hello` includes the current one), with the program in front:

- `line` — canonical + echo (commands, `read`): the text box, send adds Enter.
- `keys` — non-canonical / raw (gh/npm/inquirer menus, y/n keys, fzf, REPLs):
  the phone shows a key pad (arrows, Enter as `\r`, Space, Backspace, Tab, Esc,
  a Ctrl latch, Ctrl-C) and a field that sends each key as typed. Keys typed
  within 30 ms go out together; inputs are strictly ordered. Arrows follow the
  program's application-cursor mode (`ESC O x` after `ESC[?1h`).
- `secret` — canonical, echo off (password prompts): a masked field, never kept
  in history or on screen.

A manual **Keys** toggle covers programs the detection misreads; it resets when
the shell prompt returns. Pagers are disabled (`PAGER`, `GIT_PAGER`,
`MANPAGER=cat`) and `GIT_EDITOR=true`, so `git commit` without `-m` aborts with
"empty commit message" and `git rebase -i` keeps the todo list as is.

## Security

1. Same `X-API-Token` as every `/api` route; tailnet-only (Tailscale Serve).
2. Paired device: the phone's random key (secure storage) in `X-Shell-Device`;
   the Mac keeps only its SHA-256 and accepts exactly one device. The first phone
   to pair is accepted (trust on first use) and a push + Telegram alert is sent.
   Other clients — including Telegram, widgets, the chat agent and anyone with
   only the token — get 403. It is an API, not a skill, so the agent cannot call it.
3. `REMOTE_SHELL_ENABLED=0` in `.env` turns it off.
4. Audit log (`~/.codeasachat/shell.db`): pair/start/input/close with time,
   session and directory. Output is never stored. Input is logged by the mode it
   arrived in: `line` verbatim, `secret` as `[hidden input]`, `keys` as key
   names with typed text only counted (`↓ ↓ ⏎`, `[5 chars]`) — some raw-mode
   password fields draw their own `***`, so their content is never kept.
5. The phone asks for fingerprint / screen lock every time Terminal opens.

On the Mac: `scripts/remote-shell status | unbind | audit [N]`.

## Limits

Full-screen programs (vim, top, less) still do not render (Phase 2: a server-side
pyte screen); colours are stripped.
Residual risk: before the first pairing, a holder of the API token could pair
first; the push alert reveals it and `scripts/remote-shell unbind` removes it.
