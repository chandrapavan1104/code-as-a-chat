# Code-as-a-Chat

Turn your Mac into an always-on, headless personal dev server you control from
your phone — in plain language. Ask it to run Claude/Codex/Gemini on your
projects, take notes, set reminders, check system stats, or control the Mac
itself, all over chat. Local-first and private: your code and data never leave
your machine except to the AI CLIs you already use.

> The agent has a name and a personality (default: **Gajala**, a Telugu
> best-friend voice). Both are configurable — see `AGENT_NAME` / `AGENT_PERSONA`.

## How it works

```
   Phone                        Your Mac (always-on)
┌───────────┐   HTTPS over    ┌────────────────────────────────────────┐
│  Gajala   │   Tailscale     │  FastAPI orchestrator (server/)         │
│  Android  │◄───────────────►│   ├─ /run        shell agent (routing)  │
│    app    │                 │   ├─ /api/*      structured app backend │
└───────────┘                 │   └─ skills/     17 self-registering    │
┌───────────┐                 │                  skills                 │
│ Telegram  │◄───────────────►│  scheduler (reminders, alerts)          │
│    bot    │                 │  SQLite stores in ~/.codeasachat/        │
└───────────┘                 └────────────────────────────────────────┘
                                       │ subprocess
                                       ▼
                          claude / codex / gemini CLIs
```

- **Orchestrator** (`server/`) — FastAPI app. A `shell` skill is an LLM agent
  that plans and chains tool calls across the other skills, then formats replies
  for mobile. Skills self-register from a manifest, so adding one needs no wiring.
- **Auth gateway** — every `/run` and `/api/*` request needs an `X-API-Token`
  header. The token auto-generates to `~/.codeasachat/api_token`.
- **Clients** — a Telegram bot (`clients/telegram_bot.py`) and a native Android
  app (`clients/gajala`, Flutter).
- **Data** — SQLite databases in `~/.codeasachat/` (notes, diary, reminders,
  conversation memory, registered devices). Nothing is sent to a cloud you don't
  control.

## Skills

`claude` · `codex` · `antigravity` (Gemini) · `shell` (agent router) ·
`notes` · `diary` · `reminders` · `projects` · `sessions` · `context` ·
`filemanager` · `sysmon` · `ports` · `usage` · `memory` · `mac` (lock, sleep,
say, notify, screenshot, webcam, Bluetooth connect/disconnect).

## Requirements

- macOS with the AI CLIs you want: `claude`, `codex`, `gemini` (each logged in
  via its own subscription — no API keys needed).
- Python 3.11+.
- Optional: [Tailscale](https://tailscale.com) (to reach the Mac from your phone),
  `blueutil` (Bluetooth control), a Telegram bot, a Firebase project (app push).

## Setup (server)

```bash
git clone <this repo> && cd Code-as-a-chat
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in what you need (all optional to start)
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Run it as an always-on background service (launchd, auto-restart on crash):

```bash
./scripts/codechat install     # start | stop | restart | status | logs | uninstall
```

Grab the API token for clients:

```bash
cat ~/.codeasachat/api_token
```

## Reaching it from your phone

Expose the local server to your devices over your private tailnet:

```bash
tailscale serve --bg 8000     # → https://<your-machine>.<tailnet>.ts.net
```

Point a client at that URL with the API token. (Any HTTPS reverse proxy works;
Tailscale just keeps it private and off the public internet.)

## The Gajala Android app (`clients/gajala`)

A Flutter app: dashboard of system stats + skills, a chat screen (the shell
agent), and rich screens for notes, reminders, diary, projects, usage, and Mac
control. Light/dark themes, secure token storage.

```bash
cd clients/gajala
flutter pub get
flutter build apk --release
```

**Push notifications (optional):** create a Firebase project, add an Android app
with the package id in `android/app/build.gradle.kts`, and drop its
`google-services.json` into `clients/gajala/android/app/`. On the server, put the
Firebase **service-account** key at `~/.codeasachat/fcm-service-account.json`
(or set `FCM_SERVICE_ACCOUNT`). Reminders and alerts then push to the app.
Both credential files are gitignored — never commit them.

## Configuration

See `.env.example` for every option (persona, default engine, model, workspace
directory, Telegram, usage budgets, scheduler, push). Everything has a sane
default; you can start with an empty `.env`.

## License

[MIT](LICENSE) © 2026 Chandra Pavan Reddy
