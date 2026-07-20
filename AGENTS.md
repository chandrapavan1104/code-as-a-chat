# Project Context — Code-as-a-chat

> UNIVERSAL CONTEXT for all AI agents (Claude, Codex, Gemini).
> This file is mirrored to AGENTS.md, CLAUDE.md, and GEMINI.md — keep it as the
> single source of truth for what this project is and where it stands.
>
> AGENTS: when you make a meaningful change to this project, UPDATE the
> "Current State" and "Changelog" sections below before you finish.

## Overview
Code-as-a-Chat turns an always-on Mac into a headless personal dev server you
control from your phone in natural language. A FastAPI orchestrator exposes
"skills" (run Claude/Codex/Gemini CLIs on projects, notes, diary, reminders,
system stats, ports, sessions, Mac control, etc.); an LLM `shell` agent
routes/chains them and formats replies for mobile, streaming live progress
for long turns. Clients are a Telegram bot and a native Flutter Android app
("Gajala"). Local-first: data stays in `~/.codeasachat/`; only the AI CLIs the
user already uses talk to the outside.

## Tech Stack
- **Server:** Python 3.11+, FastAPI, Uvicorn, Pydantic, httpx, psutil,
  python-telegram-bot, python-dotenv. Push: google-auth (FCM HTTP v1).
- **Storage:** SQLite files in `~/.codeasachat/` (notes, diary, reminders,
  conversation memory, devices) + `state.json`, `api_token`.
- **App:** Flutter (Riverpod, dio, flutter_secure_storage, google_fonts,
  firebase_messaging, flutter_local_notifications). Android + web targets.
- **Ops:** macOS launchd (`scripts/codechat`), Tailscale Serve for phone access.

## Architecture / Key Files
- `server/main.py` — FastAPI app, lifespan, mounts `api_v2` router + scheduler;
  `/run` (single-response) and `/run/stream` (NDJSON step-by-step) entrypoints.
- `server/api_v2.py` — structured `/api/*` JSON backend for the app.
- `server/orchestrator.py`, `server/skills/` — routing path; skills self-register
  from a manifest (`claude_code.py`, `codex.py`, `antigravity.py` wrap the three
  CLIs via `cli_base.py`). `shell.py` is the LLM agent loop (routing + tool
  chaining), with an optional `on_event` callback for live progress.
- `server/scheduler.py` — background reminders + (optional) battery alerts.
- `server/notify.py` (Telegram push) / `server/fcm.py` (FCM push to the app).
- `server/db/` — SQLite stores. `server/config.py` — all env config.
- `clients/telegram_bot.py` — Telegram client. `clients/gajala/` — Flutter app
  (dashboard, chat, notes/diary/reminders/projects/usage/mac/system screens).
- `scripts/codechat` — launchd install/start/stop/logs.

## Conventions
- Match surrounding style; concise module docstrings explaining the "why".
- Skills subclass `Skill`; set `expose_to_agent`/`passthrough`/`final_output`
  flags rather than editing the router. New skills need no wiring.
- Secrets never committed: `.env`, `*service-account*.json`, `google-services.json`
  are gitignored. Server auto-generates `~/.codeasachat/api_token`.
- Auth: `/run`, `/run/stream`, and `/api/*` require `X-API-Token`; `/health` is open.
- After changing server code, restart cleanly (bootout + pkill + bootstrap) to
  avoid duplicate-uvicorn races.

## Current State
Working: full skill set (CLI runners, notes, diary, reminders, projects, ports,
sessions browser, filemanager, sysmon, usage, Mac control incl. Bluetooth +
wake/unlock). Shell agent with one-retry resilience + partial-result fallback,
plus an **OpenAI backup brain** (`SHELL_LLM_PROVIDER` auto|claude|openai) so it
keeps working when Claude usage runs out. Live-progress streaming over
`/run/stream`. **Session reuse** per (workspace, engine) for claude/codex/gemini
with cross-model `.md` sync; `general` home base is the default workspace;
**per-directory app conversations** whose thread follows the active project
everywhere (incl. agent-driven switches) with confirm-to-move; pinned coding
engine **+ per-engine model** (claude opus/sonnet/haiku, codex gpt-5.6-sol/…,
gemini 2.5-pro/flash), switchable from the app or in chat ("switch to opus").
**Images** send + receive (screenshots to the agent, images back).
Telegram bot; Flutter app "Gajala" (light/dark) with FCM push end-to-end and
**15-second heartbeats on long `/run/stream` turns** so silent CLI work does not
lose the phone's HTTPS stream through an idle connection; disconnected streams
also leave the server-side turn running so its reply is persisted and pushed;
**Android home-screen widgets** (Lock / Wake / Ask / Brain-dump + a Codaur usage
glance). **Codaur usage refreshes every 30 seconds while visible**, bypasses
caches, drops expired quota snapshots instead of presenting stale limits, and
avoids overlapping CLI requests when the screen first opens.
Deployed on a Mac Mini via launchd + Tailscale. Battery alerts disabled on this
always-plugged host. **Self-healing `fix` agent** (codex-powered): describe a
bug from the phone → it diagnoses + makes a minimal fix on a branch, auto-builds
app-side fixes (`build` skill → APK link) and gates server changes for your OK;
`/fix ship` merges, and a detached `restart_guard` health-checks + auto-rolls-back
so a bad server change can't lock you out.

