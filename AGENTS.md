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
"skills" (run Claude/Codex/Gemini on projects, notes, diary, reminders, system
stats, Mac control, etc.); an LLM `shell` agent routes/chains them and formats
replies for mobile. Clients are a Telegram bot and a native Flutter Android app
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
- `server/main.py` — FastAPI app, lifespan, mounts `api_v2` router + scheduler.
- `server/api_v2.py` — structured `/api/*` JSON backend for the app.
- `server/orchestrator.py`, `server/skills/` — `/run` path; skills self-register
  from a manifest. `shell.py` is the LLM agent loop (routing + tool chaining).
- `server/scheduler.py` — background reminders + (optional) battery alerts.
- `server/notify.py` (Telegram push) / `server/fcm.py` (FCM push to the app).
- `server/db/` — SQLite stores. `server/config.py` — all env config.
- `clients/telegram_bot.py` — Telegram client. `clients/gajala/` — Flutter app.
- `scripts/codechat` — launchd install/start/stop/logs.

## Conventions
- Match surrounding style; concise module docstrings explaining the "why".
- Skills subclass `Skill`; set `expose_to_agent`/`passthrough`/`final_output`
  flags rather than editing the router. New skills need no wiring.
- Secrets never committed: `.env`, `*service-account*.json`, `google-services.json`
  are gitignored. Server auto-generates `~/.codeasachat/api_token`.
- Auth: `/run` and `/api/*` require `X-API-Token`; `/health` is open.
- After changing server code, restart cleanly (bootout + pkill + bootstrap) to
  avoid duplicate-uvicorn races.

## Current State
Working: full skill set, shell agent (with one-retry resilience + partial-result
fallback), Telegram bot, Flutter app (dashboard, chat, notes/reminders/diary/
projects/usage/mac screens, light/dark), FCM push end-to-end (reminders + test
push), Bluetooth connect/disconnect/list. Deployed on a Mac Mini via launchd +
Tailscale. Battery alerts disabled on this always-plugged host.

## Changelog (most recent first)
- 2026-07-01 — FCM push (app+server) end-to-end; Bluetooth device control; shell
  agent retry/partial-result; open-source prep (LICENSE, README, .env.example,
  app moved to clients/gajala, removed old Expo client + internal docs).
- 2026-06-08 01:42 — context initialized

## TODO / Next Steps
- Push from more places (long-task completion pings, note/diary nudges).
- CI for the Flutter app; publish app as needed.
- Rotate any credentials shared during setup.
