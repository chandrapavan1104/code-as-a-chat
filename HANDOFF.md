# Code-as-a-Chat — Project Handoff

> **Last updated:** May 19, 2026  
> **Author:** Chandra Pavan  
> **Status:** Pre-implementation (Architecture finalized)

---

## What I'm Building

**Code-as-a-Chat** (also referred to as "Pocket Coding Engine") is a system that turns my Mac (MacBook Air M-series / Mac Mini) into a **headless, always-on, personal development server** that I control entirely from my phone.

The core idea: I open a chat on my phone, type (or speak) a natural language instruction like *"refactor the auth module in my project"* or *"what's my CPU usage right now?"*, and the system executes it on my Mac — writing files, running code, monitoring processes — and streams the results back to my phone in real time.

Think of it as an **intelligent remote computer, accessed through a chat interface**, with multiple AI "brains" (Claude, OpenAI, Gemini) available as interchangeable execution engines.

### The Problem It Solves

- I want to code, debug, and manage projects **without being physically at my Mac**.
- I don't want to SSH into a terminal from my phone — that UX is terrible.
- I want **natural language** to be my interface, not bash commands.
- I want to choose the best AI model for each task (Claude for refactoring, Codex for quick scripts, Gemini for analysis).
- I want all my data to stay **local and private** on my own hardware.

---

## Architecture Overview

The system has two cleanly separated layers:

```
┌──────────────────────────────────────────────────────────────────────┐
│                         MAC (SERVER)                                 │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │               FastAPI Orchestrator (:8000)                     │  │
│  │        Skill Router  →  Skill Registry  →  Skill Executor     │  │
│  └──────┬────────────┬────────────┬───────────┬────────┬────────┘  │
│         │            │            │           │        │            │
│  ┌──────▼──────┐ ┌───▼────┐ ┌────▼─────┐ ┌──▼─────┐ ┌▼─────────┐ │
│  │ Skill 1     │ │Skill 2 │ │ Skill 3  │ │Skill 4 │ │ Skill 5  │ │
│  │ Claude Code │ │ Codex  │ │Antigrav. │ │ SysMon │ │ FileMgr  │ │
│  └─────────────┘ └────────┘ └──────────┘ └────────┘ └──────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Middleware: PII/PCI Masking (Presidio) │ Auth │ Rate Limit   │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
               ┌───────────▼───────────┐
               │    MOBILE CLIENT      │
               │  V1: Telegram Bot     │
               │  V2: Android APK      │
               └───────────────────────┘
```

**Key architectural decision:** The server (FastAPI orchestrator + skills) and the client (Telegram / Android) are completely decoupled. The server exposes REST + WebSocket APIs. Any client can connect. Swapping Telegram for a custom Android app requires **zero server changes**.

---

## V1 Plan — Telegram MVP

### Goal
Get a working end-to-end system as fast as possible using Telegram as the mobile interface.

### Stack
| Component | Technology |
|---|---|
| **Server** | Python 3.12+ / FastAPI / Uvicorn |
| **Client** | Telegram Bot via `python-telegram-bot` (long polling) |
| **Skill 1** | Claude Code CLI (`claude -p "..." --bare --dangerously-skip-permissions --output-format stream-json`) |
| **Skill 2** | OpenAI Codex via [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) (`interpreter.chat()` with `auto_run=True`) |
| **Skill 3** | Gemini / Antigravity CLI (`gemini -p "..." --non-interactive --yolo --output-format stream-json`) |
| **Skill 4** | System Monitor via `psutil` (CPU, RAM, disk, thermals, top processes) |
| **Skill 5** | File Manager via Python `pathlib` / `os` (list, read, write, delete) |
| **Persistence** | `pm2` or macOS `launchd` to keep services alive 24/7 |
| **Networking** | Telegram long polling (no inbound ports needed). Tailscale for SSH access if debugging is required. |

### How Skill Routing Works (V1)
Explicit Telegram commands map to skills:
- `/claude <prompt>` → Claude Code CLI
- `/codex <prompt>` → Open Interpreter / OpenAI
- `/gemini <prompt>` → Gemini CLI
- `/status` → System Monitor
- `/files <path>` → File Manager
- Free-text messages → routed to a configurable default skill

