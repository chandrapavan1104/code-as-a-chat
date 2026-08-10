import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USERS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",")
    if uid.strip().isdigit()
]

DEFAULT_SKILL: str = os.getenv("DEFAULT_SKILL", "claude")

# The "home base" — the default workspace for general, non-project work, and the
# baseline you're in whenever you haven't deliberately switched into a project.
# General/non-project asks live here so they never pollute a real project's
# session; project work happens after you switch (or confirm a switch). Override
# with HOME_BASE_DIR; defaults to ~/Projects/general.
HOME_BASE_DIR: Path = Path(
    os.getenv("HOME_BASE_DIR", str(Path.home() / "Projects" / "general"))
).expanduser()


def ensure_home_base() -> Path:
    """Create the home base + a starter CLAUDE.md if missing, so there's always a
    safe default workspace to land in. Never overwrites an existing CLAUDE.md."""
    try:
        HOME_BASE_DIR.mkdir(parents=True, exist_ok=True)
        claude_md = HOME_BASE_DIR / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                "# General — home base\n\n"
                "Default workspace for anything that isn't a specific project: "
                "general questions, brainstorming, quick asks, and standing "
                "preferences. Notes/reminders/diary live in the central store "
                "(`~/.codeasachat/`), not here.\n\n"
                "## About me / standing preferences\n"
                "<!-- Durable, cross-project preferences load into every general chat. -->\n"
            )
    except OSError:
        pass
    return HOME_BASE_DIR


_workspace_env = os.getenv("WORKSPACE_DIR", "")
# Default to the home base (created on first use) rather than the bare home dir,
# so non-project asks land somewhere dedicated instead of ~/.
WORKSPACE_DIR: Path = (
    Path(_workspace_env).expanduser() if _workspace_env else ensure_home_base()
)

ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")


def _load_or_create_api_token() -> str:
    """Shared secret guarding the /run and /skills endpoints.
    Priority: API_TOKEN env → persisted token file → freshly generated.
    Server and bot both read this so they agree on the token automatically."""
    env = os.getenv("API_TOKEN", "").strip()
    if env:
        return env
    token_file = Path.home() / ".codeasachat" / "api_token"
    if token_file.exists():
        existing = token_file.read_text().strip()
        if existing:
            return existing
    import secrets
    token = secrets.token_urlsafe(32)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token)
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    return token


API_TOKEN: str = _load_or_create_api_token()

# LLM that powers the shell skill (routing + mobile reformatting).
# 'haiku' is fast and cheap; override to 'sonnet' / 'opus' / full model name if you want.
SHELL_MODEL: str = os.getenv("SHELL_MODEL", "haiku")

# Backup brain for when Claude usage runs out. The shell/notes/diary/reminders
# LLM calls go through Claude Haiku by default; if that call fails (quota, rate
# limit, auth, CLI missing), they fall back to the OpenAI API — provided
# OPENAI_API_KEY is set.
#   SHELL_LLM_PROVIDER = auto   → Claude first, OpenAI on failure (default)
#                        openai → skip Claude entirely (conserve Claude usage)
#                        claude → Claude only, no fallback (original behavior)
SHELL_LLM_PROVIDER: str = os.getenv("SHELL_LLM_PROVIDER", "auto")
# OpenAI model used for that fallback. gpt-4o-mini is cheap, fast, and plenty
# for routing + short mobile replies; override to taste.
OPENAI_SHELL_MODEL: str = os.getenv("OPENAI_SHELL_MODEL", "gpt-4o-mini")

# Model the codex skill passes to `codex exec --model`.
CODEX_MODEL: str = os.getenv("CODEX_MODEL", "gpt-5")

# Before resuming, reconcile our stored session id with the CLI's own newest
# session for that folder (see server/db/native_sessions.py). Keeps ONE session
# per (project, engine) even when you use the CLI directly on the Mac. Set to 0
# to trust only the server's pointer (old behaviour).
SESSION_FOLLOW_NATIVE: bool = os.getenv("SESSION_FOLLOW_NATIVE", "1") not in ("0", "false", "False")