## Changelog (most recent first)
- 2026-07-19 — In-app updater: the build skill stamps a fresh versionCode via
  `--build-number=<epoch>` (no pubspec churn) and writes `apk_version.json`;
  `GET /api/appversion` serves it; the app compares its own buildNumber and shows
  an "Update available" banner on the dashboard that downloads the APK and
  launches the system installer (open_filex, REQUEST_INSTALL_PACKAGES). Closes
  the self-heal loop on the phone — no more copy-paste APK link.
- 2026-07-19 — Hardened `/run/stream` for long/backgrounded turns: 15-second
  NDJSON heartbeats prevent idle drops, and a phone disconnect no longer cancels
  the server-side agent before it can persist and push the completed reply.
- 2026-07-18 — Fixed an intermittent Codaur 502 on screen open: the provider's
  initial load and a redundant post-frame refresh were launching overlapping
  Codaur CLI requests. The normal initial load now runs once; manual, resume,
  and 30-second refreshes are unchanged.
- 2026-07-17 — Per-engine BACKUP model: each engine (claude/codex/gemini) can
  have a fallback model; if a coding run fails on the primary, `cli_base` retries
  once on the backup with a fresh session (codex `_failed` also catches a
  turn.failed that exits 0 — e.g. model version skew). Stored in prefs
  (`coding_backup_models`), set via `/api/model` (`backup` field), the `model`
  skill ("codex backup gpt-5.5"), or the app's context sheet. Defaults set:
  claude opus→sonnet, codex gpt-5.6-sol→gpt-5.5, gemini→2.5-flash.
- 2026-07-17 — Self-healing fix agent (MVP): `fix` skill runs codex on this repo
  to diagnose + minimally fix reported bugs, gated by a git diff — app-only
  changes auto-build via the new `build` skill (rebuild + deploy APK), server
  changes are quarantined on a branch until `/fix ship`. `restart_guard` runs
  detached so server restarts health-check + auto-roll-back (no lockout).
  Guardrails: one bounded codex run (FIX_TIMEOUT), triage prompt that stops +
  asks on non-code / out-of-scope issues.
- 2026-07-16 — Per-engine model switching: pick the model within each engine
  (claude opus/sonnet/haiku, codex gpt-5.6-sol/terra/…, gemini 2.5-pro/flash).
  `prefs` stores a model per engine (codex presets read live from its model
  cache); CLI wrappers pass `--model`; `/api/model` GET/POST carries models +
  presets; new `model` skill lets the shell agent actually switch from chat
  ("switch to opus") instead of hallucinating it; app context sheet gains a
  per-engine model picker.
- 2026-07-15 — Fixed stale Codaur usage end-to-end: repaired Claude's status-line
  capture path after the Desktop → Projects migration, added foreground polling
  every 30 seconds, cache-busting/no-store responses, expired-window filtering,
  and a generic limit fallback for the Android usage widget.
- 2026-07-13 — Android home-screen widgets via `home_widget` (Gajala quick
  actions: Lock / Wake / Ask / Brain-dump; Codaur usage glance) + remote mac
  **wake/unlock** action (`caffeinate -u`). Honest limit: macOS blocks typing a
  password into a truly-locked screen, so "unlock" = wake (real unlock only when
  no password is required after sleep / within the grace window).
- 2026-07-13 — Backup brain for the shell agent: `_haiku` (used by
  shell/notes/diary/reminders) falls back to the OpenAI API when Claude fails,
  via `SHELL_LLM_PROVIDER` (auto | claude | openai) + `OPENAI_SHELL_MODEL`.
  Keeps the assistant working when Claude usage runs out.
- 2026-07-09 — App chat follows the active project everywhere: server reports
  `workspace` after each turn so the header + thread key stay synced even when an
  agent switches projects mid-turn; skill tabs now persist + get per-engine
  threads; reliable scroll-to-latest. Codaur/usage screen auto-refreshes on open
  + app-resume.
- 2026-07-05 — Per-directory conversations + confirm-to-move; `general` home base
  as the default workspace; shared-context (`.md`) pre-run sync guard.
- 2026-07-04 — Session reuse per (workspace, engine) for all three engines
  (claude/codex/gemini) with cross-model `.md` sync; chat `dir · model` header +
  switcher; pinned coding engine. Image send + receive (upload → agent, images
  back). Repo story-log under `docs/story/`.
- 2026-07-04 — Live progress for long agent turns: `/run/stream` NDJSON
  endpoint, `shell.py` on_event callback, app renders a live status bubble
  instead of a silent wait; chat-reply completion pushes to Gajala as a
  fallback if the stream drops.
- 2026-07-01 — FCM push (app+server) end-to-end; Bluetooth device control; shell
  agent retry/partial-result; open-source prep (LICENSE, README, .env.example,
  app moved to clients/gajala, removed old Expo client + internal docs).
- 2026-06-08 01:42 — context initialized

## TODO / Next Steps
- Push from more places (note/diary nudges).
- CI for the Flutter app; publish app as needed.
- Rotate any credentials shared during setup.
- At-rest security: FileVault is OFF and `.env` is world-readable — consider
  enabling FileVault (mind the headless unlock caveat) and `chmod 600 .env` +
  the SQLite stores.
- Optional widget follow-ups: Sleep tile, Wake-on-LAN (wake from full sleep),
  light-theme widget styling.