### What V1 Does NOT Include
- Custom mobile app (uses Telegram instead)
- Voice/audio input
- PII/PCI masking (stubbed, not active)
- MCP server integrations
- Local LLM inference (uses cloud APIs only)

---

## V2 Plan — Custom Android App + Full Middleware

### Goal
Replace Telegram with a purpose-built Android APK and activate the full middleware stack.

### What Changes from V1
| Area | V1 | V2 |
|---|---|---|
| **Client** | Telegram Bot | Custom Android APK (React Native / Expo) |
| **Connection** | Telegram long polling | WebSocket over Tailscale VPN (persistent, bidirectional) |
| **PII/PCI** | Stubbed | Active — [Microsoft Presidio](https://github.com/microsoft/presidio) intercepts all messages |
| **Local LLMs** | None | Apple MLX framework for on-device inference |
| **Routing** | Explicit `/commands` | Smart auto-routing (lightweight classifier picks the best model) |
| **Voice** | None | Audio input via `ffmpeg` + local Whisper or OpenAI Audio API |
| **MCP Tools** | None | GitHub, SQLite, browser automation, calendar, etc. |

### Android App Features
- Chat-based UI (similar to ChatGPT/Telegram but purpose-built)
- Skill command palette (swipe up to pick a skill)
- Real-time streaming responses via WebSocket
- File preview (view code files inline)
- Approval gates (for destructive operations, the app shows a confirmation dialog)
- System health dashboard (live CPU/RAM graphs)

### Networking (V2)
- [Tailscale](https://tailscale.com/) VPN mesh connects phone to Mac over WireGuard.
- MagicDNS gives the Mac a stable hostname (e.g., `mac-mini.ts.net`).
- `tailscale serve 8000` proxies traffic to the FastAPI server with TLS.
- Works over cellular data — no need to be on the same Wi-Fi.

---

## Future Capabilities (V3+)

These are capabilities I want to extend over time. The skill-based architecture makes all of these additive — no core refactoring needed.

### More Skills
- **Browser Automation** — Use Playwright/Puppeteer via MCP to scrape, test, or interact with web pages from chat.
- **Database Access** — Query SQLite/Postgres databases directly. Ask "how many users signed up today?" and get an answer.
- **Git Operations** — Commit, push, pull, create branches, review diffs — all from chat.
- **Calendar & Email** — Read upcoming meetings, draft emails, manage reminders.
- **Docker Management** — Start/stop containers, view logs, deploy services.
- **CI/CD Triggers** — Kick off GitHub Actions or deploy to Cloud Run from chat.

### PII/PCI Masking (Privacy Layer)
Using [Microsoft Presidio](https://github.com/microsoft/presidio):
- All messages are scanned before being sent to any LLM.
- Credit card numbers, SSNs, emails, phone numbers, API keys → automatically redacted.
- Supports custom recognizers (e.g., internal employee IDs, project codes).
- Can be toggled per-skill (e.g., always mask for cloud APIs, skip for local LLMs).

### Personalization
- **User Profile Store** — SQLite DB with preferences: default model, preferred language, coding style, timezone.
- **Custom System Prompts** — Each skill's system prompt is dynamically assembled from user preferences. Example: *"Always use TypeScript. Prefer functional programming. Use pnpm, not npm."*
- **Conversation Memory** — Persistent conversation history so the AI remembers past interactions and project context across sessions.
- **CLAUDE.md / .gemini integration** — Project-specific context files that tell each AI about the codebase structure, dependencies, and conventions.

### Scalability
- **Skill auto-discovery** — Drop a new `.py` file in `/skills/`, it auto-registers on server restart.
- **MCP Server Ecosystem** — The orchestrator can spawn and manage MCP servers as child processes. Any MCP-compatible tool (there are 1000+ in the ecosystem) becomes a skill.
- **Multi-project support** — Switch between projects from chat (`/project myapp`). Each project has its own working directory and context.
- **Multi-user support** — Allowlist-based auth. Multiple trusted users can share the same Mac server (e.g., pair programming).

---

## Design Decisions & Rationale

| Decision | Why |
|---|---|
| **FastAPI as orchestrator** | Async-native, supports both REST and WebSocket, excellent for streaming LLM responses. Lightweight enough to run on a Mac 24/7. |
| **Skill-based plugin architecture** | Each AI engine and utility is isolated. Adding, removing, or swapping skills doesn't touch core code. Inspired by [Goose](https://github.com/block/goose)'s recipe/skill system. |
| **CLI subprocess for Claude & Gemini** | Both have excellent headless CLI modes with streaming JSON output. Using the CLI avoids maintaining SDK version dependencies and gets full agentic capabilities (file access, bash execution) for free. |
| **Open Interpreter for OpenAI** | OpenAI's API is text-only — it can't touch the filesystem. Open Interpreter bridges this gap by giving GPT-4o the ability to read/write/execute files. |
| **Telegram for V1** | Zero client-side development. Handles text, files, images, voice natively. Long polling means no inbound port exposure. Perfect for an MVP. |
| **Tailscale for networking** | Zero-trust, WireGuard-encrypted, works across NATs without port forwarding. Automatic reconnection. MagicDNS gives stable hostnames. Free for personal use. |
| **`--dangerously-skip-permissions` for Claude** | Required for headless/autonomous operation. The orchestrator + Telegram allowlist provide the security layer instead. |
| **`--yolo` for Gemini** | Same rationale — auto-approves tool use for autonomous operation. |
| **Presidio for PII masking** | Microsoft's open-source solution. Mature, extensible, supports custom recognizers, and can run as a Python library (no external service needed). |

---

## Open-Source References

These projects inspired the architecture and can be referenced for implementation patterns:

| Project | What It Does | How We Reference It |
|---|---|---|
| [linuz90/claude-telegram-bot](https://github.com/linuz90/claude-telegram-bot) | Telegram bridge for Claude Code CLI | Inspiration for the Claude skill's CLI spawning pattern and Telegram streaming UX |
| [benedict2310/telecodex](https://github.com/benedict2310/telecodex) | Telegram bridge for OpenAI Codex | Inspiration for launch profiles and mobile supervision patterns |
| [OpenInterpreter/open-interpreter](https://github.com/OpenInterpreter/open-interpreter) | Gives LLMs filesystem execution | Direct dependency — used as the Codex/OpenAI execution engine |
| [microsoft/presidio](https://github.com/microsoft/presidio) | PII/PCI detection & anonymization | Direct dependency (V2) — middleware layer |
| [block/goose](https://github.com/block/goose) | Extensible AI agent with MCP | Reference architecture for the skill/recipe plugin system |

---

## Project Structure

```
Code-as-a-chat/
├── server/
│   ├── main.py                # FastAPI entry point
│   ├── config.py              # Env vars, paths, constants
│   ├── orchestrator.py        # Skill router + intent classifier
│   ├── skills/
│   │   ├── __init__.py        # Skill registry (auto-discovery)
│   │   ├── base.py            # Abstract base class for all skills
│   │   ├── claude_code.py     # Skill 1: Claude Code CLI
│   │   ├── codex.py           # Skill 2: OpenAI via Open Interpreter
│   │   ├── antigravity.py     # Skill 3: Gemini CLI
│   │   ├── sysmon.py          # Skill 4: System monitoring
│   │   └── filemanager.py     # Skill 5: File operations
│   ├── middleware/
│   │   └── pii_mask.py        # Presidio PII/PCI masking
│   └── db/
│       └── store.py           # SQLite for preferences & history
├── clients/
│   ├── telegram_bot.py        # V1 Telegram client
│   └── android/               # V2 React Native / Expo (future)
├── .env.example
├── requirements.txt
├── HANDOFF.md                 # ← This file
└── README.md
```

---

## Required API Keys

| Key | Service | Required For |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic | Skill 1 (Claude Code CLI) |
| `OPENAI_API_KEY` | OpenAI | Skill 2 (Codex via Open Interpreter) |
| `GOOGLE_API_KEY` | Google AI | Skill 3 (Gemini / Antigravity CLI) |
| `TELEGRAM_BOT_TOKEN` | Telegram (via @BotFather) | V1 Client |
| `TELEGRAM_ALLOWED_USERS` | — | Comma-separated Telegram user IDs for auth |