# ── local Qwen (Ollama) ───────────────────────────────────────────────────────
# A local model the shell brain (and notes/diary/reminders) can run on — free,
# offline, and the default for Gajala so it stops spending Claude on routine
# routing/formatting. Needs Ollama running with the model pulled.
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
# 3B (~1.9GB) is the brain default: routing/notes/formatting don't need 7B, and
# the smaller footprint keeps this 16GB host off swap. 7B stays available as the
# `qwen` chat engine. keep_alive pins it in memory so there's no reload churn.
QWEN_MODEL: str = os.getenv("QWEN_MODEL", "qwen2.5:3b")
QWEN_KEEP_ALIVE: str = os.getenv("QWEN_KEEP_ALIVE", "30m")
# Tasks that run on Qwen as PRIMARY (local first, Claude/OpenAI as fallback).
# Every other task uses Qwen only as the LAST fallback. Comma list of:
# shell, notes, diary, reminders. Default keeps reminders on Claude (time math).
QWEN_TASKS: str = os.getenv("QWEN_TASKS", "shell,notes,diary")

# ── self-healing / fix agent ──────────────────────────────────────────────────
# This repo (the app the fix agent edits). Derived from this file's location so
# it works wherever the project lives; override with REPO_DIR if needed.
REPO_DIR: Path = Path(os.getenv("REPO_DIR", str(Path(__file__).resolve().parent.parent)))
# The buildable Flutter copy (has the gitignored google-services.json / signing).
# The build skill syncs the repo's tracked app sources here before building.
FLUTTER_APP_DIR: Path = Path(os.getenv("FLUTTER_APP_DIR", str(Path.home() / "dev" / "gajala")))
FLUTTER_BIN_DIR: str = os.getenv("FLUTTER_BIN_DIR", str(Path.home() / "dev" / "flutter" / "bin"))
# Where the built APK is served from, and the URL to hand back to the phone.
APK_DEST: Path = Path(os.getenv("APK_DEST", str(Path.home() / "apk-share" / "gajala.apk")))
APK_URL: str = os.getenv("APK_URL", "http://100.93.9.34:8765/gajala.apk")
# Engine the fix agent uses for the actual code changes (codex conserves Claude).
FIX_ENGINE: str = os.getenv("FIX_ENGINE", "codex")
# Hard wall-clock ceiling for one fix attempt (seconds) — a loop/hang guard.
FIX_TIMEOUT: int = int(os.getenv("FIX_TIMEOUT", "600"))

# How many recent turn-pairs the shell pulls from memory into Haiku's context.
# Higher = better continuity, more tokens per call.
# Bumped from 5 to 12 after losing project-priority context across long chats.
MEMORY_TURNS: int = int(os.getenv("MEMORY_TURNS", "12"))

# Daily token budgets used by the /usage skill to compute a "% used today"
# for providers that don't expose a real rate-limit snapshot locally.
# Defaults are rough heuristics — tune to your actual subscription tier.
#   Claude Pro: ~5M billable tokens/day is a heavy day
#   Gemini free: extremely generous; 10M is a reasonable headroom marker
USAGE_BUDGET_CLAUDE: int = int(os.getenv("USAGE_BUDGET_CLAUDE", "5000000"))
USAGE_BUDGET_GEMINI: int = int(os.getenv("USAGE_BUDGET_GEMINI", "10000000"))

# After each claude/codex/gemini run, converge the shared context files
# (AGENTS.md / CLAUDE.md / GEMINI.md) so whichever agent updated its file
# propagates to the others. Disable with CONTEXT_AUTO_SYNC=false.
CONTEXT_AUTO_SYNC: bool = os.getenv("CONTEXT_AUTO_SYNC", "true").lower() in ("1", "true", "yes")

# When switching into a project that has no context files yet, auto-create the
# template (AGENTS.md + mirrors) so every workspace is context-ready.
# Disable with CONTEXT_AUTO_INIT=false.
CONTEXT_AUTO_INIT: bool = os.getenv("CONTEXT_AUTO_INIT", "true").lower() in ("1", "true", "yes")

# ── Agent persona ─────────────────────────────────────────────────────────────
# Name + personality of the shell agent. Persona affects only the user-facing
# reply text — tool routing stays precise English under the hood.
AGENT_NAME: str = os.getenv("AGENT_NAME", "Gajala")
# Set AGENT_PERSONA=professional to switch back to a plain assistant voice.
AGENT_PERSONA: str = os.getenv("AGENT_PERSONA", "telugu-bestie")

# Model for the diary/Anna mentor skill. Mentor judgment is worth a better
# model than the fast shell router; entries are low-frequency.
DIARY_MODEL: str = os.getenv("DIARY_MODEL", "sonnet")

# ── Background scheduler (reminders + battery alerts) ─────────────────────────
SCHEDULER_INTERVAL: int = int(os.getenv("SCHEDULER_INTERVAL", "60"))   # seconds
# Battery alerts only make sense on a laptop; disable on an always-plugged
# desktop (e.g. Mac Mini) with BATTERY_ALERTS=false. Machines with no battery
# are skipped automatically regardless.
BATTERY_ALERTS: bool = os.getenv("BATTERY_ALERTS", "true").lower() in ("1", "true", "yes")
BATTERY_THRESHOLD: int = int(os.getenv("BATTERY_THRESHOLD", "20"))     # percent
BATTERY_ALERT_COOLDOWN: int = int(os.getenv("BATTERY_ALERT_COOLDOWN", "1800"))  # seconds

# ── Night Shift (overnight autonomous build queue) ────────────────────────────
# Opt-in: while enabled and inside the night window, the runner keeps up to one
# job per engine in flight so all three coding subscriptions build in parallel on
# isolated `night/*` branches; `auto` jobs use the verified deploy coordinator.
NIGHT_SHIFT_ENABLED: bool = os.getenv("NIGHT_SHIFT_ENABLED", "false").lower() in ("1", "true", "yes")
NIGHT_START: str = os.getenv("NIGHT_START", "23:00")   # HH:MM local
NIGHT_END: str = os.getenv("NIGHT_END", "07:00")       # HH:MM local (may wrap midnight)
NIGHT_ENGINES: str = os.getenv("NIGHT_ENGINES", "claude,codex,gemini")
# Bench an engine for this cycle once it's at/over this % of its rate-limit
# window (Codex 5h/weekly %, Claude/Gemini computed vs their daily budget). It
# re-enters automatically as the window resets — that's the "keep filling" logic.
NIGHT_QUOTA_STOP_PCT: int = int(os.getenv("NIGHT_QUOTA_STOP_PCT", "85"))
NIGHT_JOB_TIMEOUT: int = int(os.getenv("NIGHT_JOB_TIMEOUT", "1800"))  # 30 min/job
NIGHT_MAX_JOBS: int = int(os.getenv("NIGHT_MAX_JOBS", "12"))          # per-night ceiling
NIGHT_TOKEN_BUDGET: int = int(os.getenv("NIGHT_TOKEN_BUDGET", "0"))   # 0 = unlimited
NIGHT_TICK: int = int(os.getenv("NIGHT_TICK", "120"))                 # dispatch cadence (s)

# ── FCM push (Gajala Android app) ─────────────────────────────────────────────
# Service-account key downloaded from Firebase console; lets the server send
# pushes to registered devices via the FCM HTTP v1 API. If the file is absent,
# push is silently skipped (Telegram still works).
FCM_SERVICE_ACCOUNT: Path = Path(
    os.getenv("FCM_SERVICE_ACCOUNT", str(Path.home() / ".codeasachat" / "fcm-service-account.json"))
).expanduser()


# Apply any persisted workspace override (from /projects switch …).
# Done inside a function so the import order stays clean.
def _apply_persisted_state() -> None:
    import json as _json
    state_file = Path.home() / ".codeasachat" / "state.json"
    if not state_file.exists():
        return
    try:
        state = _json.loads(state_file.read_text())
    except (OSError, _json.JSONDecodeError):
        return
    saved = state.get("workspace_dir")
    if not saved:
        return
    p = Path(saved).expanduser()
    if p.exists() and p.is_dir():
        global WORKSPACE_DIR
        WORKSPACE_DIR = p


_apply_persisted_state()
